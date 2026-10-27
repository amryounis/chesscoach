"""Which moves deserve a comment, and the context explaining why."""

from __future__ import annotations


import chess

from .assessment import win_pct
from .describe import describe_move, describe_move_effects, piece_label, side_name
from .engine import run_maia


def is_natural(board: chess.Board, move: chess.Move, maia_engine=None) -> bool:
    """
    Would a club player find this move without trying? Then finding it
    isn't worth praise. Maia decides when available; otherwise a simple
    rule: capturing something worth clearly more than the capturing piece.
    """
    if maia_engine is not None:
        pred = run_maia(board, engine=maia_engine)
        if pred.predicted_move:
            return pred.predicted_move == board.san(move)
    if board.is_capture(move) and not board.is_en_passant(move):
        vals = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
                chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
        cap = board.piece_at(move.to_square)
        mover = board.piece_at(move.from_square)
        if cap and mover and vals[cap.piece_type] >= vals[mover.piece_type] + 2:
            return True
    return False


LEFT_BOOK = "first move out of the opening book"
LEFT_NAMED = "first move out of the named opening lines"


def pick_critical(boards, moves, evals, labels, book, want,
                  maia_engine=None, theory: dict = None) -> dict:
    """
    Which of the annotated side's moves deserve a comment, and why.
    A real annotator skips the routine moves; so do we.
      - the move that leaves opening theory, only when `theory` (from
        opening.theory_exit) has evidence for it
      - Inaccuracy / Mistake / Blunder
      - found the only good move (best line >= 20 win% above the 2nd best)
      - punished the opponent's Mistake/Blunder with the best move
      - delivered checkmate
    """
    picks = {}
    theory = theory or {}
    first_out = theory.get("ply")
    for i, m in enumerate(moves):
        if boards[i].turn not in want or book[i]:
            continue
        lab = labels[i]
        eb = evals[i]
        is_white = boards[i].turn == chess.WHITE
        played_best = eb.best_move == boards[i].san(m)
        if boards[i + 1].is_checkmate():
            picks[i] = "delivered checkmate"
        elif lab in ("Inaccuracy", "Mistake", "Blunder"):
            picks[i] = lab.lower()
        elif played_best and eb.alt_scores:
            w1 = win_pct(eb.score_cp, eb.score_mate, is_white)
            w2 = win_pct(*eb.alt_scores[0], is_white)
            if (w1 is not None and w2 is not None and w1 - w2 >= 20
                    and not is_natural(boards[i], m, maia_engine)):
                picks[i] = "found the only good move"
        if i not in picks and i > 0 and labels[i - 1] in ("Mistake", "Blunder") \
                and played_best:
            picks[i] = "punished the opponent's mistake"
        if i == first_out and i not in picks:
            picks[i] = LEFT_BOOK if theory.get("source") == "book" else LEFT_NAMED
    return picks


def moment_context(i: int, reason: str, boards: list, moves: list,
                   labels: list, book: list, theory: dict = None) -> tuple:
    """
    Verified context explaining WHY this moment was picked, so the model
    explains the real point instead of padding.
    Returns (note for the prompt, prefix for the code-written sentence,
    extra instruction for 'What happened').
    """
    note, prefix, focus = "", "", ""
    theory = theory or {}
    if theory.get("ply") == i and theory.get("source") == "book":
        prefix = "This is where the game left known opening theory. "
        note += ("\nOPENING (verified): the moves before this one are in the "
                 "opening book; this move leaves it.")
    elif theory.get("ply") == i and theory.get("source") == "names":
        name = theory.get("opening", "")
        prefix = "This is where the game left the named opening lines. "
        note += ("\nOPENING (verified): the position before this move is a named "
                 f"opening position{f' ({name})' if name else ''}; after this move "
                 "the position is no longer a named one. Do not claim anything "
                 "about opening theory beyond that.")
    if reason == "punished the opponent's mistake" and i > 0:
        prev_b, prev_m = boards[i - 1], moves[i - 1]
        opp = side_name(prev_b.turn)
        lines = [describe_move(prev_b, prev_m)]
        lines += describe_move_effects(prev_b, prev_m)
        # What the opponent left en prise, as of right before this move
        b = boards[i]
        loose = [piece_label(b, sq) for sq, p in b.piece_map().items()
                 if p.color == prev_b.turn and p.piece_type != chess.KING
                 and b.attackers(not p.color, sq)
                 and not b.attackers(p.color, sq)]
        note += (f"\nTHE OPPONENT'S MISTAKE (verified): {opp}'s previous move "
                 f"was a {labels[i - 1]}.\n"
                 + "\n".join(f"- {l}" for l in lines)
                 + ("\n- Right before this move, these pieces of " + opp
                    + " were attacked and undefended: " + ", ".join(loose)
                    if loose else ""))
        focus = (f"Explain how this move takes advantage of {opp}'s mistake, "
                 "using THE OPPONENT'S MISTAKE.")
    if reason == "found the only good move":
        focus = "Explain why this move was hard to find but necessary."
    return note, prefix, focus
