"""
Accuracy %, by Lichess's published method (lila: AccuracyPercent.scala),
re-implemented here from its description.

Per move: how much of the mover's winning chance it kept,
    103.1668 * exp(-0.04354 * drop) - 3.1669  (+1, Lichess's allowance for
    imperfect analysis), clamped to 0..100; a move that loses nothing is 100.
Per side: the average of a volatility-weighted mean (moves in sharp
stretches of the game count more) and a harmonic mean (one blunder pulls it
down hard), the two Lichess uses so that neither a few blunders in a quiet
game nor a long won ending dominate.

Winning chances are the same curve the review uses for its labels
(assessment.win_pct), so "accuracy" and "Mistake" agree on what a move cost.
It is a measure of how close the moves were to the engine's, not a rating:
chesscoach deliberately shows no Elo estimate from one game.
"""

from __future__ import annotations

import math
from typing import Optional


def move_accuracy(before: float, after: float) -> float:
    """Accuracy of one move from the mover's winning chance before/after (%)."""
    if after >= before:
        return 100.0
    raw = 103.1668100711649 * math.exp(-0.04354415386753951 * (before - after)) - 3.166924740191411
    return max(0.0, min(100.0, raw + 1))


def _stdev(xs: list) -> float:
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def game_accuracy(white_wins: list, white_first: bool = True) -> dict:
    """{"white": %, "black": %} (None for a side with no moves) from White's
    winning chance at every position of the game, start included.
    `white_first`: whether White made the first move (False from a set-up
    position with Black to move)."""
    wins = [50.0 if w is None else float(w) for w in white_wins]
    n = len(wins) - 1                         # moves
    out = {"white": None, "black": None}
    if n < 1:
        return out
    size = max(2, min(8, n // 10))
    windows = [wins[:size]] * max(0, min(size, len(wins)) - 2)
    windows += [wins[i:i + size] for i in range(0, len(wins) - size + 1)]
    weights = [max(0.5, min(12.0, _stdev(w))) for w in windows]
    per = {"white": [], "black": []}
    for i in range(n):
        white = (i % 2 == 0) == white_first
        prev, nxt = wins[i], wins[i + 1]
        acc = move_accuracy(prev, nxt) if white else move_accuracy(100 - prev, 100 - nxt)
        w = weights[i] if i < len(weights) else weights[-1]
        per["white" if white else "black"].append((acc, w))
    for color, items in per.items():
        if not items:
            continue
        weighted = sum(a * w for a, w in items) / sum(w for _, w in items)
        harmonic = len(items) / sum(1 / max(a, 1.0) for a, _ in items)
        out[color] = round((weighted + harmonic) / 2, 1)
    return out


def describe(acc: Optional[float]) -> str:
    return "?" if acc is None else f"{acc:.0f}%"
