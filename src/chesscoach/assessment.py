"""Move labels and evaluation wording, computed from engine scores."""

from __future__ import annotations

import math
from typing import Optional


def win_pct(score_cp: Optional[int], score_mate: Optional[int],
            for_white: bool) -> Optional[float]:
    """
    Winning chances (0-100) for one side, from a White-view engine score.
    Same curve Lichess uses for its move judgments. Mate maps to 100 / 0.
    """
    if score_mate is not None:
        white_mates = score_mate > 0
        return 100.0 if white_mates == for_white else 0.0
    if score_cp is None:
        return None
    cp = score_cp if for_white else -score_cp
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def compute_assessment(prev_score_cp: Optional[int], curr_score_cp: Optional[int],
                       prev_mate: Optional[int], curr_mate: Optional[int],
                       is_white_move: bool,
                       delivered_mate: bool = False) -> tuple:
    """
    Move label from the drop in the mover's winning chances (not raw
    centipawns). Raw cp misjudges won positions: +9 -> +6 is a 300 cp
    'loss' but the game is still won. Winning chances barely move there.

    Thresholds on win% drop (Lichess: 10/20/30 for ?!/?/??):
      < 5  Excellent | < 10 Good | < 20 Inaccuracy | < 30 Mistake | else Blunder

    Returns (label, cp_loss). cp_loss is for display only; None when a
    mate score is involved.
    """
    if delivered_mate:
        return ("Excellent", 0)

    before = win_pct(prev_score_cp, prev_mate, is_white_move)
    after = win_pct(curr_score_cp, curr_mate, is_white_move)
    if before is None or after is None:
        return ("Unknown", None)

    cp_loss = None
    if prev_score_cp is not None and curr_score_cp is not None:
        diff = (prev_score_cp - curr_score_cp) if is_white_move \
            else (curr_score_cp - prev_score_cp)
        cp_loss = max(0, diff)

    drop = before - after
    if drop < 5:
        label = "Excellent"
    elif drop < 10:
        label = "Good"
    elif drop < 20:
        label = "Inaccuracy"
    elif drop < 30:
        label = "Mistake"
    else:
        label = "Blunder"
    return (label, cp_loss)


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
