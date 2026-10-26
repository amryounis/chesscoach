"""
Phase 7g: what a move does, as events: each a sentence and the arrows that
show it on the board, all computed with python-chess. The notes under every
move and the board markings come from here (the coach's prompts still use
describe.describe_move_effects).

Chessmaster 10's auto-annotation of Homer vs Fritz was the yardstick: it
said "Protects White's pawn at c4" for 9.e3 (a line opened for the f1
bishop), "Removes the threat on White's pawn at b2" for 14.O-O-O,
"Partially pins…", "Creates a passed pawn…", "Black wins a bishop for a
knight. Material is even." Every one of those is a fact code can compute.

Event kinds, most telling first (RANK): checkmate, check, fork, threat
(a new threat left standing), lost (a piece left with no defender),
defended (a threatened piece now safe), recapture, pin, unpin, discovered
(a line opened: attack or defence), passed, promote, structure, material,
castle, attack. Arrow kinds: attack, defend, threat; circles: loose.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import chess

from .describe import PIECE_NAMES, piece_label, side_name

VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5,
          chess.QUEEN: 9, chess.KING: 100}
RANK = ["checkmate", "check", "fork", "hangs", "threat", "lost", "defended", "recapture",
        "pin", "discovered", "unpin", "promote", "passed", "material", "structure",
        "castle", "attack"]


@dataclass
class Event:
    kind: str
    text: str
    shapes: list = field(default_factory=list)   # {"from","to","kind"} | {"square","kind"}


def _name(board, sq) -> str:
    p = board.piece_at(sq)
    return "the king" if p and p.piece_type == chess.KING else f"the {piece_label(board, sq)}"


def _short(board, sq) -> str:
    return f"the {PIECE_NAMES[board.piece_type_at(sq)]} on {chess.square_name(sq)}"


def _threatened(b: chess.Board, sq: int) -> list:
    """Attackers that make the piece on sq a real target: a cheaper one,
    or any attacker when it has no defender."""
    p = b.piece_at(sq)
    if not p or p.piece_type == chess.KING:
        return []
    atk = list(b.attackers(not p.color, sq))
    cheaper = [a for a in atk if VALUES[b.piece_type_at(a)] < VALUES[p.piece_type]]
    return cheaper or (atk if atk and not b.attackers(p.color, sq) else [])


def _worth_attacking(b: chess.Board, attacker_sq: int, sq: int) -> bool:
    """The king, a piece worth more than the attacker, an undefended
    piece, or anything a pawn attacks (pawn tension)."""
    q = b.piece_at(sq)
    own = VALUES[b.piece_type_at(attacker_sq)]
    return (q.piece_type == chess.KING or VALUES[q.piece_type] > own
            or not b.attackers(q.color, sq) or own == 1)


def _rays_pins(b: chess.Board, color: bool) -> set:
    """(pinner, pinned, behind) for every pin by `color`'s sliders: an enemy
    piece in front of a more valuable enemy piece (or the king) on the line."""
    out = set()
    dirs = {chess.BISHOP: [(1, 1), (1, -1), (-1, 1), (-1, -1)],
            chess.ROOK: [(1, 0), (-1, 0), (0, 1), (0, -1)]}
    dirs[chess.QUEEN] = dirs[chess.BISHOP] + dirs[chess.ROOK]
    for sq, p in b.piece_map().items():
        if p.color != color or p.piece_type not in dirs:
            continue
        for df, dr in dirs[p.piece_type]:
            f, r = chess.square_file(sq), chess.square_rank(sq)
            first = None
            while True:
                f, r = f + df, r + dr
                if not (0 <= f < 8 and 0 <= r < 8):
                    break
                t = chess.square(f, r)
                q = b.piece_at(t)
                if q is None:
                    continue
                if q.color == color:
                    break
                if first is None:
                    first = t
                    continue
                if (VALUES[q.piece_type] > VALUES[b.piece_type_at(first)]
                        and VALUES[q.piece_type] >= 5):     # to a rook, queen or king
                    out.add((sq, first, t))
                break
    return out


def _pawn_files(b: chess.Board, color: bool) -> dict:
    files = {}
    for sq in b.pieces(chess.PAWN, color):
        files.setdefault(chess.square_file(sq), []).append(sq)
    return files


def _passed(b: chess.Board, sq: int) -> bool:
    p = b.piece_at(sq)
    if not p or p.piece_type != chess.PAWN:
        return False
    f, r = chess.square_file(sq), chess.square_rank(sq)
    for e in b.pieces(chess.PAWN, not p.color):
        ef, er = chess.square_file(e), chess.square_rank(e)
        if abs(ef - f) <= 1 and ((er > r) if p.color == chess.WHITE else (er < r)):
            return False
    return True


def _isolated(b: chess.Board, color: bool) -> set:
    files = _pawn_files(b, color)
    return {f for f in files if f - 1 not in files and f + 1 not in files}


def _doubled(b: chess.Board, color: bool) -> set:
    return {f for f, sqs in _pawn_files(b, color).items() if len(sqs) > 1}


def material_words(board: chess.Board) -> str:
    """The balance in one short sentence: "Material is level", "Black is up
    a bishop and a pawn", or when both have extras "Black is about a pawn
    ahead: two bishops against a knight and a pawn"."""
    from .facts import ORDER, count_phrase, join_words, material_counts
    w, b = material_counts(board, chess.WHITE), material_counts(board, chess.BLACK)
    w_extra = [count_phrase(w[pt] - b[pt], pt) for pt in ORDER if w[pt] > b[pt]]
    b_extra = [count_phrase(b[pt] - w[pt], pt) for pt in ORDER if b[pt] > w[pt]]
    net = sum(VALUES[pt] * (w[pt] - b[pt]) for pt in ORDER)
    if not w_extra and not b_extra:
        return "Material is level"
    if not b_extra:
        return f"White is up {join_words(w_extra)}"
    if not w_extra:
        return f"Black is up {join_words(b_extra)}"
    if net == 0:
        return f"Material is level: {join_words(w_extra)} against {join_words(b_extra)}"
    ahead, mine, theirs = (("White", w_extra, b_extra) if net > 0 else ("Black", b_extra, w_extra))
    worth = "a pawn" if abs(net) == 1 else f"{abs(net)} pawns"
    return f"{ahead} is about {worth} ahead: {join_words(mine)} against {join_words(theirs)}"


def move_events(board_before: chess.Board, move: chess.Move) -> list:
    """Every event of the move, most telling first. `board_before` should
    carry its move stack: a threat counts as new only if the opponent's
    last move created it (the gambit pawn on c4 is not news every move)."""
    mover = board_before.piece_at(move.from_square)
    if mover is None:
        return []
    me, them = mover.color, not mover.color
    b0 = board_before.copy()
    after = board_before.copy()
    after.push(move)
    to, name = move.to_square, chess.square_name
    who = side_name(me)
    ev = []
    captured = b0.piece_at(to) if b0.is_capture(move) and not b0.is_en_passant(move) else (
        chess.Piece(chess.PAWN, them) if b0.is_en_passant(move) else None)
    moved_type = after.piece_type_at(to)

    # Checks: mate, double, discovered, plain.
    if after.is_check():
        checkers = list(after.checkers())
        arrows = [{"from": name(c), "to": name(after.king(them)), "kind": "attack"} for c in checkers]
        if after.is_checkmate():
            ev.append(Event("checkmate", "Checkmate", arrows))
        elif len(checkers) > 1:
            ev.append(Event("check", "It gives a double check: the king must move", arrows))
        elif checkers[0] != to:
            ev.append(Event("check", f"It gives a discovered check: {_short(after, checkers[0])} "
                                     f"now checks the king", arrows))
        else:
            ev.append(Event("check", "It gives check", arrows))

    # A fork: the moved piece attacks two targets it could win.
    forked = [sq for sq in after.attacks(to)
              if (q := after.piece_at(sq)) and q.color == them
              and (q.piece_type == chess.KING or VALUES[q.piece_type] > VALUES[moved_type]
                   or (VALUES[q.piece_type] >= 3 and not after.attackers(them, sq)))]
    safe = not after.attackers(them, to) or after.attackers(me, to)
    if len(forked) >= 2 and safe:
        names = [_name(after, sq) for sq in sorted(forked)]
        ev.append(Event("fork", f"It is a fork: the {PIECE_NAMES[moved_type]} on {name(to)} "
                                f"attacks {', '.join(names[:-1])} and {names[-1]} at once",
                        [{"from": name(to), "to": name(sq), "kind": "attack"} for sq in forked]))

    # The moved piece itself can be taken: by something cheaper, or with no
    # defender (6...Bxd4 7.Nxd4; 9.Bxe5 dxe5; 10.Bxf7+ Kxf7).
    # (Pawns are left out: 2.c4 "hangs" the pawn, and that is the gambit.)
    if moved_type not in (chess.KING, chess.PAWN) and not after.is_checkmate():
        takers = sorted(after.attackers(them, to), key=lambda a: VALUES[after.piece_type_at(a)])
        defended = bool(after.attackers(me, to))
        cheaper = [a for a in takers if VALUES[after.piece_type_at(a)] < VALUES[moved_type]]
        legal_takers = [a for a in (cheaper or (takers if not defended else []))
                        if chess.Move(a, to) in after.legal_moves
                        or (after.piece_type_at(a) == chess.PAWN
                            and chess.square_rank(to) in (0, 7))]
        gain = VALUES[captured.piece_type] if captured else 0
        if legal_takers and VALUES[moved_type] > gain:
            by = _short(after, legal_takers[0])
            piece = PIECE_NAMES[moved_type]
            if captured:
                text = (f"It takes the {PIECE_NAMES[captured.piece_type]}, but {by} can take "
                        f"the {piece} back: a {piece} for a {PIECE_NAMES[captured.piece_type]}")
            else:
                text = f"It puts the {piece} where {by} can take it"
            ev.append(Event("hangs", text,
                            [{"from": name(legal_takers[0]), "to": name(to), "kind": "threat"}]))

    # Threats against our pieces: new ones left standing; ones now dealt with.
    prev = None
    if b0.move_stack:
        prev = b0.copy()
        prev.pop()
    for sq, q in b0.piece_map().items():
        if q.color != me or q.piece_type == chess.KING or sq == move.from_square:
            continue
        was, still = _threatened(b0, sq), _threatened(after, sq)
        if after.piece_at(sq) != q:
            continue
        if was and still and not b0.is_check():
            new = prev is None or prev.piece_at(sq) != q or not _threatened(prev, sq)
            if new and not (captured and VALUES[captured.piece_type] >= VALUES[q.piece_type]):
                ev.append(Event("threat", f"It IGNORES a threat: {_name(after, sq)} was attacked "
                                          f"by {_short(b0, was[0])} before this move and is "
                                          f"still attacked after it",
                                [{"from": name(still[0]), "to": name(sq), "kind": "threat"}]))
        elif was and not still and to not in was:
            defenders = [d for d in after.attackers(me, sq) if d not in b0.attackers(me, sq)]
            by = _short(after, defenders[0]) if defenders else ""
            if defenders and defenders[0] == to:
                ev.append(Event("defended", f"It defends {_name(after, sq)}, which "
                                            f"{_short(b0, was[0])} was attacking",
                                [{"from": name(to), "to": name(sq), "kind": "defend"}]))
            elif defenders:
                # 9.e3: the f1 bishop now defends c4.
                ev.append(Event("defended", f"It opens the line for {_short(after, defenders[0])}, "
                                            f"which now defends {_name(after, sq)} against "
                                            f"{_short(b0, was[0])}",
                                [{"from": name(defenders[0]), "to": name(sq), "kind": "defend"}]))
            elif not after.attackers(them, sq):
                ev.append(Event("defended", f"It blocks the attack on {_name(after, sq)} by "
                                            f"{_short(b0, was[0])}"))
        elif was and to in was:
            ev.append(Event("defended", f"It removes the threat to {_name(after, sq)} by "
                                        f"capturing the attacker"))

    # Our pieces left with no defender at all (still defended: not news).
    for sq in b0.attacks(move.from_square):
        q = after.piece_at(sq)
        if (q and q.color == me and q.piece_type != chess.KING and sq != to
                and after.attackers(them, sq) and not after.attackers(me, sq)
                and b0.attackers(me, sq)):
            ev.append(Event("lost", f"The {PIECE_NAMES[mover.piece_type]} no longer defends "
                                    f"{_name(after, sq)}, which is attacked and now has no "
                                    f"defender", [{"square": name(sq), "kind": "loose"}]))

    # Recapture: taking back on the square the opponent just captured on.
    if (captured and b0.move_stack and b0.peek().to_square == to and prev is not None
            and prev.is_capture(b0.peek())):
        ev.append(Event("recapture", f"It recaptures on {name(to)}"))

    # Pins made by the moved piece, pins it released (ours).
    pins_after = _rays_pins(after, me)
    for pinner, pinned, behind in sorted(_rays_pins(after, me) - _rays_pins(b0, me)):
        if pinner != to:
            continue
        what = ("the king" if after.piece_type_at(behind) == chess.KING
                else _short(after, behind))
        ev.append(Event("pin", f"It pins {_name(after, pinned)} to {what}",
                        [{"from": name(to), "to": name(pinned), "kind": "attack"}]))
    freed = {p for _, p, _ in _rays_pins(b0, them)} - {p for _, p, _ in _rays_pins(after, them)}
    freed = [sq for sq in freed if after.piece_at(sq) == b0.piece_at(sq)]
    if freed:
        ev.append(Event("unpin", "It frees " + " and ".join(_name(after, sq) for sq in sorted(freed))
                                 + " from a pin"))
    del pins_after

    # Lines opened by the move: our other sliders now attack or defend.
    for sq, p in after.piece_map().items():
        if (p.color != me or sq == to or p.piece_type not in (chess.BISHOP, chess.ROOK, chess.QUEEN)
                or b0.piece_at(sq) != p):
            continue
        new = after.attacks(sq) - b0.attacks(sq)
        told = {s_.get("to") for e in ev for s_ in e.shapes}
        for t in sorted(new):
            q = after.piece_at(t)
            if not q or t == to or name(t) in told:
                continue
            if q.color == me and q.piece_type != chess.KING and after.attackers(them, t):
                ev.append(Event("discovered", f"It opens the line for {_short(after, sq)}, which "
                                              f"now defends {_name(after, t)}",
                                [{"from": name(sq), "to": name(t), "kind": "defend"}]))
            elif q.color == them and q.piece_type != chess.KING and _worth_attacking(after, sq, t):
                ev.append(Event("discovered", f"It opens the line for {_short(after, sq)}, which "
                                              f"now attacks {_name(after, t)}",
                                [{"from": name(sq), "to": name(t), "kind": "attack"}]))

    # Pawns: promotion, promotion threat, passed pawns, structure.
    if move.promotion:
        ev.append(Event("promote", f"The pawn promotes to a {PIECE_NAMES[move.promotion]} "
                                   f"on {name(to)}"))
    if moved_type == chess.PAWN:
        rank = chess.square_rank(to)
        if (me == chess.WHITE and rank == 6) or (me == chess.BLACK and rank == 1):
            ev.append(Event("promote", f"The pawn on {name(to)} is one step from promoting"))
        if _passed(after, to):
            ev.append(Event("passed", f"It pushes the passed pawn to {name(to)}"
                            if _passed(b0, move.from_square) else
                            f"It creates a passed pawn on {name(to)}"))
    # Mid-exchange (the opponent can take back on that square) the balance
    # and the pawn structure are not settled yet: say them after the trade.
    settled = not captured or not after.attackers(them, to)
    for color, whose in ((them, side_name(them)), (me, who)) if settled else ():
        iso = _isolated(after, color) - _isolated(b0, color)
        for f in sorted(iso):
            if f in _pawn_files(after, color):
                sq = _pawn_files(after, color)[f][0]
                ev.append(Event("structure", f"{whose}'s pawn on {name(sq)} is now isolated"))
        dbl = _doubled(after, color) - _doubled(b0, color)
        for f in sorted(dbl):
            ev.append(Event("structure", f"{whose} now has doubled pawns on the "
                                         f"{chess.FILE_NAMES[f]}-file"))

    # Material after a capture: the balance, not the capture count.
    if captured and settled:
        ev.append(Event("material", material_words(after)))

    # Castling, and castling made possible.
    if b0.is_castling(move):
        ev.append(Event("castle", f"{who} castles "
                        + ("kingside" if chess.square_file(to) == 6 else "queenside")))
    for side, between in (("kingside", (chess.F1, chess.G1) if me else (chess.F8, chess.G8)),
                          ("queenside", (chess.B1, chess.C1, chess.D1) if me
                           else (chess.B8, chess.C8, chess.D8))):
        right = (b0.has_kingside_castling_rights(me) if side == "kingside"
                 else b0.has_queenside_castling_rights(me))
        if right and move.from_square in between and all(after.piece_at(s) is None for s in between):
            ev.append(Event("castle", f"It clears the way for {who} to castle {side}"))

    # Plain attacks of the moved piece, when nothing above said it.
    said = {s.get("to") for e in ev for s in e.shapes}
    hits = [t for t in after.attacks(to)
            if (q := after.piece_at(t)) and q.color == them and q.piece_type != chess.KING
            and name(t) not in said and _worth_attacking(after, to, t)]
    if hits:
        hits.sort(key=lambda t: -VALUES[after.piece_type_at(t)])
        ev.append(Event("attack", "It attacks " + " and ".join(_name(after, t) for t in hits),
                        [{"from": name(to), "to": name(t), "kind": "attack"} for t in hits]))
    # Newly defending an attacked piece of ours (not already said).
    for t in after.attacks(to):
        q = after.piece_at(t)
        if (q and q.color == me and q.piece_type != chess.KING and name(t) not in said
                and t not in b0.attacks(move.from_square) and after.attackers(them, t)
                and not any(s_.get("to") == name(t) for e in ev for s_ in e.shapes)):
            ev.append(Event("attack", f"It defends {_name(after, t)}",
                            [{"from": name(to), "to": name(t), "kind": "defend"}]))

    ev.sort(key=lambda e: RANK.index(e.kind))
    return ev


def notes_and_shapes(board_before: chess.Board, move: chess.Move, n: int = 3,
                     limit: int = 6) -> tuple:
    """(the `n` most telling sentences, the board markings for them and the
    rest, at most `limit`): the note under every move and its arrows."""
    evs = move_events(board_before, move)
    notes = [e.text for e in evs[:n]]
    shapes, seen = [], set()
    for e in evs:
        for s in e.shapes:
            key = (s.get("from"), s.get("to"), s.get("square"))
            if key not in seen:
                seen.add(key)
                shapes.append(s)
    order = {"threat": 0, "attack": 1, "loose": 2, "defend": 3}
    shapes.sort(key=lambda s: order[s["kind"]])
    return notes, shapes[:limit]
