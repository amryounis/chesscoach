"""Tactical facts: attacked pieces, pins, mate threats."""

from __future__ import annotations


import chess

from .describe import describe_move, describe_move_effects, piece_label, side_name
from .engine import EngineAnalysis
from .facts import castling_lines, loose_piece_lines


def mate_in_one_moves(board: chess.Board) -> list:
    """All moves that deliver immediate checkmate for the side to move."""
    found = []
    for m in board.legal_moves:
        board.push(m)
        if board.is_checkmate():
            found.append(m)
        board.pop()
    return found


def extract_tactical_features(board: chess.Board,
                              engine: EngineAnalysis) -> str:
    """
    Verified tactical facts so the LLM never has to calculate.

    v5 changes:
      - Every attacked piece lists its attackers AND defenders by name,
        including 'defended by: nobody'. No more vague 'under-defended'.
      - Explicit negative facts ('No pieces are pinned', 'No mate threats')
        so the LLM can't fill silence with invented pins or mates.
      - Engine's best move described in words only — no algebraic notation.
    """
    facts = []

    # --- Attacked pieces, with named attackers and defenders ---
    attacked_lines = []
    for sq, p in sorted(board.piece_map().items()):
        if p.piece_type == chess.KING:
            continue
        attackers = board.attackers(not p.color, sq)
        if not attackers:
            continue
        defenders = board.attackers(p.color, sq)
        atk = ", ".join(piece_label(board, a) for a in attackers)
        dfn = (", ".join(piece_label(board, d) for d in defenders)
               if defenders else "NOBODY")
        tag = " [HANGING]" if not defenders else ""
        attacked_lines.append(
            f"- {piece_label(board, sq)}{tag}\n"
            f"    attacked by: {atk}\n"
            f"    defended by: {dfn}")
    if attacked_lines:
        facts.append("Attacked pieces (complete list — any piece not listed "
                     "here is not attacked):\n" + "\n".join(attacked_lines))
    else:
        facts.append("No pieces are attacked.")

    # --- Pins (always stated, even when empty) ---
    pins = []
    for color in (chess.WHITE, chess.BLACK):
        king_sq = board.king(color)
        for sq, p in board.piece_map().items():
            if p.color != color or p.piece_type == chess.KING:
                continue
            if board.is_pinned(color, sq):
                mask = board.pin(color, sq)
                pinner = next((s for s in mask
                               if s not in (sq, king_sq)
                               and (q := board.piece_at(s))
                               and q.color != color), None)
                by = f" by the {piece_label(board, pinner)}" if pinner is not None else ""
                pins.append(f"- {piece_label(board, sq)} is pinned to its king{by}")
    facts.append("Pins:\n" + "\n".join(pins) if pins
                 else "Pins: none. No piece on the board is pinned.")

    # --- Mate threats (always stated) ---
    mover = side_name(board.turn)
    other = side_name(not board.turn)
    mate_lines = []
    m1 = mate_in_one_moves(board)
    if m1:
        mate_lines.append(f"{mover} (to move) has checkmate in one: "
                          + "; ".join(describe_move(board, m) for m in m1))
    if not board.is_check():
        nb = board.copy()
        nb.push(chess.Move.null())
        threats = mate_in_one_moves(nb)
        if threats:
            mate_lines.append(f"{other} is threatening checkmate: "
                              + "; ".join(describe_move(nb, m) for m in threats))
    facts.append("Mate threats:\n" + "\n".join(f"- {l}" for l in mate_lines)
                 if mate_lines else
                 "Mate threats: none. Neither side threatens checkmate.")

    # --- Castling rights and undefended pieces (always stated) ---
    facts.append("Castling:\n" + "\n".join(f"- {l}" for l in castling_lines(board)))
    facts.append("Undefended pieces (no defender, attacked or not):\n"
                 + "\n".join(f"- {l}" for l in loose_piece_lines(board)))

    # --- In check? ---
    if board.is_check():
        checkers = ", ".join(piece_label(board, s) for s in board.checkers())
        facts.append(f"{mover}'s king is in CHECK from: {checkers}")

    # --- Engine's best move from this position, in words ---
    if engine.best_line:
        try:
            mv = board.parse_san(engine.best_line[0])
            lines = [describe_move(board, mv)]
            lines += describe_move_effects(board, mv)
            facts.append("Engine's top choice from this position:\n"
                         + "\n".join(f"- {l}" for l in lines))
        except ValueError:
            pass

    return "\n\n".join(facts)
