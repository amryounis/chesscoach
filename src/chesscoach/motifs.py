"""
Tactical motifs in an engine line: the tags Lichess puts on its puzzles
(fork, pin, skewer, hanging piece, mate...), found by code on the board.

For every error the review tags two lines:
  allowed  the opponent's best line after the move: what the error let in
  missed   the engine's line instead of the move: what the player passed up
Lucas Chess does the same from Lichess's puzzle tagger. That tagger is
AGPL; these detectors are written from the motif definitions, not from its
code, and they are deliberately conservative: a motif is only reported
when the line itself shows it paying off (the forked piece is taken, the
skewered piece behind is taken), so a tag is a fact about the line, not a
guess.
"""

from __future__ import annotations

from typing import Optional

import chess

VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5,
          chess.QUEEN: 9, chess.KING: 0}

# tag -> words, in the order they are listed
MOTIFS = {
    "mate": "a forced mate",
    "back_rank_mate": "a back-rank mate",
    "double_check": "a double check",
    "fork": "a fork",
    "pin": "a pin",
    "skewer": "a skewer",
    "discovered_attack": "a discovered attack",
    "hanging_piece": "a hanging piece",
    "capturing_defender": "taking a defender",
    "sacrifice": "a sacrifice",
    "promotion": "a promotion",
    "advanced_pawn": "an advanced pawn",
}

PLIES = 6            # how far into the line to look


def _material(board: chess.Board, color: bool) -> int:
    return sum(VALUES[p.piece_type] for p in board.piece_map().values() if p.color == color)


def _balance(board: chess.Board, color: bool) -> int:
    return _material(board, color) - _material(board, not color)


def _captures_later(boards: list, moves: list, start: int, squares: set, by: bool) -> bool:
    """Does side `by` capture one of the pieces on `squares` within the rest
    of the line, there or wherever it has moved on to? (A forked bishop
    that takes the forking pawn and is then won on e5 is still won.)"""
    tracked = set(squares)
    for j in range(start, len(moves)):
        b, m = boards[j], moves[j]
        if b.turn == by:
            if b.is_capture(m) and m.to_square in tracked:
                return True
        elif m.from_square in tracked:
            tracked.discard(m.from_square)
            tracked.add(m.to_square)
    return False


def _passed(board: chess.Board, sq: int, color: bool) -> bool:
    f, r = chess.square_file(sq), chess.square_rank(sq)
    for e in board.pieces(chess.PAWN, not color):
        ef, er = chess.square_file(e), chess.square_rank(e)
        if abs(ef - f) <= 1 and (er > r if color == chess.WHITE else er < r):
            return False
    return True


def _beyond(board: chess.Board, frm: int, through: int) -> Optional[int]:
    """The first occupied square past `through` on the line from `frm`."""
    df = (chess.square_file(through) > chess.square_file(frm)) - (chess.square_file(through) < chess.square_file(frm))
    dr = (chess.square_rank(through) > chess.square_rank(frm)) - (chess.square_rank(through) < chess.square_rank(frm))
    f, r = chess.square_file(through) + df, chess.square_rank(through) + dr
    while 0 <= f < 8 and 0 <= r < 8:
        t = chess.square(f, r)
        if board.piece_at(t):
            return t
        f, r = f + df, r + dr
    return None


def line_motifs(board: chess.Board, line: list, score_mate: Optional[int] = None,
                plies: int = PLIES) -> list:
    """Motif tags in `line` (SAN or Move, from `board`), from the point of
    view of the side to move at `board` (the one carrying out the tactic).
    `score_mate`: the engine's mate score for the line (White's view), if any.
    `plies`: how far into the line to look."""
    me = board.turn
    boards, moves = [board.copy(stack=False)], []
    for item in line[:plies]:
        b = boards[-1]
        try:
            mv = item if isinstance(item, chess.Move) else b.parse_san(item)
        except ValueError:
            break
        moves.append(mv)
        nb = b.copy(stack=False)
        nb.push(mv)
        boards.append(nb)
    if not moves:
        return []
    found = []

    def add(tag):
        if tag not in found:
            found.append(tag)

    end = boards[-1]
    mating = (score_mate is not None and (score_mate > 0) == (me == chess.WHITE)) \
        or (end.is_checkmate() and end.turn != me)
    if mating:
        add("mate")
        if end.is_checkmate():
            k = end.king(end.turn)
            last = moves[-1]
            piece = boards[-2].piece_type_at(last.from_square)
            home = 0 if end.turn == chess.WHITE else 7
            if k is not None and chess.square_rank(k) == home and piece in (chess.ROOK, chess.QUEEN) \
                    and chess.square_rank(last.to_square) == home:
                add("back_rank_mate")

    start_balance = _balance(boards[0], me)
    first_own = 0 if boards[0].turn == me else 1
    for i, mv in enumerate(moves):
        b, after = boards[i], boards[i + 1]
        if b.turn != me:
            continue
        mover = after.piece_at(mv.to_square)
        if mover is None:
            continue
        # double check
        if after.is_check() and len(after.checkers()) >= 2:
            add("double_check")
        # promotion / advanced pawn
        if mv.promotion:
            add("promotion")
        elif mover.piece_type == chess.PAWN:
            # on the 7th rank, or a passed pawn on the 6th: a promotion threat
            rel = chess.square_rank(mv.to_square) if me == chess.WHITE else 7 - chess.square_rank(mv.to_square)
            if rel == 6 or (rel == 5 and _passed(after, mv.to_square, me)):
                add("advanced_pawn")
        # hanging piece: taking something nothing defended
        if b.is_capture(mv) and not b.is_en_passant(mv):
            victim = b.piece_at(mv.to_square)
            # only our first move: a piece won later is the tactic paying off
            if victim and victim.piece_type != chess.PAWN and i == first_own \
                    and not b.attackers(victim.color, mv.to_square):
                add("hanging_piece")
            # capturing a defender: what it defended is taken later in the line
            if victim and i + 2 < len(boards):
                guarded = {sq for sq in b.attacks(mv.to_square)
                           if (p := b.piece_at(sq)) and p.color == victim.color
                           and p.piece_type != chess.KING and VALUES[p.piece_type] >= 3}
                if guarded and _captures_later(boards, moves, i + 1, guarded, me):
                    add("capturing_defender")
        # fork: the moved piece attacks two worthwhile targets, one is won
        targets = {sq for sq in after.attacks(mv.to_square)
                   if (p := after.piece_at(sq)) and p.color != me
                   and (p.piece_type == chess.KING or VALUES[p.piece_type] > VALUES[mover.piece_type]
                        or (VALUES[p.piece_type] >= 3 and not after.attackers(p.color, sq)))}
        if len(targets) >= 2 and _captures_later(boards, moves, i + 1, targets - {after.king(not me)}, me):
            add("fork")
        # pin: the moved piece pins an enemy piece to its king, and the
        # pinned piece is won in the line
        if mover.piece_type in (chess.BISHOP, chess.ROOK, chess.QUEEN):
            for sq in after.attacks(mv.to_square):
                p = after.piece_at(sq)
                if p and p.color != me and p.piece_type != chess.KING \
                        and after.is_pinned(not me, sq) \
                        and mv.to_square in chess.SquareSet(after.pin(not me, sq)) \
                        and _captures_later(boards, moves, i + 1, {sq}, me):
                    add("pin")
                    break
            # skewer: a valuable piece attacked with another piece straight
            # behind it on the same line, and the one behind is won
            for sq in after.attacks(mv.to_square):
                front = after.piece_at(sq)
                if not front or front.color == me or front.piece_type not in (
                        chess.KING, chess.QUEEN, chess.ROOK):
                    continue
                t = _beyond(after, mv.to_square, sq)
                back = after.piece_at(t) if t is not None else None
                if back and back.color != me and VALUES[back.piece_type] >= 3 \
                        and VALUES[back.piece_type] <= max(VALUES[front.piece_type], 9) \
                        and _captures_later(boards, moves, i + 1, {t}, me):
                    add("skewer")
                    break
        # discovered attack: the move opened a line for another of our
        # pieces onto the king or onto a piece the line then wins
        for sq, p in after.piece_map().items():
            if p.color == me or sq == mv.to_square:
                continue
            if p.piece_type != chess.KING and VALUES[p.piece_type] < 3:
                continue
            opened = [a for a in after.attackers(me, sq) - b.attackers(me, sq)
                      if a != mv.to_square and mv.from_square in chess.SquareSet(chess.between(a, sq))]
            if opened and (p.piece_type == chess.KING
                           or _captures_later(boards, moves, i + 1, {sq}, me)):
                add("discovered_attack")
                break
        # sacrifice: our first move gives up material the line wins back (or mates)
        if i == 0 and i + 1 < len(boards) - 1:
            after_reply = boards[min(i + 2, len(boards) - 1)]
            # ...and comes out ahead (getting it back is an exchange)
            if _balance(after_reply, me) <= start_balance - 2 and (
                    mating or _balance(end, me) > start_balance):
                add("sacrifice")
    return [t for t in MOTIFS if t in found]


def describe(tags: list) -> str:
    return ", ".join(MOTIFS[t] for t in tags if t in MOTIFS)
