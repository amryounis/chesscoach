"""Move labels and evaluation wording, computed from engine scores."""

from __future__ import annotations

import math
from typing import Optional


def win_pct(score_cp: Optional[int], score_mate: Optional[int],
            for_white: bool) -> Optional[float]:
    """
    Winning chances (0-100) for one side, from a White-view engine score.
    Same curve Lichess uses for its move judgments, with the score capped at
    10 pawns as Lichess does. Mate maps to 100 / 0.
    """
    if score_mate is not None:
        white_mates = score_mate > 0
        return 100.0 if white_mates == for_white else 0.0
    if score_cp is None:
        return None
    cp = score_cp if for_white else -score_cp
    cp = max(-1000, min(1000, cp))                    # as Lichess: cp.ceiled
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def compute_assessment(prev_score_cp: Optional[int], curr_score_cp: Optional[int],
                       prev_mate: Optional[int], curr_mate: Optional[int],
                       is_white_move: bool,
                       delivered_mate: bool = False) -> tuple:
    """
    Move label as Lichess judges a move (lila, modules/analyse Advice.scala).

    From the drop in the mover's winning chances (not raw centipawns: +9 ->
    +6 is a 300 cp 'loss' but the game is still won). Lichess's thresholds
    are .1 / .2 / .3 on its -1..1 scale of winning chances, which is 5 / 10 /
    15 points of win% (0-100) here:
      < 2  Excellent | < 5 Good | < 10 Inaccuracy | < 15 Mistake | else Blunder
    (Lichess has no words below an inaccuracy; Excellent / Good are ours.)

    A mate that appears or goes away is judged as Lichess does, by the score
    on the other side of it:
      allowing a mate:  Inaccuracy when the mover was already more than 10
                        pawns down, Mistake when more than 7, else Blunder
      losing your mate: Inaccuracy when still more than 10 pawns up, Mistake
                        when more than 7, else Blunder

    Returns (label, cp_loss). cp_loss is for display only; None when a
    mate score is involved.
    """
    if delivered_mate:
        return ("Excellent", 0)

    cp_loss = None
    if prev_score_cp is not None and curr_score_cp is not None:
        diff = (prev_score_cp - curr_score_cp) if is_white_move \
            else (curr_score_cp - prev_score_cp)
        cp_loss = max(0, diff)

    sign = 1 if is_white_move else -1          # the mover's point of view
    prev_mine = prev_mate is not None and prev_mate * sign > 0
    curr_theirs = curr_mate is not None and curr_mate * sign < 0
    curr_mine = curr_mate is not None and curr_mate * sign > 0
    if prev_mate is None and curr_theirs and prev_score_cp is not None:      # allowed a mate
        pov = prev_score_cp * sign
        return ("Inaccuracy" if pov < -999 else "Mistake" if pov < -700 else "Blunder", cp_loss)
    if prev_mine and not curr_mine:                                          # lost the mate
        pov = (curr_score_cp or 0) * sign if curr_mate is None else 0
        return ("Inaccuracy" if pov > 999 else "Mistake" if pov > 700 else "Blunder", cp_loss)

    before = win_pct(prev_score_cp, prev_mate, is_white_move)
    after = win_pct(curr_score_cp, curr_mate, is_white_move)
    if before is None or after is None:
        return ("Unknown", None)
    return (label_for_drop(before - after), cp_loss)


# Lichess's .1 / .2 / .3 (winning chances, -1..1) as points of win% (0-100)
INACCURACY, MISTAKE, BLUNDER = 5, 10, 15


def label_for_drop(drop: float) -> str:
    """Move label from the drop in the mover's winning chances (%), with
    Lichess's thresholds (see compute_assessment)."""
    if drop < 2:
        return "Excellent"
    if drop < INACCURACY:
        return "Good"
    if drop < MISTAKE:
        return "Inaccuracy"
    if drop < BLUNDER:
        return "Mistake"
    return "Blunder"


def eval_phrase(score_cp: Optional[int], score_mate: Optional[int]) -> tuple:
    """
    Map an engine score (White's view) to fixed wording.
    Returns (phrase, is_decisive). Bands, in pawns:
      <= 0.30 equal | <= 0.80 slightly better | <= 2.00 clearly better
      <= 5.00 much better | > 5.00 or mate: winning
    """
    if score_mate is not None:
        side = "White" if score_mate > 0 else "Black"
        return (f"{side} has a forced checkmate", True)
    if score_cp is None:
        return ("unclear", False)
    side = "White" if score_cp > 0 else "Black"
    a = abs(score_cp)
    if a <= 30:
        return ("the position is roughly equal", False)
    if a <= 80:
        return (f"{side} is slightly better", False)
    if a <= 200:
        return (f"{side} is clearly better", False)
    if a <= 500:
        return (f"{side} is much better", False)
    return (f"{side} is winning", True)
