"""
Answer-key harness: score the facts layer and any model against positions
whose correct explanation we know.

Each key (answer_keys/*.toml) gives a position, optionally the move to
explain, the facts a correct explanation must contain, and the false claims
models have actually made. Three scores come out:

  facts recall   did the FACTS we give the model contain each required fact?
                 (no model involved; needs Stockfish for move keys)
  text recall    did the model's text state each required fact?
  false claims   'must not' patterns matched, plus piece-on-square and
                 notation errors from the output check

Text matching uses regular expressions, so recall is approximate: a model
can say the right thing in words no pattern covers. False-claim patterns
are narrower and are the number to watch.

Modes for model runs:
  pipeline  our move annotation (the prompt the review uses, one retry)
  raw       the model alone: FEN plus Stockfish's lines, as in the
            October 2026 comparison
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import chess
import chess.engine

from . import config

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

KEYS_DIR = Path(__file__).parent / "answer_keys"
MODES = ("pipeline", "raw", "explain", "ask")
DEFAULT_ITEM_MODES = ["pipeline", "raw"]     # items name "explain" / "ask" explicitly
RAW_NUM_PREDICT = 2500


# --- Keys -----------------------------------------------------------------

@dataclass
class Item:
    id: str
    say: str
    text: list
    facts: list = field(default_factory=list)
    modes: list = field(default_factory=lambda: list(DEFAULT_ITEM_MODES))


@dataclass
class Key:
    id: str
    title: str
    fen: str
    move: str = ""
    engine_best: str = ""
    labels: list = field(default_factory=list)     # skip unless the move gets one
    must: list = field(default_factory=list)       # [Item]
    must_not: list = field(default_factory=list)   # [Item]
    asks: list = field(default_factory=list)       # [Question]
    source: str = ""

    def board(self) -> chess.Board:
        return chess.Board(self.fen)

    def played(self) -> Optional[chess.Move]:
        return self.board().parse_san(self.move) if self.move else None


def _items(rows: list) -> list:
    return [Item(id=r["id"], say=r.get("say", r["id"]), text=r.get("text", []),
                 facts=r.get("facts", []), modes=r.get("modes", list(DEFAULT_ITEM_MODES)))
            for r in rows]


@dataclass
class Question:
    """A question for 'Ask the coach' about the key's position."""
    id: str
    question: str
    must: list = field(default_factory=list)
    must_not: list = field(default_factory=list)


def _questions(rows: list) -> list:
    out = []
    for r in rows:
        must = _items(r.get("must", []))
        must_not = _items(r.get("must_not", []))
        for i in must + must_not:
            i.modes = ["ask"]
        out.append(Question(r["id"], r["question"], must, must_not))
    return out


def load_keys(paths: Optional[list] = None, ids: Optional[list] = None) -> list:
    """Keys from the given TOML files (default: the bundled ones)."""
    files = [Path(p) for p in paths] if paths else sorted(KEYS_DIR.glob("*.toml"))
    keys = []
    for f in files:
        with open(f, "rb") as fh:
            data = tomllib.load(fh)
        for k in data.get("key", []):
            key = Key(id=k["id"], title=k.get("title", k["id"]), fen=k["fen"],
                      move=k.get("move", ""), engine_best=k.get("engine_best", ""),
                      labels=k.get("labels", []),
                      must=_items(k.get("must", [])),
                      must_not=_items(k.get("must_not", [])),
                      asks=_questions(k.get("ask", [])), source=f.name)
            key.played()          # fails loudly on a bad FEN or illegal move
            keys.append(key)
    if ids:
        missing = set(ids) - {k.id for k in keys}
        if missing:
            raise ValueError(f"unknown key id(s): {', '.join(sorted(missing))}")
        keys = [k for k in keys if k.id in ids]
    return keys


def _matches(patterns: list, text: str) -> bool:
    return any(re.search(p, text, flags=re.I) for p in patterns)


# --- Results --------------------------------------------------------------

@dataclass
class Result:
    key: str
    mode: str                       # "facts", "pipeline", "raw", "text"
    model: str = ""
    text: str = ""
    hits: list = field(default_factory=list)        # must ids found
    misses: list = field(default_factory=list)      # must ids not found
    not_covered: list = field(default_factory=list) # facts mode: no facts pattern
    false_claims: list = field(default_factory=list)  # must_not ids matched
    board_errors: list = field(default_factory=list)  # from check_output
    removed: list = field(default_factory=list)       # sentences the check removed
    caught: list = field(default_factory=list)        # false claims in the first draft
    by_code: list = field(default_factory=list)       # hits only in the code-written sentence
    skipped: str = ""
    secs: float = 0.0
    error: str = ""

    @property
    def recall(self) -> str:
        n = len(self.hits) + len(self.misses)
        return f"{len(self.hits)}/{n}" if n else "-"

    @property
    def wrong(self) -> int:
        return len(self.false_claims) + len(self.board_errors)


def score_text(key: Key, text: str, mode: str = "raw", model: str = "",
               boards: Optional[list] = None, prep: Optional[dict] = None) -> Result:
    """Score a written explanation against a key."""
    from .narrator import check_output
    r = Result(key=key.id, mode=mode, model=model, text=text)
    for item in key.must:
        base = "pipeline" if mode.startswith("pipeline") else mode
        if base in MODES and base not in item.modes:
            continue
        (r.hits if _matches(item.text, text) else r.misses).append(item.id)
    # Which hits the model wrote, and which only the code-written sentence did.
    fixed = (prep or {}).get("fixed", "")
    if fixed and mode in ("explain", "ask"):
        rest = text.replace(fixed, "")
        r.by_code = [i.id for i in key.must if i.id in r.hits and not _matches(i.text, rest)]
    r.false_claims = [i.id for i in key.must_not if _matches(i.text, text)]
    if boards is None:
        b = key.board()
        boards = [b]
        if key.move:
            a = b.copy()
            a.push(key.played())
            boards.append(a)
    from .grounding import ground
    # Explain and Ask are about the position, not the key's move.
    move = None if mode in ("explain", "ask") else key.played()
    # The code-written first sentence of an annotation is not the model's.
    keep = prep.get("fixed", "") if prep else ""
    if not keep and mode.startswith("pipeline") and move is not None:
        from .describe import describe_move
        keep = prep["fixed"] if prep and prep.get("fixed") else describe_move(key.board(), move)
    played = prep.get("played_boards") if prep else None
    better = prep.get("better_boards") if prep else None
    r.board_errors = [f"{f.kind}: \"{f.claim}\" — {f.correction}"
                      for f in ground(text, boards, move, keep=keep,
                                      played=played, better=better,
                                      candidates=(prep or {}).get("candidates") or None)]
    if mode.startswith("pipeline"):
        r.board_errors += check_output(text, [], boards, allowed_san=key.move,
                                       placement=False)
    return r


# --- Engine helpers -------------------------------------------------------

class Engines:
    """One Stockfish process for a whole run; analyses cached per FEN."""

    def __init__(self, path: Optional[str] = None):
        # None: use the settings; "": run without an engine.
        self.path = config.settings.stockfish_path if path is None else path
        self.sf = None
        self.cache = {}
        self.preps = {}

    def __enter__(self):
        if self.path:
            self.sf = chess.engine.SimpleEngine.popen_uci(self.path)
        return self

    def __exit__(self, *exc):
        if self.sf:
            self.sf.quit()

    def analyse(self, board: chess.Board):
        from .engine import run_stockfish
        if not self.sf:
            return None
        fen = board.fen()
        if fen not in self.cache:
            # A clean hash table per position: otherwise earlier analyses
            # change later ones and a key's score depends on run order.
            self.sf.configure({"Clear Hash": None})
            self.cache[fen] = run_stockfish(board, engine=self.sf)
        return self.cache[fen]


def _engine_skip(key: Key, engines: "Engines") -> str:
    """A key written for one engine verdict is skipped under another:
    a different preferred move, or a label on the other side of a
    threshold (moves near a boundary flip with depth)."""
    if not engines.sf:
        return ""
    eb = engines.analyse(key.board())
    if key.engine_best and eb.best_move != key.engine_best:
        return (f"engine preferred {eb.best_move}, the key assumes "
                f"{key.engine_best}")
    if key.labels:
        prep = prepared(key, engines)
        if prep and prep["label"] not in key.labels:
            return (f"engine labels the move {prep['label']} at this depth; "
                    f"the key assumes {' or '.join(key.labels)}")
    return ""


def run_questions(key: Key, model: str, engines: Engines) -> list:
    """'Ask the coach': every question in the key, each scored on its own."""
    from .coach import ask
    out = []
    for q in key.asks:
        t0 = time.time()
        a = ask(key.board(), q.question, model, engines.sf)
        qk = Key(f"{key.id}?{q.id}", q.question, key.fen, must=q.must, must_not=q.must_not)
        if a.error:
            out.append(Result(key=qk.id, mode="ask", model=model, error=a.error))
            continue
        r = score_text(qk, a.text, "ask", model, boards=line_boards(
            Key(key.id, key.title, key.fen), engines) + list(a.boards),
            prep={"fixed": a.fixed, "candidates": a.candidates})
        r.removed, r.caught, r.secs = a.removed, a.caught, time.time() - t0
        r.text = (a.text + f"\n\n_Route: {a.route or 'agent'}. Tools used: "
                  + ", ".join(f"{n}({', '.join(map(str, args.get('moves', [])))})"
                              for n, args in a.tool_calls) + "_")
        out.append(r)
    return out


def line_boards(key: Key, engines: Engines, plies: int = 4) -> list:
    """Before, after, and positions along every engine line from both: what
    a free explanation may legitimately talk about."""
    b = key.board()
    boards = [b]
    starts = [b]
    if key.move:
        a = b.copy()
        a.push(key.played())
        boards.append(a)
        starts.append(a)
    for start in starts:
        eng = engines.analyse(start)
        if not eng:
            continue
        for pv in [eng.best_line] + [line for _, line in eng.alt_lines]:
            t = start.copy()
            for san in pv[:plies]:
                try:
                    t.push_san(san)
                except ValueError:
                    break
                boards.append(t.copy())
    return boards


def prepared(key: Key, engines: Engines) -> Optional[dict]:
    """The review pipeline's prompt and check data for a move key."""
    from .narrator import prepare_move
    if not key.move or not engines.sf:
        return None
    if key.id in engines.preps:            # concepts cost searches: once per key
        return engines.preps[key.id]
    b = key.board()
    mv = key.played()
    a = b.copy()
    a.push(mv)
    eb, ea = engines.analyse(b), engines.analyse(a)
    engines.preps[key.id] = prepare_move(b, mv, eb, ea, maia=None, engine=engines.sf)
    return engines.preps[key.id]


def facts_text(key: Key, engines: Engines) -> str:
    """What the model is given: the pipeline prompt if Stockfish is there,
    otherwise the static facts of the position after the move."""
    from .describe import describe_move, describe_move_effects
    from .facts import position_facts
    prep = prepared(key, engines)
    if prep:
        return prep["prompt"]
    b = key.board()
    if not key.move:
        return position_facts(b)
    mv = key.played()
    lines = [describe_move(b, mv)] + describe_move_effects(b, mv)
    a = b.copy()
    a.push(mv)
    return "\n".join(lines) + "\n\n" + position_facts(a)


# --- The three runs -------------------------------------------------------

def run_facts(key: Key, engines: Engines) -> Result:
    """Facts-layer recall: does what we give the model contain each fact?"""
    r = Result(key=key.id, mode="facts")
    if key.engine_best or key.labels:
        if not engines.sf:
            r.skipped = "needs Stockfish (line comparison facts)"
            return r
        r.skipped = _engine_skip(key, engines)
        if r.skipped:
            return r
    text = facts_text(key, engines)
    r.text = text
    for item in key.must:
        if not item.facts:
            r.not_covered.append(item.id)
        elif _matches(item.facts, text):
            r.hits.append(item.id)
        else:
            r.misses.append(item.id)
    return r


RAW_PROMPT = """You are a chess coach explaining a position to a club player (about 1500).

Position (FEN): {fen}
{to_move} to move.
{move_line}
Stockfish's top lines (score in pawns, from White's point of view):
{lines}

Explain the position and the engine's ideas in plain language: material, king safety, threats, and why the best moves are best."""


def _raw_prompt(key: Key, engines: Engines) -> str:
    b = key.board()
    eb = engines.analyse(b)
    lines = []
    if eb:
        first = [(eb.score_cp, eb.score_mate, eb.best_line)]
        alts = [(cp, mate, pv) for (cp, mate), (_, pv) in zip(eb.alt_scores, eb.alt_lines)]
        for i, (cp, mate, pv) in enumerate(first + alts, 1):
            score = f"mate in {mate}" if mate is not None else f"{(cp or 0) / 100:+.2f}"
            lines.append(f"{i}. {' '.join(pv)} ({score})")
    move_line = (f"The move played was {key.move}. Explain whether it was good "
                 "and what was better, if anything." if key.move else "")
    return RAW_PROMPT.format(fen=key.fen, to_move="White" if b.turn else "Black",
                             move_line=move_line,
                             lines="\n".join(lines) or "(not available)")


def run_model(key: Key, model: str, mode: str, engines: Engines) -> Result:
    """One model, one key, one mode."""
    from .narrator import call_llm, narrate
    t0 = time.time()
    if not engines.sf:
        return Result(key=key.id, mode=mode, model=model, skipped="needs Stockfish")
    if mode == "explain":
        from .coach import explain
        a = explain(key.board(), model, engines.sf)
        if a.error:
            return Result(key=key.id, mode=mode, model=model, error=a.error)
        r = score_text(key, a.text, mode, model, boards=line_boards(
            Key(key.id, key.title, key.fen), engines), prep={"fixed": a.fixed})
        r.removed, r.caught, r.secs = a.removed, a.caught, time.time() - t0
        return r
    if mode == "ask":
        return run_questions(key, model, engines)[0] if key.asks else Result(
            key=key.id, mode=mode, model=model, skipped="no questions in this key")
    skip = _engine_skip(key, engines)
    if skip:
        return Result(key=key.id, mode=mode, model=model, skipped=skip)
    if mode == "pipeline":
        prep = prepared(key, engines)
        if prep is None:
            return Result(key=key.id, mode=mode, model=model,
                          skipped="no move to annotate (position keys need "
                                  "'Explain this position', phase 4)")
        out = narrate(model, prep, fallback=False)   # measure the model alone
        if out["err"]:
            return Result(key=key.id, mode=mode, model=model, error=out["err"])
        label = mode if config.settings.grounding else "pipeline (no check)"
        r = score_text(key, out["text"], label, model, boards=prep["boards"], prep=prep)
        r.removed = out.get("removed", [])
        r.caught = out.get("first_fails", [])
    elif mode == "raw":
        # A free explanation runs far longer than an annotation; at the
        # narrator's 800-token cap the baseline replies were cut off mid-line,
        # which counted as misses.
        cap = config.settings.num_predict
        config.settings.num_predict = max(cap, RAW_NUM_PREDICT)
        try:
            text, err = call_llm(model, [{"role": "user", "content": _raw_prompt(key, engines)}],
                                 system=None)
        finally:
            config.settings.num_predict = cap
        if err:
            return Result(key=key.id, mode=mode, model=model, error=err)
        r = score_text(key, text, mode, model, boards=line_boards(key, engines))
    else:
        raise ValueError(f"unknown mode {mode!r}; use one of {', '.join(MODES)}")
    r.secs = time.time() - t0
    return r


# --- Reporting ------------------------------------------------------------

def summary_table(results: list) -> str:
    rows = ["| Key | Run | Recall | False claims | Board errors | Caught | Removed | Note |",
            "|---|---|---|---|---|---|---|---|"]
    for r in results:
        run = r.mode + (f" · {r.model}" if r.model else "")
        note = r.skipped or r.error
        if r.mode == "facts" and r.not_covered and not note:
            note = "not covered yet: " + ", ".join(r.not_covered)
        if r.misses and not note:
            note = "missed: " + ", ".join(r.misses)
        if r.by_code:
            note = (note + "; " if note else "") + "by code: " + ", ".join(r.by_code)
        off = bool(r.skipped or r.error)
        rows.append(f"| {r.key} | {run} | {'-' if off else r.recall} | "
                    f"{', '.join(r.false_claims) or ('-' if off else '0')} | "
                    f"{'-' if off else len(r.board_errors)} | "
                    f"{'-' if off or r.mode not in ('pipeline', 'explain', 'ask') else len(r.caught)} | "
                    f"{'-' if off or r.mode not in ('pipeline', 'explain', 'ask') else len(r.removed)} | {note} |")
    return "\n".join(rows)


def item_table(results: list, keys: list) -> str:
    """With repeated runs: how often each required fact was stated and each
    false claim made, per key, mode and model. One run at temperature 0.3
    is a sample, not a score."""
    by_key = {k.id: k for k in keys}
    for k in keys:                                   # ask questions score as keys
        for q in k.asks:
            by_key[f"{k.id}?{q.id}"] = Key(f"{k.id}?{q.id}", q.question, k.fen,
                                           must=q.must, must_not=q.must_not)
    groups = {}
    for r in results:
        if r.mode == "facts" or r.skipped or r.error:
            continue
        groups.setdefault((r.key, r.mode, r.model), []).append(r)
    rows = ["| Key | Run | Item | Stated | |", "|---|---|---|---|---|"]
    for (kid, mode, model), rs in groups.items():
        n = len(rs)
        k = by_key[kid]
        run = f"{mode} · {model}"
        for item in k.must:
            seen = sum(item.id in r.hits for r in rs)
            asked = sum(item.id in r.hits or item.id in r.misses for r in rs)
            if asked:
                rows.append(f"| {kid} | {run} | {item.id} | {seen}/{asked} | must |")
        for item in k.must_not:
            made = sum(item.id in r.false_claims for r in rs)
            if made:
                rows.append(f"| {kid} | {run} | {item.id} | {made}/{n} | FALSE |")
        caught = sum(len(r.caught) for r in rs)
        removed = sum(len(r.removed) for r in rs)
        if caught or removed:
            rows.append(f"| {kid} | {run} | (grounding) | caught {caught}, removed "
                        f"{removed} over {n} runs | |")
    return "\n".join(rows)


def totals(results: list) -> dict:
    done = [r for r in results if not (r.skipped or r.error)]
    hit = sum(len(r.hits) for r in done)
    n = hit + sum(len(r.misses) for r in done)
    return {"runs": len(done), "recall": f"{hit}/{n}",
            "false_claims": sum(len(r.false_claims) for r in done),
            "board_errors": sum(len(r.board_errors) for r in done),
            "removed": sum(len(r.removed) for r in done),
            "caught": sum(len(r.caught) for r in done),
            "by_code": sum(len(r.by_code) for r in done)}


def write_report(results: list, path: str, keys: list) -> None:
    by_id = {k.id: k for k in keys}
    for k in keys:
        for q in k.asks:
            by_id[f"{k.id}?{q.id}"] = Key(f"{k.id}?{q.id}", f"{k.title} — asked: "
                                          f"\"{q.question}\"", k.fen,
                                          must=q.must, must_not=q.must_not)
    out = ["# Answer-key evaluation", "", summary_table(results), ""]
    t = totals(results)
    out += [f"Total: recall {t['recall']}, false claims {t['false_claims']}, "
            f"board errors {t['board_errors']} over {t['runs']} runs.", ""]
    for r in results:
        if r.mode == "facts" or not r.text:
            continue
        k = by_id[r.key]
        out += [f"## {k.title} — {r.mode}" + (f" · {r.model}" if r.model else ""), ""]
        out += [r.text.strip(), ""]
        lookup = {i.id: i.say for i in k.must + k.must_not}
        if r.misses:
            out += ["Missed: " + "; ".join(lookup[i] for i in r.misses), ""]
        if r.false_claims:
            out += ["False claims: " + "; ".join(lookup[i] for i in r.false_claims), ""]
        if r.board_errors:
            out += ["Board errors:"] + [f"- {e}" for e in r.board_errors] + [""]
        if r.caught:
            out += ["Caught in the first draft:"] + [f"- {e}" for e in r.caught] + [""]
        if r.removed:
            out += ["Removed by the grounding check:"] + [f"- {e}" for e in r.removed] + [""]
    Path(path).write_text("\n".join(out), encoding="utf-8")


# --- Command line ---------------------------------------------------------

def installed_models() -> list:
    """Model names Ollama has, e.g. ['qwen3:14b', 'mistral:7b']."""
    import ollama
    resp = ollama.list()
    items = getattr(resp, "models", None)
    if items is None:
        items = resp.get("models", [])
    names = []
    for m in items:
        name = getattr(m, "model", None) or (m.get("model") or m.get("name")
                                              if isinstance(m, dict) else None)
        if name:
            names.append(name)
    return names


def check_models(models: list) -> None:
    """Fail before a long run, not after it, when a model name is wrong."""
    if not models:
        return
    try:
        have = installed_models()
    except Exception as e:
        raise ValueError(f"cannot reach Ollama ({e}). Is it running?") from e
    # Ollama treats 'name' and 'name:latest' as the same model.
    norm = lambda n: n if ":" in n else n + ":latest"
    missing = [m for m in models if norm(m) not in {norm(h) for h in have}]
    if missing:
        raise ValueError(f"model(s) not found in Ollama: {', '.join(missing)}. "
                         f"Installed: {', '.join(have) or 'none'}")


def main_eval(args, models: list) -> int:
    """`chesscoach eval`."""
    keys = load_keys(args.key_files, args.key_ids)
    if args.list:
        for k in keys:
            print(f"{k.id:<16} {k.title}  ({len(k.must)} must, "
                  f"{len(k.must_not)} must-not; {k.source})")
        return 0

    if args.text:
        if len(keys) != 1:
            raise ValueError("--text needs exactly one --key")
        text = Path(args.text).read_text(encoding="utf-8")
        results = [score_text(keys[0], text, mode="raw", model=Path(args.text).name)]
    else:
        modes = args.modes or (["facts", "pipeline"] if models else ["facts"])
        if args.no_grounding:
            config.settings.grounding = False
        if any(m != "facts" for m in modes):
            check_models(models)
        results = []
        with Engines() as eng:
            if not eng.sf:
                print("note: Stockfish not found — move keys use static facts "
                      "only, and model runs are skipped", file=sys.stderr)
            for k in keys:
                if "facts" in modes:
                    results.append(run_facts(k, eng))
                for mode in (m for m in modes if m != "facts"):
                    for model in models:
                        for n in range(args.runs):
                            tag = f" (run {n + 1}/{args.runs})" if args.runs > 1 else ""
                            print(f"  {k.id} · {mode} · {model}{tag} ...", file=sys.stderr)
                            if mode == "ask":
                                if k.asks:
                                    results += run_questions(k, model, eng)
                                continue
                            results.append(run_model(k, model, mode, eng))

    if args.runs > 1:
        print(item_table(results, keys))
        print()
    print(summary_table(results))
    t = totals(results)
    if t["by_code"]:
        print(f"\n{t['by_code']} of the facts stated were in the code-written first "
              f"sentence; the model stated the rest.")
    print(f"\nTotal: recall {t['recall']}, false claims {t['false_claims']}, "
          f"board errors {t['board_errors']}, caught in first drafts {t['caught']}, "
          f"sentences removed {t['removed']} "
          f"over {t['runs']} runs.")
    if args.report:
        write_report(results, args.report, keys)
        print(f"Report written to {args.report}")
    if args.strict and any(r.misses or r.false_claims or r.error for r in results):
        return 1
    return 0
