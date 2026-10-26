"""
Review a whole game: analyse, pick critical moments, narrate.

`review_game()` returns structured data (used by the GUI);
`annotate_game()` wraps it for the command line and writes the markdown report.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

import chess
import chess.engine
import chess.pgn

from . import config
from .assessment import compute_assessment, eval_phrase, win_pct
from .engine import EngineAnalysis, open_maia, run_maia, run_stockfish
from .engine_cache import open_stockfish
from .moments import moment_context, pick_critical
from .narrator import narrate, prepare_move, warm_up
from .describe import move_notes, move_shapes, side_name
from .opening import book_status, opening_notes
from .report import print_scoreboard
from .summary import summarize
from .themes import move_themes

# progress(stage, done, total, message); stages: "engine", "narrate", "done"
Progress = Callable[[str, int, int, str], None]


class Cancelled(Exception):
    """The caller asked the review to stop."""


@dataclass
class MoveReview:
    ply: int                      # 0-based index into the game's moves
    move_label: str               # "8...Bg4"
    san: str
    uci: str
    color: str                    # "white" / "black"
    fen_before: str
    fen_after: str
    label: str                    # Excellent ... Blunder, "Book" for theory
    score_cp: Optional[int]       # engine score after the move (White's view)
    score_mate: Optional[int]
    verdict: str                  # "Black is clearly better"
    best_san: str                 # engine's choice in the position before
    best_uci: str
    book: bool
    reason: str = ""              # why it is a critical moment ("" if not)
    annotations: dict = field(default_factory=dict)   # model -> text
    issues: dict = field(default_factory=dict)        # model -> [str]
    stats: dict = field(default_factory=dict)         # model -> {secs, first_issues}
    key_facts: list = field(default_factory=list)     # what the coach was told matters
    win_before: Optional[float] = None    # mover's winning chance before the move (%)
    win_after: Optional[float] = None     # and after it
    themes: list = field(default_factory=list)        # [Theme dict]: what the error got wrong
    plan: dict = field(default_factory=dict)          # the plan behind the engine's quiet move
    threat: dict = field(default_factory=dict)        # the opponent's threat when moving:
                                                      # {uci, text, ignored}, for the arrow
    notes: list = field(default_factory=list)         # what the move does, one line each (code)
    shapes: list = field(default_factory=list)        # arrows/circles for what it does (code)
    opening: dict = field(default_factory=dict)       # {eco, name, idea?} when newly named
    left_theory: dict = field(default_factory=dict)   # {alternatives, opening} on leaving theory
    mate_before: Optional[int] = None    # forced mate for the mover (+) or against (-), before
    mate_after: Optional[int] = None     # and after the move


@dataclass
class GameReview:
    headers: dict
    side: str
    models: list
    start_fen: str
    moves: list                   # [MoveReview], every ply
    seconds: float = 0.0
    summary: list = field(default_factory=list)   # [Claim dict], built from stored facts

    def moments(self) -> list:
        return [m for m in self.moves if m.reason]

    def to_dict(self) -> dict:
        return asdict(self)


def _mate_for(score_mate: Optional[int], white: bool) -> Optional[int]:
    """A White-view mate score from the mover's side: + the mover mates."""
    if score_mate is None:
        return None
    return score_mate if white else -score_mate


def load_game(pgn_path: str) -> chess.pgn.Game:
    with open(pgn_path, encoding="utf-8") as f:
        game = chess.pgn.read_game(f)
    if game is None or not list(game.mainline_moves()):
        raise ValueError(f"No game found in {pgn_path}")
    return game


def review_game(game: chess.pgn.Game, side: str = "both", models: Optional[list] = None,
                every_move: bool = False, progress: Optional[Progress] = None,
                should_stop: Optional[Callable[[], bool]] = None,
                on_prompt: Optional[Callable[[str], None]] = None) -> GameReview:
    models = models or [config.settings.model]
    progress = progress or (lambda *a: None)
    stop = should_stop or (lambda: False)
    t_start = time.time()

    moves = list(game.mainline_moves())
    boards = [game.board()]
    for m in moves:
        b = boards[-1].copy()
        b.push(m)
        boards.append(b)

    # Stockfish: every position once. Position N+1's analysis is both the
    # 'after' of move N and the 'before' of move N+1. One engine process.
    config.settings.require_stockfish()
    # Load the first model into VRAM while Stockfish works, so the first
    # annotation doesn't wait ~3 minutes for the model to load.
    warm = threading.Thread(target=warm_up, args=(models[0],), daemon=True)
    warm.start()

    evals: list = []
    sf = open_stockfish()
    try:
        for i, b in enumerate(boards):
            if stop():
                raise Cancelled()
            evals.append(run_stockfish(b, engine=sf))
            progress("engine", i + 1, len(boards), "Analysing positions")
    finally:
        sf.quit()
    if warm.is_alive():
        progress("engine", len(boards), len(boards), f"Loading {models[0]}")
        warm.join()

    labels = [compute_assessment(evals[i].score_cp, evals[i + 1].score_cp,
                                 evals[i].score_mate, evals[i + 1].score_mate,
                                 boards[i].turn == chess.WHITE,
                                 delivered_mate=boards[i + 1].is_checkmate())[0]
              for i in range(len(moves))]
    book = book_status(config.settings.book_path, boards, moves)
    openings = opening_notes(boards, moves, book, config.settings.book_path)

    want = {"white": [chess.WHITE], "black": [chess.BLACK],
            "both": [chess.WHITE, chess.BLACK]}[side]
    maia_engine = open_maia()
    concept_sf = None
    review = GameReview(headers=dict(game.headers), side=side, models=list(models),
                        start_fen=boards[0].fen(), moves=[])
    try:
        if every_move:
            picks = {i: "every-move mode" for i in range(len(moves))
                     if boards[i].turn in want and not book[i]}
        else:
            picks = pick_critical(boards, moves, evals, labels, book, want,
                                  maia_engine)

        for i, m in enumerate(moves):
            b = boards[i]
            n = b.fullmove_number
            san = b.san(m)
            ea: EngineAnalysis = evals[i + 1]
            verdict = eval_phrase(ea.score_cp, ea.score_mate)[0]
            if boards[i + 1].is_checkmate():
                verdict = "checkmate"
            try:
                best_uci = b.parse_san(evals[i].best_move).uci() if evals[i].best_move else ""
            except ValueError:
                best_uci = ""
            me_white = b.turn == chess.WHITE
            w0 = win_pct(evals[i].score_cp, evals[i].score_mate, me_white)
            w1 = (100.0 if boards[i + 1].is_checkmate() else
                  win_pct(ea.score_cp, ea.score_mate, me_white))
            notes = move_notes(b, m)
            mate = 1 if boards[i + 1].is_checkmate() else _mate_for(ea.score_mate, me_white)
            if mate and not boards[i + 1].is_checkmate():
                # Chessmaster's "Black has a mate in 8": the engine's, after the move.
                notes.insert(0, f"{side_name(b.turn)} can now force mate in {mate}" if mate > 0
                             else f"{side_name(not b.turn)} can now force mate in {-mate}")
            review.moves.append(MoveReview(
                ply=i, move_label=f"{n}. {san}" if b.turn == chess.WHITE else f"{n}...{san}",
                san=san, uci=m.uci(), color="white" if b.turn == chess.WHITE else "black",
                fen_before=b.fen(), fen_after=boards[i + 1].fen(),
                label="Book" if book[i] else labels[i],
                score_cp=ea.score_cp, score_mate=ea.score_mate, verdict=verdict,
                best_san=evals[i].best_move, best_uci=best_uci, book=book[i],
                reason=picks.get(i, ""), win_before=w0, win_after=w1,
                notes=notes, shapes=move_shapes(b, m), opening=openings[i].get("opening", {}),
                left_theory=openings[i].get("left_theory", {}),
                mate_before=_mate_for(evals[i].score_mate, me_white),
                mate_after=(1 if boards[i + 1].is_checkmate() else
                            _mate_for(ea.score_mate, me_white))))

        todo = sorted(picks)
        # One Stockfish for the engine-verified concepts of every moment.
        concept_sf = (open_stockfish() if config.settings.concepts and todo else None)
        for k, i in enumerate(todo):
            if stop():
                raise Cancelled()
            mv = review.moves[i]
            progress("narrate", k, len(todo), f"Writing comment on {mv.move_label}")
            maia = run_maia(boards[i], engine=maia_engine) if maia_engine else None
            note, prefix, focus = moment_context(i, picks[i], boards, moves, labels, book)
            prep = prepare_move(boards[i], moves[i], evals[i], evals[i + 1],
                                maia, opening_note=note, reason=picks[i],
                                fixed_prefix=prefix, focus=focus, engine=concept_sf)
            mv.key_facts = prep.get("key_facts", [])
            mv.plan = prep.get("plan") or {}
            c = prep.get("concepts")
            if c is not None and c.threat_before is not None:
                mv.threat = {"uci": c.threat_before.move.uci(),
                             "text": c.threat_before.text(),
                             "ignored": c.threat_ignored is not None}
            mv.themes = [t.to_dict() for t in move_themes(
                i, mv.move_label, mv.label, boards[i], moves[i], evals[i].best_move,
                prep.get("concepts"), prep.get("diffs"), evals[i + 1], mv.win_before)]
            if on_prompt:
                on_prompt(prep["prompt"])
            for model in models:
                r = narrate(model, prep)
                mv.annotations[model] = r["text"] or r["err"]
                if r.get("fallback"):
                    print(f"  {mv.move_label}: engine facts only ({r['fallback']})")
                mv.issues[model] = r["issues"]
                mv.stats[model] = {"secs": r["secs"], "first_issues": r["first_issues"],
                                   "removed": len(r.get("removed", []))}
        progress("narrate", len(todo), len(todo), "Done")
    finally:
        if maia_engine:
            maia_engine.quit()
        if concept_sf:
            concept_sf.quit()

    review.summary = [c.to_dict() for c in summarize(review)]
    review.seconds = time.time() - t_start
    progress("done", 1, 1, "Review complete")
    return review


def _opening_lines(review: GameReview) -> list:
    """The opening (the last name the game reached) and where it left theory."""
    named = [mv for mv in review.moves if mv.opening]
    out = []
    if named:
        o = named[-1].opening
        out.append(f"Opening: {o['eco']} {o['name']}.")
        idea = next((mv.opening.get("idea") for mv in reversed(named) if mv.opening.get("idea")), "")
        if idea:
            out.append(f"_{idea}_")
    left = next((mv for mv in review.moves if mv.left_theory), None)
    if left:
        num = left.move_label.split(".")[0] + ("..." if left.color == "black" else ". ")
        alts = " or ".join(num + a["san"] + (f" ({a['name']})" if a["name"] else "")
                           for a in left.left_theory["alternatives"])
        out.append(f"{left.move_label} left known theory"
                   + (f"; theory continues with {alts}." if alts else "."))
    return out + [""] if out else []


def write_markdown(review: GameReview, path: str) -> None:
    """The report: every annotated side's move with its label, then the comments."""
    h = review.headers
    want = {"white": ["white"], "black": ["black"], "both": ["white", "black"]}[review.side]
    out = [f"# {h.get('White', '?')} vs {h.get('Black', '?')} ({h.get('Result', '?')})", "",
           f"Annotated side: {review.side}. Model(s): {', '.join(review.models)}.", "",
           *_opening_lines(review),
           "## Move list", "",
           "| Move | Label | Note |", "|---|---|---|"]
    for mv in review.moves:
        if mv.color in want:
            note = "book" if mv.book else mv.reason
            out.append(f"| {mv.move_label} | {'—' if mv.book else mv.label} | {note} |")
    if review.summary:
        out += ["", "## Summary", "",
                "_Written by code from the stored facts of each move; open a line to "
                "see the facts it rests on._", ""]
        for c in review.summary:
            out.append(f"- {c['text']}")
            if c.get("facts"):
                out += ["  <details><summary>Facts behind this</summary>", ""]
                out += [f"  - {f}" for f in c["facts"]] + ["", "  </details>"]
    out += ["", "## Critical moments", ""]
    for mv in review.moments():
        out.append(f"### {mv.move_label} — {mv.label} ({mv.reason})")
        for model in review.models:
            if len(review.models) > 1:
                out.append(f"**{model}**")
            out.append(mv.annotations.get(model, ""))
            if mv.issues.get(model):
                out.append("> Check: " + "; ".join(mv.issues[model]))
            out.append("")
        if mv.plan:
            p = mv.plan
            out += [f"**Plan behind {p['move']}**: idea {p['idea']} ({p['idea_words']}). "
                    f"Played at once: {p['now_win']:.0f}% instead of {p['plan_win']:.0f}%"
                    + (f"; {p['problem_text']}" if p.get("problem_text") else "") + ". "
                    f"Line: {' '.join(p['outcome'])}.", ""]
        if mv.key_facts:
            out += ["<details><summary>Key facts given to the coach</summary>", ""]
            out += [f"- {k}" for k in mv.key_facts] + ["", "</details>", ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def annotate_game(pgn_path: str, side: str, models: list, verbose: bool,
                  every_move: bool = False) -> GameReview:
    """Command-line review: progress on the console, markdown report on disk."""
    game = load_game(pgn_path)
    h = game.headers
    n_plies = len(list(game.mainline_moves()))
    print(f"Game: {h.get('White', '?')} vs {h.get('Black', '?')}, {h.get('Result', '?')}, "
          f"{n_plies} plies. Annotating: {side}"
          f"{' (every move)' if every_move else ' (critical moments only)'}.")
    print(f"Stockfish: analysing {n_plies + 1} positions at depth {config.settings.depth}...")

    t0 = [time.time()]

    def progress(stage, done, total, message):
        if stage == "engine":
            print(f"\r  {done}/{total}", end="", flush=True)
            if done == total and message.startswith("Analysing"):
                print(f"  done in {time.time() - t0[0]:.0f}s")
            elif message.startswith("Loading"):
                print(f"Waiting for {models[0]} to finish loading...")

    review = review_game(game, side, models, every_move, progress=progress,
                         on_prompt=print if verbose else None)

    n_book = sum(m.book for m in review.moves)
    print(f"Opening book: {n_book} plies of known theory"
          + ("" if os.path.exists(config.settings.book_path or "") else
             " (no opening book configured — set book_path in the config file)"))
    print(f"Moments annotated: {len(review.moments())}")
    rows = []
    for mv in review.moments():
        for model in models:
            if model not in mv.stats:
                continue
            r = {"err": None, "issues": mv.issues[model], **mv.stats[model]}
            rows.append((mv.move_label, model, r))
            fixed = (f", retry {r['first_issues']}→{len(r['issues'])}"
                     if r["first_issues"] else "")
            print(f"{mv.move_label:<14}{mv.label:<12}{mv.reason:<36}"
                  f"{model:<14}{r['secs']:5.1f}s  issues {len(r['issues'])}{fixed}")

    if review.summary:
        print("\nSummary:")
        for c in review.summary:
            print(f"  - {c['text']}")
    if config.settings.keep_history:
        from . import history
        try:
            saved = history.save(review, config.settings.player_name)
            if saved:
                print(f"Stored for patterns across games ({saved.parent}); "
                      f"see `chesscoach patterns`.")
        except OSError as e:
            print(f"Could not store the review for patterns: {e}")

    out = os.path.splitext(pgn_path)[0] + "_annotated.md"
    write_markdown(review, out)
    print_scoreboard(rows)
    print(f"\nFull annotations written to: {out}")
    return review
