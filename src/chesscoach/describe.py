"""Moves and boards described in plain English, from python-chess facts."""

from __future__ import annotations


import chess


PIECE_NAMES = {
    chess.PAWN: "pawn", chess.KNIGHT: "knight", chess.BISHOP: "bishop",
    chess.ROOK: "rook", chess.QUEEN: "queen", chess.KING: "king",
}


def piece_label(board: chess.Board, sq: int) -> str:
    """Return e.g. 'White knight on f3' for the piece on sq."""
    p = board.piece_at(sq)
    if not p:
        return f"empty square {chess.square_name(sq)}"
    side = "White" if p.color == chess.WHITE else "Black"
    return f"{side} {PIECE_NAMES[p.piece_type]} on {chess.square_name(sq)}"


def side_name(color: bool) -> str:
    return "White" if color == chess.WHITE else "Black"


def describe_move(board: chess.Board, move: chess.Move) -> str:
    """
    One move in plain English, from the position BEFORE the move.
    e.g. 'White captures the knight on f6 with the pawn from e5, giving check'
    """
    p = board.piece_at(move.from_square)
    if p is None:
        return "an unknown move"
    side = side_name(p.color)
    pname = PIECE_NAMES[p.piece_type]
    frm = chess.square_name(move.from_square)
    to = chess.square_name(move.to_square)

    if board.is_castling(move):
        text = f"{side} castles " + (
            "kingside" if board.is_kingside_castling(move) else "queenside")
    elif board.is_en_passant(move):
        text = (f"{side} captures {side_name(not p.color)}'s pawn en passant "
                f"with the pawn from {frm}, landing on {to}")
    elif board.is_capture(move):
        cap = board.piece_at(move.to_square)
        text = (f"{side} captures {side_name(cap.color)}'s "
                f"{PIECE_NAMES[cap.piece_type]} on {to} with the {pname} from {frm}")
    else:
        text = f"{side} moves the {pname} from {frm} to {to}"

    if move.promotion:
        text += f", promoting to a {PIECE_NAMES[move.promotion]}"
    if board.gives_check(move):
        b2 = board.copy()
        b2.push(move)
        text += ", delivering CHECKMATE" if b2.is_checkmate() else ", giving check"
    return text


def describe_move_effects(board_before: chess.Board, move: chess.Move) -> list:
    """
    Verified consequences of a move: what it captured, what the moved piece
    now attacks, and — critically — which friendly pieces it STOPPED defending.
    (That last one is what the LLM kept inventing: e.g. 'the knight on d4
    defends e5'. Here we compute the real relationship.)
    """
    effects = []
    mover = board_before.piece_at(move.from_square)
    if mover is None:
        return effects
    color = mover.color
    after = board_before.copy()
    after.push(move)

    # Pieces the moving piece defended before, but no longer defends
    defended_before = {
        sq for sq in board_before.attacks(move.from_square)
        if (q := board_before.piece_at(sq)) and q.color == color
        and q.piece_type != chess.KING
    }
    defended_after = {
        sq for sq in after.attacks(move.to_square)
        if (q := after.piece_at(sq)) and q.color == color
        and q.piece_type != chess.KING
    }
    # Only report it when it matters: the piece is attacked afterwards.
    for sq in sorted(defended_before - defended_after):
        if after.piece_at(sq) is None or not after.attackers(not color, sq):
            continue
        remaining = after.attackers(color, sq)
        line = (f"The {PIECE_NAMES[mover.piece_type]} no longer defends the "
                f"{piece_label(after, sq)}, which is attacked")
        line += (" — it now has NO defenders" if not remaining
                 else " — still defended by "
                      + ", ".join(piece_label(after, d) for d in remaining))
        effects.append(line)

    # Threat handling: friendly pieces under real threat BEFORE the move
    # (undefended, or attacked by a cheaper piece). Did this move fix it?
    vals = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
            chess.ROOK: 5, chess.QUEEN: 9}
    def threatened(b, sq, piece):
        atk = b.attackers(not piece.color, sq)
        if not atk:
            return []
        cheaper = [a for a in atk
                   if vals.get(b.piece_at(a).piece_type, 0) < vals[piece.piece_type]]
        if cheaper:
            return cheaper
        return list(atk) if not b.attackers(piece.color, sq) else []
    for sq, q in board_before.piece_map().items():
        if q.color != color or q.piece_type == chess.KING:
            continue
        threat = threatened(board_before, sq, q)
        if not threat:
            continue
        by = ", ".join(piece_label(board_before, a) for a in threat)
        if sq == move.from_square:
            still = threatened(after, move.to_square, q)
            if not still:
                effects.append(f"It moves the {PIECE_NAMES[q.piece_type]} "
                               f"out of danger (it was attacked by the {by})")
        elif sq == move.to_square:
            pass
        elif after.piece_at(sq) == q and threatened(after, sq, q):
            captured = board_before.piece_at(move.to_square)
            if board_before.is_check():
                # Answering a check is forced; 'ignores a threat' would blame
                # the player for something they had no time for.
                effects.append(
                    f"It answers the check; the {piece_label(after, sq)} "
                    f"is still attacked by the {by}")
            elif captured and vals.get(captured.piece_type, 0) >= vals[q.piece_type]:
                effects.append(
                    f"It leaves the {piece_label(after, sq)} attacked by the "
                    f"{by}, but in return captures a "
                    f"{PIECE_NAMES[captured.piece_type]} — a counter-attack "
                    f"rather than a defence")
            else:
                effects.append(
                    f"It IGNORES a threat: the {piece_label(after, sq)} was "
                    f"attacked by the {by} before this move and is still "
                    f"attacked after it")
        elif after.piece_at(sq) == q and move.to_square in threat:
            effects.append(f"It removes the threat to the "
                           f"{piece_label(after, sq)} by capturing the "
                           f"attacking piece")

    # A check against a king that may still castle: the reason for many
    # checks, and in the baseline run the fact the model left out even
    # though the castling rights were in its prompt.
    opp = not color
    if after.is_check() and (after.has_kingside_castling_rights(opp)
                             or after.has_queenside_castling_rights(opp)):
        them = side_name(opp)
        replies = list(after.legal_moves)
        if replies and all(r.from_square == after.king(opp) for r in replies):
            effects.append(f"The check takes away {them}'s castling for good: "
                           f"every legal reply moves the king")
        else:
            effects.append(f"The check stops {them} from castling on the next move; "
                           f"if {them} answers with a king move, the right to "
                           f"castle is lost for good")

    # A fork: the moved piece attacks two targets at once, each of which it
    # could win (the king, a piece worth more than the attacker, or an
    # undefended piece), and the attacker itself is not simply lost.
    # (8...e5 attacking the knight on d4 and the bishop on f4.)
    own = vals.get(mover.piece_type, 100) if mover.piece_type != chess.KING else 100
    if move.promotion:
        own = vals.get(move.promotion, 9)
    forked = []
    for sq in after.attacks(move.to_square):
        q = after.piece_at(sq)
        if not q or q.color == color:
            continue
        if (q.piece_type == chess.KING or vals.get(q.piece_type, 0) > own
                or (vals.get(q.piece_type, 0) >= 3 and not after.attackers(not color, sq))):
            forked.append(sq)
    forker_safe = (not after.attackers(not color, move.to_square)
                   or after.attackers(color, move.to_square))
    if len(forked) >= 2 and forker_safe:
        names = [("the king" if after.piece_type_at(sq) == chess.KING
                  else f"the {piece_label(after, sq)}") for sq in sorted(forked)]
        effects.append(f"It is a fork: the {PIECE_NAMES[after.piece_type_at(move.to_square)]} "
                       f"on {chess.square_name(move.to_square)} attacks "
                       + " and ".join([", ".join(names[:-1]), names[-1]]
                                      if len(names) > 2 else names) + " at once")

    # Castling made possible: the move clears the last piece between king
    # and rook ("Enables the short castle").
    for side, between in (("kingside", (chess.F1, chess.G1) if color else (chess.F8, chess.G8)),
                          ("queenside", (chess.B1, chess.C1, chess.D1) if color
                           else (chess.B8, chess.C8, chess.D8))):
        right = (board_before.has_kingside_castling_rights(color) if side == "kingside"
                 else board_before.has_queenside_castling_rights(color))
        if (right and move.from_square in between
                and all(after.piece_at(sq) is None for sq in between)):
            effects.append(f"It clears the way for {side_name(color)} to castle {side}")

    # What the moved piece attacks from its new square
    targets = [piece_label(after, sq) for sq in after.attacks(move.to_square)
               if (q := after.piece_at(sq)) and q.color != color
               and q.piece_type != chess.KING]
    if targets:
        effects.append(f"From {chess.square_name(move.to_square)} it now "
                       f"attacks: " + ", ".join(targets))

    # Pieces left newly hanging some other way (e.g. a line was opened),
    # skipping ones already reported above.
    for sq, q in after.piece_map().items():
        if q.color != color or q.piece_type == chess.KING or sq == move.to_square:
            continue
        name = chess.square_name(sq)
        if any(f" on {name}" in e for e in effects):
            continue
        was = (board_before.piece_at(sq) == q
               and board_before.attackers(not color, sq)
               and not board_before.attackers(color, sq))
        now = after.attackers(not color, sq) and not after.attackers(color, sq)
        if now and not was:
            effects.append(f"After the move, the {piece_label(after, sq)} "
                           f"is attacked and has no defenders")
    return effects


def describe_pv_ideas(board: chess.Board, pv_moves: list,
                      max_moves: int = 3) -> str:
    """Convert a PV line (SAN list) into numbered plain-English steps."""
    out = []
    temp = board.copy()
    for i, san in enumerate(pv_moves[:max_moves]):
        try:
            mv = temp.parse_san(san)
        except ValueError:
            break
        out.append(f"  {i+1}. {describe_move(temp, mv)}")
        temp.push(mv)
    return "\n".join(out) if out else "  No continuation available."


def board_to_ascii(board: chess.Board) -> str:
    """Expanded 8x8 ASCII grid — no compressed FEN digits."""
    rows = []
    for rank in range(7, -1, -1):
        cells = []
        for file in range(8):
            piece = board.piece_at(chess.square(file, rank))
            if piece:
                cells.append(piece.symbol())
            else:
                cells.append(".")
        rows.append(f"[{rank+1}] {' '.join(cells)}")
    rows.append("    a b c d e f g h")
    return "\n".join(rows)


def board_to_piece_list(board: chess.Board) -> str:
    """Explicit piece-by-square listing, grouped by side."""
    white_pieces = []
    black_pieces = []
    for sq in chess.SQUARES:
        piece = board.piece_at(sq)
        if piece:
            name = PIECE_NAMES[piece.piece_type]
            sq_name = chess.square_name(sq)
            entry = f"{name} on {sq_name}"
            if piece.color == chess.WHITE:
                white_pieces.append(entry)
            else:
                black_pieces.append(entry)
    lines = ["White pieces: " + ", ".join(white_pieces),
             "Black pieces: " + ", ".join(black_pieces)]
    return "\n".join(lines)


# The note under every move and its arrows now come from events.py (phase
# 7g); these two names stay for the callers and tests that use them.

def move_notes(board_before: chess.Board, move: chess.Move, n: int = 3) -> list:
    """At most `n` verified things the move does, the most telling first."""
    from .events import notes_and_shapes
    return notes_and_shapes(board_before, move, n=n)[0]


def move_shapes(board_before: chess.Board, move: chess.Move, limit: int = 6) -> list:
    """Board markings for what the move does (arrows and circles)."""
    from .events import notes_and_shapes
    return notes_and_shapes(board_before, move, limit=limit)[1]
