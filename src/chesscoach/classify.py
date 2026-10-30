"""
Every move of a reviewed game in one of Lucas Chess's classes, by checkable
rules, and the counts per side and per phase.

  !!  brilliant    the only good move (the next best keeps 20+ points of
                   winning chance less), not one a club player plays by
                   instinct (Maia, or a plain winning capture), and a
                   sacrifice: the line gives up material and wins it back
                   with interest, or mates
  !   good         the only good move, not instinctive, not a sacrifice
  !?  interesting  a sacrifice that is not the engine's first choice but
                   keeps the evaluation (Excellent or Good)
  best             the engine's first choice
  ok               acceptable: within 10 points of winning chance of it
  ?!  ?  ??        inaccuracy, mistake, blunder (the review's labels)
  book             opening book
A delivered mate is best (or ! when it was the only win). Sacrifices are
found by motifs.line_motifs, which only reports one the line pays off.
!! and !? need a game still to be decided: the mover's winning chance
before the move between 10% and 90% (a "sacrifice" in a lost or long won
position is neither brilliant nor interesting).
"""

from __future__ import annotations

from typing import Optional

import chess

from .accuracy import move_accuracy
from .assessment import win_pct
from .motifs import line_motifs
from .moments import is_natural
from .summary import phase_of

CLASSES = ("!!", "!", "!?", "best", "ok", "?!", "?", "??", "book")
WORDS = {"!!": "Brilliant", "!": "Good", "!?": "Interesting", "best": "Best", "ok": "Acceptable",
         "?!": "Inaccuracies", "?": "Mistakes", "??": "Blunders", "book": "Book"}
NAG = {"!!": 3, "!": 1, "!?": 5, "?!": 6, "?": 2, "??": 4}
_FROM_LABEL = {"Inaccuracy": "?!", "Mistake": "?", "Blunder": "??"}
ONLY_MOVE = 20.0                       # points of winning chance to the second best


def _only_move(ea, white: bool) -> bool:
    if not ea.alt_scores:
        return False
    w1 = win_pct(ea.score_cp, ea.score_mate, white)
    w2 = win_pct(*ea.alt_scores[0], white)
    return w1 is not None and w2 is not None and w1 - w2 >= ONLY_MOVE


def classify(board: chess.Board, move: chess.Move, label: str, book: bool,
             ea_before, ea_after, maia_engine=None) -> str:
    if book:
        return "book"
    if label in _FROM_LABEL:
        return _FROM_LABEL[label]
    white = board.turn == chess.WHITE
    san = board.san(move)
    w0 = win_pct(ea_before.score_cp, ea_before.score_mate, white)
    open_game = w0 is not None and 10 <= w0 <= 90
    played_best = ea_before.best_move == san
    after = board.copy(stack=False)
    after.push(move)
    line = [san] + list(ea_after.best_line or [])
    sac = open_game and not after.is_game_over() and \
        "sacrifice" in line_motifs(board, line, ea_after.score_mate)
    if played_best and _only_move(ea_before, white) and not is_natural(board, move, maia_engine):
        return "!!" if sac else "!"
    if sac and not played_best:
        return "!?"
    return "best" if played_best else "ok"


def _accuracy(values: list) -> Optional[float]:
    """Mean of the arithmetic and harmonic means of move accuracies: the
    Lichess idea without its volatility weights (too few moves per phase)."""
    if not values:
        return None
    mean = sum(values) / len(values)
    harmonic = len(values) / sum(1 / max(v, 1.0) for v in values)
    return round((mean + harmonic) / 2, 1)


def stats(moves: list) -> dict:
    """{"white": {...}, "black": {...}}: counts per class, best-move % and
    accuracy overall and per phase. `moves`: MoveReview objects or dicts
    with color, mark, label, book, fen_before, win_before, win_after."""
    g = (lambda m, k: m.get(k) if isinstance(m, dict) else getattr(m, k, None))
    out = {}
    for color in ("white", "black"):
        mine = [m for m in moves if g(m, "color") == color]
        counts = {c: sum(1 for m in mine if g(m, "mark") == c) for c in CLASSES}
        phases = {}
        for ph in ("opening", "middlegame", "endgame"):
            pm = [m for m in mine if not g(m, "book") and phase_of(g(m, "fen_before")) == ph]
            acc = [move_accuracy(g(m, "win_before"), g(m, "win_after")) for m in pm
                   if g(m, "win_before") is not None and g(m, "win_after") is not None]
            best = sum(1 for m in pm if g(m, "mark") in ("!!", "!", "best"))
            phases[ph] = {"moves": len(pm), "best": best,
                          "best_pct": round(100 * best / len(pm)) if pm else None,
                          "accuracy": _accuracy(acc)}
        analysed = [m for m in mine if not g(m, "book")]
        best = sum(1 for m in analysed if g(m, "mark") in ("!!", "!", "best"))
        out[color] = {"counts": counts, "moves": len(mine), "analysed": len(analysed),
                      "best": best, "best_pct": round(100 * best / len(analysed)) if analysed else None,
                      "phases": phases}
    return out
