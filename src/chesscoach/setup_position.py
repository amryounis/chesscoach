"""
Setting up a position by hand (Review → Setup): what is wrong with it, in
plain words, and what the castling and en passant boxes may offer.

The dialog sends the FEN it builds (pieces, side to move, the castling boxes
ticked, the en passant square, the move number); this says whether a game
could reach it, as python-chess judges it, and cleans what cannot be:
castling with a king or rook moved off its square, an en passant square
with no pawn that just moved two squares.
"""

from __future__ import annotations

import chess

CASTLING = {"K": (chess.E1, chess.H1, chess.WHITE), "Q": (chess.E1, chess.A1, chess.WHITE),
            "k": (chess.E8, chess.H8, chess.BLACK), "q": (chess.E8, chess.A8, chess.BLACK)}

PROBLEMS = [
    (chess.STATUS_EMPTY, "The board is empty."),
    (chess.STATUS_NO_WHITE_KING, "White has no king."),
    (chess.STATUS_NO_BLACK_KING, "Black has no king."),
    (chess.STATUS_TOO_MANY_KINGS, "Each side has exactly one king; here one has more."),
    (chess.STATUS_TOO_MANY_WHITE_PAWNS, "White has more than eight pawns."),
    (chess.STATUS_TOO_MANY_BLACK_PAWNS, "Black has more than eight pawns."),
    (chess.STATUS_PAWNS_ON_BACKRANK, "A pawn is on the first or the last rank, where pawns can't stand."),
    (chess.STATUS_TOO_MANY_WHITE_PIECES, "White has more than sixteen pieces."),
    (chess.STATUS_TOO_MANY_BLACK_PIECES, "Black has more than sixteen pieces."),
    (chess.STATUS_TOO_MANY_CHECKERS, "The king is in check from more than two pieces, which no move can do."),
    (chess.STATUS_IMPOSSIBLE_CHECK, "This check couldn't come from a move (two pieces on one line give it)."),
]


def castling_possible(board: chess.Board) -> dict:
    """{"K": bool, ...}: king and rook on their starting squares."""
    out = {}
    for flag, (k, r, color) in CASTLING.items():
        kp, rp = board.piece_at(k), board.piece_at(r)
        out[flag] = bool(kp and kp.piece_type == chess.KING and kp.color == color
                         and rp and rp.piece_type == chess.ROOK and rp.color == color)
    return out


def ep_squares(board: chess.Board) -> list:
    """The squares the side to move could take en passant on: an enemy pawn
    that may just have moved two squares, with a pawn of ours beside it."""
    out = []
    me = board.turn
    rank = 4 if me == chess.WHITE else 3                 # the enemy pawn's rank
    for f in range(8):
        sq = chess.square(f, rank)
        p = board.piece_at(sq)
        if not p or p.piece_type != chess.PAWN or p.color == me:
            continue
        behind = chess.square(f, rank + (1 if me == chess.WHITE else -1))
        start = chess.square(f, rank + (2 if me == chess.WHITE else -2))
        if board.piece_at(behind) or board.piece_at(start):
            continue
        beside = [chess.square(g, rank) for g in (f - 1, f + 1) if 0 <= g <= 7]
        if any((q := board.piece_at(b)) and q.piece_type == chess.PAWN and q.color == me
               for b in beside):
            out.append(chess.square_name(behind))
    return out


def check(fen: str) -> dict:
    """{ok, problems, fen, castling_possible, ep_squares}: `fen` cleaned of
    castling and en passant that cannot be, and what is still wrong."""
    try:
        board = chess.Board(fen)
    except ValueError as e:
        return {"ok": False, "problems": [f"That isn't a position: {e}."], "fen": fen,
                "castling_possible": {k: False for k in CASTLING}, "ep_squares": []}
    possible = castling_possible(board)
    rights = "".join(f for f in "KQkq" if possible[f] and board.has_castling_rights(
        CASTLING[f][2]) and _has(board, f))
    eps = ep_squares(board)
    ep = chess.square_name(board.ep_square) if board.ep_square is not None else ""
    if ep not in eps:
        ep = ""
    parts = board.fen().split()
    parts[2] = rights or "-"
    parts[3] = ep or "-"
    clean = " ".join(parts)
    board = chess.Board(clean)
    status = board.status()
    problems = [text for flag, text in PROBLEMS if status & flag]
    if status & chess.STATUS_EMPTY:
        problems = ["The board is empty: put the two kings on it, at least."]
    if status & chess.STATUS_OPPOSITE_CHECK:
        other = "Black" if board.turn == chess.WHITE else "White"
        mover = "White" if board.turn == chess.WHITE else "Black"
        problems.append(f"{other}'s king is in check, but it is {mover}'s move: "
                        f"make it {other}'s move, or take the check away.")
    if not problems and board.is_game_over():
        res = board.outcome()
        what = ("checkmate" if board.is_checkmate() else "stalemate" if board.is_stalemate()
                else "a draw: neither side can mate")
        problems.append(f"Note: this is {what}; there is nothing to play.")
        return {"ok": True, "problems": problems, "fen": clean, "over": True,
                "castling_possible": possible, "ep_squares": eps, "result": res.result()}
    return {"ok": not problems, "problems": problems, "fen": clean,
            "castling_possible": possible, "ep_squares": eps}


def _has(board: chess.Board, flag: str) -> bool:
    k, r, color = CASTLING[flag]
    return bool(board.castling_rights & chess.BB_SQUARES[r])
