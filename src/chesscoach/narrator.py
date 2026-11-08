"""Calls the local LLM, checks its output, retries once on rule breaks."""

from __future__ import annotations

import re
import time
from typing import Optional

import chess
import ollama

from . import config, llm
from .assessment import compute_assessment
from .describe import side_name, PIECE_NAMES, describe_move
from .engine import EngineAnalysis, MaiaPrediction
from .features import extract_features
from .grounding import correction_text, ground, remove_failed
from .prompts import SYSTEM_PROMPT, build_user_prompt
from .tactics import extract_tactical_features


def check_output(text: str, banned: list, boards: list,
                 allowed_san: str = "", label: str = "",
                 placement: bool = True) -> list:
    """
    Cheap post-generation check. Doesn't fix anything — it flags lines so
    we can measure how often each model breaks the rules.
      - banned overstatement words
      - '<piece> on <square>' claims that match neither the before- nor the
        after-position (the text may legitimately say 'the knight on f3'
        about the piece that just left f3)
      - algebraic move notation other than the annotated move itself
    """
    import re
    warnings = []
    low = text.lower()
    if label:
        m = re.search(r"\*\*Assessment\*\*:?\s*\**\s*([A-Za-z]+)", text)
        if m and m.group(1).lower() != label.lower():
            warnings.append(f"wrong label: wrote '{m.group(1)}', must be '{label}'")
    for w in banned:
        if re.search(rf"\b{re.escape(w)}\b", low):
            warnings.append(f"banned word used: '{w}'")
    for pname, sq in (re.findall(
            r"\b(pawn|knight|bishop|rook|queen|king)s? on \**([a-h][1-8])\b", low)
            if placement else []):
        found = [b.piece_at(chess.parse_square(sq)) for b in boards]
        if not any(p and PIECE_NAMES[p.piece_type] == pname for p in found):
            actual = PIECE_NAMES[found[-1].piece_type] if found[-1] else "nothing"
            warnings.append(f"says '{pname} on {sq}', board has {actual} there")
    for m in re.findall(r"(?<![\w-])(?:[KQRBN][a-h]?[1-8]?x?[a-h][1-8]|[a-h]x[a-h][1-8]|O-O(?:-O)?)(?![\w-])", text):
        if m != allowed_san:
            warnings.append(f"move notation in output: '{m}'")
    return warnings


def prepare_move(board_before: chess.Board, played: chess.Move,
                 eng_before: EngineAnalysis, eng_after: EngineAnalysis,
                 maia: Optional[MaiaPrediction], opening_note: str = "",
                 reason: str = "", fixed_prefix: str = "",
                 focus: str = "", engine=None, extra_facts: Optional[list] = None) -> dict:
    """Everything the narrator needs for one move: prompt + check data.
    `extra_facts`: measured facts added after the key facts (how hard the
    better move was to find, forced lines)."""
    board_after = board_before.copy()
    board_after.push(played)
    features = extract_features(board_after)
    tactical = extract_tactical_features(board_after, eng_after)

    # Tier 2: engine-verified threats, the moved piece's job, and the
    # short ranked list of key facts. Needs a Stockfish process.
    key_facts, concept_notes, concepts, diffs = [], [], None, []
    plan_info = None
    if engine is not None and config.settings.concepts:
        from .concepts import analyse_move
        from .features import compare_lines, line_diff
        label0, _ = compute_assessment(
            eng_before.score_cp, eng_after.score_cp, eng_before.score_mate,
            eng_after.score_mate, board_before.turn == chess.WHITE,
            delivered_mate=board_after.is_checkmate())
        comparison = (compare_lines(board_before, played, eng_before, eng_after)
                      if label0 in ("Inaccuracy", "Mistake", "Blunder") else [])
        diffs = (line_diff(board_before, played, eng_before, eng_after)
                 if comparison else [])
        from .prompts import _effects
        c = analyse_move(engine, board_before, played, eng_before, eng_after,
                         _effects(board_before, played), comparison)
        key_facts, concept_notes, concepts = c.key_facts, c.notes, c
        # The plan behind the engine's move when it is quiet: what it
        # prepares, and why the idea does not work at once.
        if config.settings.plans and eng_before.best_move:
            from .plans import find_plan
            try:
                best_mv = board_before.parse_san(eng_before.best_move)
                plan = find_plan(engine, board_before, best_mv)
            except ValueError:
                plan = None
            if plan is not None:
                plan_info = plan.to_dict()
                plan_info["played"] = best_mv == played
                key_facts = plan.facts(side_name(board_before.turn)) + key_facts
                key_facts = key_facts[:6]
        equal = " ".join(x for x in (c.equal_sentence, c.loose_sentence) if x)
    else:
        equal = ""
    if extra_facts:
        key_facts = list(key_facts) + [f for f in extra_facts if f not in key_facts]
    # root(): the position the move stack starts from (a FEN, or the start).
    pgn_moves = board_after.root().variation_san(board_after.move_stack)
    prompt, banned, fixed = build_user_prompt(
        board_before, played, features, eng_before, eng_after,
        tactical=tactical,
        maia=maia if (maia and maia.predicted_move) else None,
        pgn_moves=pgn_moves, opening_note=opening_note,
        fixed_prefix=fixed_prefix, focus=focus, key_facts=key_facts,
        fixed_suffix=equal)

    # Positions along the engine lines, so e.g. 'pawn on f6' after a
    # described capture on f6 isn't flagged as a wrong piece claim.
    # Claims about what a move LEADS TO are checked only on its own line:
    # the played move's (after it) or the engine's choice ('Better was').
    def line_boards(start, line, n=4):
        out, tb = [], start.copy()
        for san in line[:n]:
            try:
                tb.push_san(san)
            except ValueError:
                break
            out.append(tb.copy())
        return out
    played_boards = [board_after] + line_boards(board_after, eng_after.best_line)
    better_boards = line_boards(board_before, eng_before.best_line, 5)
    if plan_info:
        from .plans import plan_boards
        extra = plan_boards(board_before, plan_info)
        better_boards = better_boards + extra
    check_boards = [board_before] + played_boards + better_boards

    # A code-written 'Better was', used if the model's is removed.
    better_fixed = ""
    if eng_before.best_move and eng_before.best_move != board_before.san(played):
        try:
            better_fixed = ("The engine preferred this: "
                            + describe_move(board_before,
                                            board_before.parse_san(eng_before.best_move))
                            + ".")
        except ValueError:
            pass

    label, _ = compute_assessment(
        eng_before.score_cp, eng_after.score_cp,
        eng_before.score_mate, eng_after.score_mate,
        board_before.turn == chess.WHITE,
        delivered_mate=board_after.is_checkmate())
    n = board_before.fullmove_number
    san = board_before.san(played)
    move_label = f"{n}. {san}" if board_before.turn == chess.WHITE else f"{n}...{san}"
    return {"prompt": prompt, "banned": banned, "boards": check_boards,
            "san": san, "label": label, "move_label": move_label,
            "fixed": fixed, "reason": reason, "move": played,
            "better_fixed": better_fixed, "key_facts": key_facts,
            "concept_notes": concept_notes, "concepts": concepts, "diffs": diffs,
            "plan": plan_info,
            "played_boards": played_boards, "better_boards": better_boards}


def insert_fixed_sentence(text: str, fixed: str) -> str:
    """Put the code-written first sentence at the start of 'What happened'."""
    if fixed in text:
        return text
    m = re.search(r"(\*\*What happened\*\*:?\s*)", text)
    if m:
        return text[:m.end()] + fixed + " " + text[m.end():]
    return f"**What happened**: {fixed}\n" + text


_SECTION = re.compile(r"^\s*\*\*(Assessment|What happened|Better was|What now):?\*\*:?\s*(.*)$", re.I)
_ORDER = ["Assessment", "What happened", "Better was", "What now"]


def tidy_annotation(text: str, prep: dict) -> str:
    """
    Put the annotation back into its fixed shape after the model, a
    rewrite or the grounding check has had it: the four headings in order,
    the label from code, the code-written first sentence at the start of
    'What happened', and text that lost its heading kept under 'What
    happened'. If 'Better was' was removed on a bad move, the engine's
    move is stated in a code-written sentence instead.
    """
    parts = {k: [] for k in _ORDER}
    current = None
    for line in text.split("\n"):
        m = _SECTION.match(line)
        if m:
            current = next(k for k in _ORDER if k.lower() == m.group(1).lower())
            if m.group(2).strip():
                parts[current].append(m.group(2).strip())
        elif line.strip():
            # Text with no heading of its own belongs to 'What happened'
            # unless it follows 'Better was' or 'What now'.
            key = current if current in ("Better was", "What now") else "What happened"
            parts[key].append(line.strip())
    body = " ".join(parts["What happened"]).strip()
    fixed = prep["fixed"]
    if fixed.strip(" .") not in body:
        body = (fixed + " " + body).strip()
    out = [f"**Assessment**: {prep.get('label') or ' '.join(parts['Assessment'])}",
           f"**What happened**: {body}"]
    better = " ".join(parts["Better was"]).strip()
    if prep.get("label") in ("Inaccuracy", "Mistake", "Blunder") and not better \
            and prep.get("better_fixed"):
        better = prep["better_fixed"]
    if better:
        out.append(f"**Better was**: {better}")
    now = " ".join(parts["What now"]).strip()
    if now:
        out.append(f"**What now**: {now}")
    return "\n\n".join(out)


_DEFAULT = object()


def call_llm(model: str, messages: list, system=_DEFAULT,
             max_tokens: Optional[int] = None) -> tuple:
    """
    One chat call with thinking turned off (Qwen3 etc.): we want narration
    of the given facts, not private reasoning. Returns (text, error).
    `system=None` sends no system prompt (the evaluation's 'raw' runs).
    """
    options = {"temperature": config.settings.temperature, "top_p": 0.9,
               "num_predict": config.settings.num_predict,
               "num_ctx": config.settings.num_ctx}
    system = SYSTEM_PROMPT if system is _DEFAULT else system
    full = ([{"role": "system", "content": system}] if system else []) + messages
    first_user = 1 if system else 0
    if llm.is_online(model):
        try:
            resp = llm.chat(model, full, max_tokens=max_tokens)
        except (llm.LLMError, config.ConfigError) as e:
            return None, str(e)
        text = resp["message"]["content"]
        return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.S).strip(), None
    if max_tokens:
        options["num_predict"] = max_tokens
    try:
        try:
            resp = ollama.chat(model=model, messages=full, options=options,
                               think=False, keep_alive=config.settings.keep_alive)
        except Exception as e:
            # Model without a thinking mode, or an older ollama package.
            if "think" not in str(e).lower() and not isinstance(e, TypeError):
                raise
            full = [dict(m) for m in full]
            full[first_user]["content"] = "/no_think\n" + full[first_user]["content"]
            resp = ollama.chat(model=model, messages=full, options=options)
        text = resp["message"]["content"]
        return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.S).strip(), None
    except Exception as e:
        return None, f"{e} (is '{model}' pulled? run: ollama list)"


def review_text(text: str, prep: dict) -> tuple:
    """(rule issues, grounding failures) for one annotation."""
    grounded = config.settings.grounding
    issues = check_output(text, prep["banned"], prep["boards"],
                          allowed_san=prep["san"], label=prep["label"],
                          placement=not grounded)
    fails = (ground(text, prep["boards"], prep.get("move"), keep=prep["fixed"],
                    played=prep.get("played_boards"), better=prep.get("better_boards"))
             if grounded else [])
    return issues, fails


def narrate(model: str, prep: dict) -> dict:
    """
    Annotate one move. Rule breaks and false claims about the board get one
    rewrite request that names exactly what was wrong. A false claim that
    survives the rewrite is removed with its sentence: the coach says less
    rather than something untrue.
    """
    t0 = time.time()
    msgs = [{"role": "user", "content": prep["prompt"]}]
    text, err = call_llm(model, msgs)
    if err:
        return {"text": None, "err": err, "issues": [], "first_issues": 0,
                "removed": [], "first_fails": [], "secs": time.time() - t0}
    issues, fails = review_text(text, prep)
    first = len(issues) + len(fails)
    first_fails = [f"{f.kind}: \"{f.claim}\" — {f.correction}" for f in fails]
    if first:
        fix = "Your annotation has problems.\n"
        if issues:
            fix += "Rules broken:\n" + "\n".join(f"- {w}" for w in issues) + "\n"
        if fails:
            fix += ("Statements that are false on the board:\n"
                    + correction_text(fails) + "\n")
        fix += ("Rewrite the whole annotation in the same format, fixing only "
                "those problems. If you cannot state a point correctly from the "
                "facts given, leave it out. Do not add anything new.")
        text2, err2 = call_llm(model, msgs + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": fix}])
        if not err2:
            issues2, fails2 = review_text(text2, prep)
            if len(issues2) + len(fails2) < first:
                text, issues, fails = text2, issues2, fails2
    removed = []
    if fails:
        text = remove_failed(text, fails)
        seen = {}
        for f in fails:
            seen.setdefault(f.sentence, f.correction)
        removed = [f"{s} [{c}]" for s, c in seen.items()]
        issues = issues + [f"sentence removed, {c}: \"{s}\"" for s, c in seen.items()]
    return {"text": tidy_annotation(text, prep), "err": None,
            "issues": issues, "first_issues": first, "removed": removed,
            "first_fails": first_fails, "secs": time.time() - t0}


BATCH_HEAD = re.compile(r"^\s*#{2,4}\s*MOVE\s+(\d+)\b[^\n]*$", re.M | re.I)


def _batch_prompt(preps: list) -> str:
    n = len(preps)
    head = (f"Annotate each of the {n} moves below, separately. Each move has its own facts: "
            "use only that move's facts for its annotation, and follow every rule for each "
            f"one. Answer with exactly {n} sections, in order, each starting with a line "
            "`### MOVE k` (k = 1 to " + str(n) + ") and then that move's annotation in the "
            "usual format. Write nothing else.\n\n")
    return head + "\n\n".join(f"### MOVE {k}\n{p['prompt']}" for k, p in enumerate(preps, 1))


def _split_batch(text: str, n: int) -> dict:
    """{k: annotation} from an answer with ### MOVE k sections."""
    out = {}
    heads = list(BATCH_HEAD.finditer(text or ""))
    for j, h in enumerate(heads):
        k = int(h.group(1))
        end = heads[j + 1].start() if j + 1 < len(heads) else len(text)
        body = text[h.end():end].strip()
        if 1 <= k <= n and body and k not in out:
            out[k] = body
    return out


def narrate_batch(model: str, preps: list) -> list:
    """Annotate several moves in one request, for online models with small
    free quotas: the comments in one call, the checks on each comment as in
    narrate(), and one more call for all the rewrites they ask for. Then a
    false claim still left is removed with its sentence. -> narrate()'s
    results, one per prep."""
    t0 = time.time()
    n = len(preps)
    if not n:
        return []
    budget = config.settings.num_predict * n + 200
    msgs = [{"role": "user", "content": _batch_prompt(preps)}]
    text, err = call_llm(model, msgs, max_tokens=budget)
    if err:
        return [{"text": None, "err": err, "issues": [], "first_issues": 0, "removed": [],
                 "first_fails": [], "secs": time.time() - t0} for _ in preps]
    parts = _split_batch(text, n)
    state = {}
    for k, prep in enumerate(preps, 1):
        if k in parts:
            issues, fails = review_text(parts[k], prep)
            state[k] = [parts[k], issues, fails, len(issues) + len(fails)]
    redo = [k for k, st in state.items() if st[3]]
    if redo:
        fix = ["Some annotations have problems. Rewrite only these moves, each under its own "
               "`### MOVE k` line, in the same format, fixing only the problems named. If you "
               "cannot state a point correctly from that move's facts, leave it out. Do not "
               "add anything new."]
        for k in redo:
            _, issues, fails, _ = state[k]
            fix.append(f"### MOVE {k}")
            if issues:
                fix.append("Rules broken:\n" + "\n".join(f"- {w}" for w in issues))
            if fails:
                fix.append("Statements that are false on the board:\n" + correction_text(fails))
        text2, err2 = call_llm(model, msgs + [{"role": "assistant", "content": text},
                                              {"role": "user", "content": "\n\n".join(fix)}],
                               max_tokens=config.settings.num_predict * len(redo) + 200)
        if not err2:
            for k, body in _split_batch(text2, n).items():
                if k in redo:
                    issues2, fails2 = review_text(body, preps[k - 1])
                    if len(issues2) + len(fails2) < state[k][3]:
                        state[k][:3] = [body, issues2, fails2]
    secs = (time.time() - t0) / n
    out = []
    for k, prep in enumerate(preps, 1):
        if k not in state:
            out.append({"text": None, "err": "The online model left this move out of its answer.",
                        "issues": [], "first_issues": 0, "removed": [], "first_fails": [],
                        "secs": secs})
            continue
        body, issues, fails, first = state[k]
        removed = []
        if fails:
            body = remove_failed(body, fails)
            seen = {}
            for f in fails:
                seen.setdefault(f.sentence, f.correction)
            removed = [f"{s} [{c}]" for s, c in seen.items()]
            issues = issues + [f"sentence removed, {c}: \"{s}\"" for s, c in seen.items()]
        out.append({"text": tidy_annotation(body, prep), "err": None, "issues": issues,
                    "first_issues": first, "removed": removed, "first_fails": [],
                    "secs": secs})
    return out


class NarratorUnavailable(config.ConfigError):
    """Ollama can't narrate: not running, or the model isn't pulled."""


def _model_names(listing) -> set:
    models = listing.get("models", []) if isinstance(listing, dict) else \
        getattr(listing, "models", []) or []
    names = set()
    for m in models:
        for key in ("model", "name"):
            v = m.get(key) if isinstance(m, dict) else getattr(m, key, None)
            if v:
                names.add(v)
    return names


def check_ollama(model: str) -> None:
    """Fail early, in plain words, when the model can't narrate: before a
    review spends minutes on Stockfish, and again if Ollama goes away in
    the middle. Raises NarratorUnavailable."""
    if llm.is_online(model):
        try:
            llm.check(model)
        except config.ConfigError as e:
            raise NarratorUnavailable(str(e)) from None
        return
    try:
        names = _model_names(ollama.list())
    except Exception as e:
        raise NarratorUnavailable(
            "Ollama isn't running, so the coach can't write comments. Start Ollama "
            "(or install it from https://ollama.com/download), then review again.") from e
    if model not in names and f"{model}:latest" not in names:
        raise NarratorUnavailable(
            f"Ollama is running but the model '{model}' isn't installed. Run "
            f"`ollama pull {model}`, or choose an installed model in Settings.")


def warm_up(model: str):
    """Ask Ollama to load the model and keep it loaded; no text generated."""
    if llm.is_online(model):
        return                       # nothing to load: the service has it
    try:
        ollama.chat(model=model, messages=[], keep_alive=config.settings.keep_alive,
                    options={"num_ctx": config.settings.num_ctx})
    except Exception:
        pass   # a failed warm-up only costs time; the real call reports errors
