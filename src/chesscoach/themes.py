"""
Phase 5: what each of the player's errors got wrong, as stored facts.

A theme is a tag plus the verified fact behind it, computed by code from
the same engine and board data the comment was built from: never parsed
back out of the model's text. The game summary and the patterns across
games are built only from these records, so every summary claim traces to
a move and a fact.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

import chess

from .describe import PIECE_NAMES, describe_move, side_name

ERRORS = ("Inaccuracy", "Mistake", "Blunder")

# Outcomes rather than causes: shown for a move only when no cause explains
# it, and never counted as a pattern across games ("you lost material" in
# three games teaches nothing; what lost it does).
OUTCOMES = {"material"}
CAUSES_OF_MATERIAL = {"into_capture", "left_loose", "threat_ignored", "job_given_up",
                      "allowed_draw", "missed_forcing"}

# tag -> how the summary says it, after "N of your errors ..." / "... once"
TAGS = {
    "into_capture": "put a piece or pawn where the opponent's best reply takes it",
    "allowed_draw": "let a winning position slip to about equal",
    "threat_ignored": "did not deal with the opponent's threat",
    "job_given_up": "moved a piece that was holding something together",
    "left_loose": "left a pawn or piece hanging (no defender, and the engine's line takes it)",
    "king_shelter": "weakened your king's pawn shelter",
    "pawn_weakness": "left you with an isolated or doubled pawn",
    "opp_passed_pawn": "let the opponent get a passed pawn",
    "material": "ended up with less material than the best move would have",
    "castling": "gave up the right to castle",
    "missed_forcing": "missed a better capture or check",
}


@dataclass
class Theme:
    tag: str
    ply: int
    move_label: str
    label: str              # Inaccuracy / Mistake / Blunder
    fact: str               # the verified fact, in words

    def to_dict(self) -> dict:
        return asdict(self)


def _forcing(board: chess.Board, move: chess.Move) -> bool:
    return board.is_capture(move) or board.gives_check(move)


def move_themes(ply: int, move_label: str, label: str, board_before: chess.Board,
                played: chess.Move, best_san: str, concepts=None,
                diffs: Optional[list] = None, eng_after=None,
                win_before: Optional[float] = None) -> list:
    """The themes of one error. `concepts` (Tier 2) and `diffs`
    (features.line_diff) come from the same preparation as the comment."""
    if label not in ERRORS:
        return []
    me = board_before.turn
    mine, theirs = side_name(me), side_name(not me)
    out = []

    def add(tag, fact):
        if not any(t.tag == tag for t in out):
            out.append(Theme(tag, ply, move_label, label, fact.rstrip(".") + "."))

    after = board_before.copy(stack=False)
    after.push(played)
    mat_lost = any(d.key == "material" and d.favours == "engine" and d.caused
                   for d in diffs or [])
    # The opponent's best reply takes what was just moved (6...Bxd4 7.Nxd4,
    # 55.b7 Rxb7): the commonest club blunder, said plainly.
    reply = None
    if eng_after is not None and eng_after.best_line:
        try:
            reply = after.parse_san(eng_after.best_line[0])
        except ValueError:
            reply = None
    if (reply is not None and mat_lost and after.is_capture(reply)
            and reply.to_square == played.to_square):
        p = after.piece_at(played.to_square)
        add("into_capture", f"{theirs}'s best reply takes the {PIECE_NAMES[p.piece_type]} "
            f"on {chess.square_name(played.to_square)}: {describe_move(after, reply)}")
    # Winning before, about equal after (55.b7 in a won rook ending).
    if eng_after is not None and win_before is not None and win_before >= 80:
        from .assessment import win_pct
        w = win_pct(eng_after.score_cp, eng_after.score_mate, me == chess.WHITE)
        if w is not None and 40 <= w <= 60:
            add("allowed_draw", f"Before the move {mine}'s winning chance was "
                f"{win_before:.0f}%; after it, {w:.0f}%")

    if concepts is not None:
        if concepts.threat_ignored is not None:
            add("threat_ignored", "Before the move, " + concepts.threat_ignored.text()
                + "; it was still the opponent's best move afterwards")
        if concepts.job_given_up is not None:
            r = concepts.job_given_up
            add("job_given_up", f"The {PIECE_NAMES[r.piece.piece_type]} on "
                f"{chess.square_name(r.square)} was holding something together: "
                f"without it, {r.stops}")
        after = board_before.copy(stack=False)
        after.push(played)
        for sq in concepts.left_loose:
            p = after.piece_at(sq)
            if p:
                add("left_loose", f"After the move the {PIECE_NAMES[p.piece_type]} on "
                    f"{chess.square_name(sq)} had no defender, and the engine's line "
                    f"captures it")
                break

    for d in diffs or []:
        if d.favours != "engine" or not d.caused:
            continue
        fact = (f"{d.key}: after the move → {d.played}; after the engine's choice "
                f"→ {d.engine}")
        if d.key in (f"{mine}'s king shelter",
                     f"files with no {mine} pawn next to {mine}'s king"):
            add("king_shelter", fact)
        elif d.key in (f"{mine}'s isolated pawns", f"{mine}'s doubled pawns"):
            add("pawn_weakness", fact)
        elif d.key == f"{theirs}'s passed pawns":
            add("opp_passed_pawn", fact)
        elif d.key == "material":
            add("material", fact)
    if any(t.tag in CAUSES_OF_MATERIAL for t in out):
        out = [t for t in out if t.tag != "material"]
    # A move that drops material is about that; pawn details measured four
    # moves down the line are noise beside it.
    if any(t.tag in ("into_capture", "left_loose") for t in out):
        out = [t for t in out if t.tag not in ("pawn_weakness", "opp_passed_pawn")]

    best = None
    if best_san:
        try:
            best = board_before.parse_san(best_san)
        except ValueError:
            best = None
    if best is not None and best != played:
        if _forcing(board_before, best) and not _forcing(board_before, played):
            add("missed_forcing", "The engine's choice was forcing: "
                + describe_move(board_before, best))
        had = board_before.has_castling_rights(me)
        after = board_before.copy(stack=False)
        after.push(played)
        alt = board_before.copy(stack=False)
        alt.push(best)
        if (had and not board_before.is_castling(played)
                and not after.has_castling_rights(me) and alt.has_castling_rights(me)):
            add("castling", f"{mine} could still castle before the move and could not "
                "afterwards; the engine's choice kept the right")
    return out
