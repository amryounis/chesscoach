"""
"Explain this position", laid out as a coach's sheet.

Large chat models write a well-ordered position summary (ChessBase's AI
review, ChatGPT, Gemini), but the facts under it are unreliable: in Amr's
tests one said the material was equal with White a pawn up, one invented a
move order that cannot reach the position, one proposed a break with a pawn
Black did not have. So here every section but the plans is written by code,
from the board and Stockfish, and is true by construction:

  Overview          who is to move, who is better, the material, the opening
  Material, pawns   the balance; doubled, isolated and passed pawns; pawns
                    with no defender, and what is aimed at them
  Kings             castled or not, the rights left, the files open next to it
  Pieces            development, room to move, the bishop pair
  Threats           what attacks what now; what the other side would do
                    with a free move
  Candidate moves   the engine's best, each with what it does and its line
  Moves to avoid    tempting moves (captures, checks) that lose, and how

The model writes only the plans for both sides, from these facts, and the
board check reads them (coach.explain).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import chess

from .allmoves import EQUAL, explain_all
from .assessment import eval_phrase, label_for_drop
from .concepts import threat
from .describe import PIECE_NAMES, describe_move_effects, side_name
from .engine import run_stockfish
from .facts import material_lines
from .opening import name_at

HOME_MINORS = {chess.WHITE: {chess.B1: chess.KNIGHT, chess.G1: chess.KNIGHT,
                             chess.C1: chess.BISHOP, chess.F1: chess.BISHOP},
               chess.BLACK: {chess.B8: chess.KNIGHT, chess.G8: chess.KNIGHT,
                             chess.C8: chess.BISHOP, chess.F8: chess.BISHOP}}
VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9,
         chess.KING: 100}
NUMBER = {0: "no", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
          7: "seven", 8: "eight"}
CANDIDATES = 3
AVOID = 3


class Line(str):
    """A sentence of the sheet with what to draw for it on the board:
    [{"orig": "c5"} circle | {"orig": "f8", "dest": "c5"} arrow, "brush": ...]"""
    shapes: list

    def __new__(cls, text: str, shapes=()):
        obj = super().__new__(cls, text)
        obj.shapes = [dict(x) for x in shapes]
        return obj


def _sq(sq: int) -> str:
    return chess.square_name(sq)


def circle(sq: int, brush: str) -> dict:
    return {"orig": _sq(sq), "brush": brush}


def arrow(a: int, b: int, brush: str) -> dict:
    return {"orig": _sq(a), "dest": _sq(b), "brush": brush}


@dataclass
class Brief:
    overview: str = ""
    sections: list = field(default_factory=list)      # [(heading, [lines])]
    lines: list = field(default_factory=list)          # candidate lines, SAN, for the check
    best_san: str = ""

    def marks(self) -> list:
        """What to draw for each line, in the order of the lines:
        [{"shapes": [...], "main": bool}]; the main ones (candidate moves,
        moves to avoid) are drawn while the sheet is shown."""
        out = []
        for head, lines in self.sections:
            main = head.startswith("Candidate") or head == "Moves to avoid"
            out += [{"shapes": getattr(x, "shapes", []), "main": main} for x in lines]
        return out

    def text(self) -> str:
        out = [f"**Overview**: {self.overview}"]
        for head, lines in self.sections:
            if lines:
                out.append(f"**{head}**")
                out += [f"- {x}" for x in lines]
        return "\n".join(out)


def _who(board: chess.Board, sq: int) -> str:
    p = board.piece_at(sq)
    return f"{side_name(p.color)}'s {PIECE_NAMES[p.piece_type]} on {chess.square_name(sq)}"


def _the(board: chess.Board, sq: int) -> str:
    p = board.piece_at(sq)
    return f"the {PIECE_NAMES[p.piece_type]} on {chess.square_name(sq)}"


def _advanced(color: bool, sq: int) -> bool:
    r = chess.square_rank(sq)
    return r >= 3 if color == chess.WHITE else r <= 4


def aimed_through_own_pawn(board: chess.Board, target: int) -> list:
    """Enemy bishops, rooks and queens whose line to `target` is blocked
    only by one of their own pawns: when that pawn moves, they attack it.
    -> [(slider square, pawn square)]"""
    p = board.piece_at(target)
    if p is None:
        return []
    out = []
    for sq in chess.SquareSet(board.occupied_co[not p.color]):
        q = board.piece_at(sq)
        if q.piece_type not in (chess.BISHOP, chess.ROOK, chess.QUEEN):
            continue
        between = [s for s in chess.SquareSet(chess.between(sq, target)) if board.piece_at(s)]
        if len(between) != 1:
            continue
        b = between[0]
        blocker = board.piece_at(b)
        if blocker.color != q.color or blocker.piece_type != chess.PAWN:
            continue
        t = board.copy(stack=False)
        t.remove_piece_at(b)
        if sq in t.attackers(q.color, target):
            out.append((sq, b))
    return out


def pawn_lines(board: chess.Board) -> list:
    out = []
    for color in (chess.WHITE, chess.BLACK):
        side = side_name(color)
        pawns = board.pieces(chess.PAWN, color)
        theirs = board.pieces(chess.PAWN, not color)
        files = [chess.square_file(s) for s in pawns]
        doubled = sorted({f for f in files if files.count(f) > 1})
        for f in doubled:
            sqs = [chess.square_name(s) for s in sorted(pawns) if chess.square_file(s) == f]
            out.append(Line(f"{side} has doubled pawns on the {chess.FILE_NAMES[f]}-file "
                            f"({' and '.join(sqs)}).",
                            [circle(t, "yellow") for t in pawns if chess.square_file(t) == f]))
        for s in sorted(pawns):
            f, r = chess.square_file(s), chess.square_rank(s)
            ahead = [t for t in theirs if abs(chess.square_file(t) - f) <= 1
                     and (chess.square_rank(t) > r if color == chess.WHITE
                          else chess.square_rank(t) < r)]
            if not ahead:
                out.append(Line(f"{side}'s pawn on {chess.square_name(s)} is a passed pawn: "
                                "no enemy pawn can stop it.", [circle(s, "green")]))
            if not [t for t in pawns if abs(chess.square_file(t) - f) == 1] and f not in doubled:
                out.append(Line(f"{side}'s pawn on {chess.square_name(s)} is isolated: "
                                "no pawn of its own on the files next to it.", [circle(s, "yellow")]))
            if _advanced(color, s) and not board.attackers(color, s):
                atk = board.attackers(not color, s)
                line = f"{side}'s pawn on {chess.square_name(s)} has no defender"
                if atk:
                    line += " and is attacked by " + " and ".join(_the(board, a) for a in sorted(atk))
                shapes = [circle(s, "red")] + [arrow(a, s, "attack") for a in sorted(atk)]
                aimed = aimed_through_own_pawn(board, s)
                if aimed:
                    sl, pw = aimed[0]
                    line += (f". {_who(board, sl)} is aimed at it, behind its own pawn on "
                             f"{chess.square_name(pw)}: once that pawn moves, it attacks it")
                    shapes += [arrow(sl, s, "threat"), circle(pw, "yellow")]
                out.append(Line(line + ".", shapes))
    return out


def king_lines(board: chess.Board) -> list:
    out = []
    for color in (chess.WHITE, chess.BLACK):
        side = side_name(color)
        k = board.king(color)
        if k is None:
            continue
        home = chess.E1 if color == chess.WHITE else chess.E8
        ks, qs = board.has_kingside_castling_rights(color), board.has_queenside_castling_rights(color)
        rights = ("can still castle on either side" if ks and qs else
                  "can still castle kingside" if ks else
                  "can still castle queenside" if qs else "can no longer castle")
        f = chess.square_file(k)
        back = chess.square_rank(k) == (0 if color == chess.WHITE else 7)
        if back and f >= 6 and k != home:
            where = f"{side}'s king is castled on the kingside ({chess.square_name(k)})"
        elif back and f <= 2 and k != home:
            where = f"{side}'s king is castled on the queenside ({chess.square_name(k)})"
        elif k == home:
            where = f"{side}'s king is still in the centre on {chess.square_name(k)}"
        else:
            where = f"{side}'s king is on {chess.square_name(k)}"
        line = where + (f" and {rights}" if k == home else "")
        own = board.pieces(chess.PAWN, color)
        open_files = [ff for ff in (f - 1, f, f + 1) if 0 <= ff <= 7
                      and not any(chess.square_file(s) == ff for s in own)]
        if open_files:
            names = [f"{chess.FILE_NAMES[ff]}-" for ff in open_files]
            names = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
            line += (f"; no {side} pawn on the {names}file" + ("s" if len(open_files) > 1 else "")
                     + " next to it")
        out.append(Line(line + ".", [circle(k, "blue")]))
    if board.is_check():
        out.append(f"{side_name(board.turn)} is in check and must answer it.")
    return out


def piece_lines(board: chess.Board) -> list:
    out = []
    for color in (chess.WHITE, chess.BLACK):
        side = side_name(color)
        home = HOME_MINORS[color]
        minors = [s for s in chess.SquareSet(board.pieces(chess.KNIGHT, color)
                                             | board.pieces(chess.BISHOP, color))]
        at_home = [s for s in minors if home.get(s) == board.piece_at(s).piece_type]
        out_ = [s for s in minors if s not in at_home]
        if len(minors) == 1 and not out_:
            out.append(f"{side}'s {PIECE_NAMES[board.piece_at(minors[0]).piece_type]} on "
                       f"{chess.square_name(minors[0])} has not moved yet.")
        elif minors and not out_:
            out.append(Line(f"{side} has not developed a knight or bishop yet.",
                            [circle(s, "yellow") for s in at_home]))
        elif at_home and len(out_) <= 2:
            out.append(Line(f"{side} has developed {NUMBER.get(len(out_), len(out_))} minor "
                            f"piece{'s' if len(out_) > 1 else ''} ("
                            + ", ".join(_the(board, s) for s in out_) + "); still at home: "
                            + ", ".join(_the(board, s) for s in at_home) + ".",
                            [circle(s, "green") for s in out_] + [circle(s, "yellow") for s in at_home]))
    if not board.is_check():
        nb = board.copy(stack=False)
        nb.push(chess.Move.null())
        mine, theirs = board.legal_moves.count(), nb.legal_moves.count()
        if abs(mine - theirs) >= 5:
            more = board.turn if mine > theirs else not board.turn
            out.append(f"{side_name(more)}'s pieces have more room: {max(mine, theirs)} "
                       f"moves to {min(mine, theirs)}.")
    # Pieces with no defender (a rook in its corner is left out: in the
    # opening every one of them is "undefended", and it says nothing).
    for color in (chess.WHITE, chess.BLACK):
        loose = [sq for sq, p in board.piece_map().items()
                 if p.color == color and p.piece_type not in (chess.PAWN, chess.KING)
                 and not board.attackers(color, sq)
                 and not (p.piece_type == chess.ROOK and sq in (chess.A1, chess.H1, chess.A8, chess.H8))]
        if loose:
            names = [f"{_the(board, sq)}" for sq in sorted(loose)]
            names = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
            out.append(Line(f"{side_name(color)}'s {names[4:]} "
                            f"{'is' if len(loose) == 1 else 'are'} undefended.",
                            [circle(sq, "red") for sq in loose]))
    w, b = (len(board.pieces(chess.BISHOP, c)) >= 2 for c in (chess.WHITE, chess.BLACK))
    if w != b:
        c = chess.WHITE if w else chess.BLACK
        out.append(Line(f"{'White' if w else 'Black'} has the bishop pair.",
                        [circle(sq, "green") for sq in board.pieces(chess.BISHOP, c)]))
    return out


def attack_lines(board: chess.Board) -> list:
    """What attacks what now, the attacked piece's defenders, most valuable first."""
    rows = []
    for sq, p in board.piece_map().items():
        if p.piece_type == chess.KING:
            continue
        atk = sorted(board.attackers(not p.color, sq))
        if not atk:
            continue
        dfd = sorted(board.attackers(p.color, sq))
        cheapest = min(VALUE[board.piece_at(a).piece_type] for a in atk)
        who = " and ".join(_who(board, a) for a in atk[:2])
        line = f"{who} attack{'s' if len(atk) == 1 else ''} {_who(board, sq)}"
        if not dfd:
            line += ", which has no defender"
        else:
            line += ", defended by " + " and ".join(_the(board, d) for d in dfd[:2])
        if cheapest < VALUE[p.piece_type] and p.piece_type != chess.PAWN:
            line += " (attacked by a cheaper piece)"
        danger = (not dfd) or cheapest < VALUE[p.piece_type]
        shapes = [arrow(a, sq, "attack" if danger else "threat") for a in atk[:2]]
        shapes += [arrow(d, sq, "defend") for d in dfd[:2]]
        rows.append((not danger, -VALUE[p.piece_type], Line(line + ".", shapes)))
    return [r[2] for r in sorted(rows)[:5]]


def protects(board: chess.Board, move: chess.Move) -> list:
    """Pieces and advanced pawns of the mover that had no defender and gain one."""
    color = board.turn
    after = board.copy(stack=False)
    after.push(move)
    out = []
    for sq, p in board.piece_map().items():
        if p.color != color or p.piece_type == chess.KING or sq == move.from_square:
            continue
        if p.piece_type == chess.PAWN and not _advanced(color, sq):
            continue
        if not board.attackers(color, sq) and after.piece_at(sq) and \
                move.to_square in after.attackers(color, sq):
            out.append(f"protects {_the(board, sq)}, which had no defender")
    return out


def protected_squares(board: chess.Board, move: chess.Move) -> list:
    """The squares `protects` names."""
    color = board.turn
    after = board.copy(stack=False)
    after.push(move)
    return [sq for sq, p in board.piece_map().items()
            if p.color == color and p.piece_type != chess.KING and sq != move.from_square
            and (p.piece_type != chess.PAWN or _advanced(color, sq))
            and not board.attackers(color, sq) and after.piece_at(sq)
            and move.to_square in after.attackers(color, sq)]


def _reply(board: chess.Board, move: chess.Move, line: str):
    """The answer to `move` in its engine line ("1. Qxd5 Qxd5 2. ...")."""
    sans = [t for t in line.split() if not t[0].isdigit()]
    if len(sans) < 2:
        return None
    b = board.copy(stack=False)
    b.push(move)
    try:
        return b.parse_san(sans[1])
    except ValueError:
        return None


def _tempting(board: chess.Board, mv: chess.Move) -> int:
    if board.is_capture(mv):
        cap = board.piece_at(mv.to_square)
        return 10 + (VALUE[cap.piece_type] if cap else 1)
    if board.gives_check(mv):
        return 8
    return 0


def build(board: chess.Board, engine) -> Brief:
    me = board.turn
    br = Brief()
    try:
        engine.configure({"Clear Hash": None})
    except Exception:
        pass
    ea = run_stockfish(chess.Board(board.fen()), engine=engine, multipv=1)
    verdict = eval_phrase(ea.score_cp, ea.score_mate)[0]
    mat = material_lines(board)[-1].replace("Material difference: ", "").split(". ")[0].rstrip(".")
    counts = (len(board.pieces(chess.PAWN, chess.WHITE)), len(board.pieces(chess.PAWN, chess.BLACK)))
    overview = f"{side_name(me)} is to move, and {verdict}. {mat}."
    named = name_at(board)
    if named:
        overview += f" This is the {named[1]} ({named[0]})."
    br.overview = overview

    pawns = [f"Pawns: White has {NUMBER.get(counts[0], counts[0])}, Black "
             f"{NUMBER.get(counts[1], counts[1])}."] + pawn_lines(board)
    br.sections.append(("Material and pawns", pawns))
    br.sections.append(("Kings", king_lines(board)))
    br.sections.append(("Pieces", piece_lines(board)))

    threats = attack_lines(board)
    t = threat(engine, board)
    if t:
        threats.append(Line(f"If it were {side_name(t.side)}'s move: {t.text()}",
                            [arrow(t.move.from_square, t.move.to_square, "threat")]))
    if not threats:
        threats = ["Nothing is attacked, and neither side has a threat."]
    br.sections.append(("Threats", threats))

    rows = explain_all(board, engine)["moves"]
    best = rows[0]["win"] if rows else 50.0
    cands = []
    for r in rows[:CANDIDATES]:
        if r["drop"] >= EQUAL * 2 and cands:
            break
        mv = chess.Move.from_uci(r["uci"])
        shapes = [arrow(mv.from_square, mv.to_square, "green" if not cands else "paleGreen")]
        shapes += [arrow(mv.to_square, sq, "defend") for sq in protected_squares(board, mv)]
        does = [f"It {x}." for x in protects(board, mv)]
        does += [e.rstrip(".") + "." for e in describe_move_effects(board, mv)[:2]]
        note = "" if not cands else (" (practically as good as the first)" if r["drop"] < EQUAL else "")
        line = (f"{r['san']}{note}: {side_name(me)}'s winning chance {r['win']:.0f}%. "
                + " ".join(does) + (" " if does else "") + f"Line: {r['line']}")
        cands.append(Line(line, shapes))
        br.lines.append(r["line"])
    if rows:
        br.best_san = rows[0]["san"]
    br.sections.append((f"Candidate moves for {side_name(me)}", cands))

    avoid = []
    for r in sorted(rows, key=lambda r: -_tempting(board, chess.Move.from_uci(r["uci"]))):
        mv = chess.Move.from_uci(r["uci"])
        if len(avoid) >= AVOID or not _tempting(board, mv):
            break
        lab = label_for_drop(max(0.0, best - r["win"]))
        if lab not in ("Mistake", "Blunder"):
            continue
        mark = "??" if lab == "Blunder" else "?"
        why = r["text"].split(": ", 1)[1] if ": " in r["text"] else r["text"]
        shapes = [arrow(mv.from_square, mv.to_square, "red")]
        reply = _reply(board, mv, r["line"])
        if reply:
            shapes.append(arrow(reply.from_square, reply.to_square, "paleRed"))
        avoid.append(Line(f"{r['san']}{mark}: {why}.", shapes))
    br.sections.append(("Moves to avoid", avoid))
    return br
