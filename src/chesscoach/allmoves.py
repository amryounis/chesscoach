"""
Explain all moves: every legal move in a position with a short verdict.

Fritz's "Explain all moves" answers the question players ask most: "why not
my move?". Stockfish scores every legal move (MultiPV over all of them, at a
modest depth so it takes seconds), and code says what each one does,
from the engine's line after it:

  Checkmate / Forces mate in 3 / Allows mate in 2
  Best move; As good as the best; Good           (+ "recaptures", "check",
                                                  "castles", "takes the ...")
  Inaccuracy / Mistake / Blunder: loses a knight for two pawns,
                                  or: refuted by 9...Qb6

Material is counted along the engine's line until the captures stop, so a
move that drops a piece is said to lose it even when the line trades on.
No model is involved: every word comes from the board and the engine.
"""

from __future__ import annotations

from typing import Optional

import chess
import chess.engine

from . import config
from .assessment import label_for_drop, win_pct
from .describe import PIECE_NAMES
from .engine import white_mate

DEPTH = 12               # a quick search: every move, in seconds
LINE_PLIES = 8           # how far the line is followed for material...
MAX_PLIES = 14           # ...and on while captures go on
EQUAL = 3.0              # points of winning chance: as good as the best
GOOD = 10.0
VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}


def _a(piece: int, n: int = 1) -> str:
    name = PIECE_NAMES[piece]
    if n == 1:
        return ("an " if name[0] in "aeiou" else "a ") + name
    words = {2: "two", 3: "three", 4: "four"}
    return f"{words.get(n, n)} {name}s"


def _pieces(types: list) -> str:
    counts = {}
    for t in types:
        counts[t] = counts.get(t, 0) + 1
    parts = [_a(t, counts[t]) for t in sorted(counts, key=lambda t: -VALUES[t])]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def trade(board: chess.Board, line: list) -> tuple:
    """(lost, won): piece types the side to move loses and wins along the
    line (Moves), followed past LINE_PLIES while captures go on; identical
    pieces traded both ways cancel. A promotion counts as winning the new
    piece for the pawn."""
    b = board.copy(stack=False)
    me = b.turn
    lost, won = [], []
    for k, mv in enumerate(line[:MAX_PLIES]):
        if k >= LINE_PLIES and not b.is_capture(mv):
            break
        if mv not in b.legal_moves:
            break
        mine = b.turn == me
        if b.is_capture(mv):
            victim = chess.PAWN if b.is_en_passant(mv) else b.piece_type_at(mv.to_square)
            (won if mine else lost).append(victim)
        if mv.promotion:
            (won if mine else lost).append(mv.promotion)
            (lost if mine else won).append(chess.PAWN)
        b.push(mv)
    for t in list(lost):
        if t in won:
            lost.remove(t)
            won.remove(t)
    return lost, won


def _material_words(lost: list, won: list) -> str:
    net = sum(VALUES[t] for t in won) - sum(VALUES[t] for t in lost)
    if net <= -1 and lost:
        return f"loses {_pieces(lost)}" + (f" for {_pieces(won)}" if won else "")
    if net >= 1 and won:
        return f"wins {_pieces(won)}" + (f" for {_pieces(lost)}" if lost else "")
    return ""


def _kind(board: chess.Board, move: chess.Move) -> str:
    """What a sound move is, in a few words."""
    if board.is_castling(move):
        return "castles"
    if board.is_capture(move):
        last = board.peek() if board.move_stack else None
        if last is not None and last.to_square == move.to_square:
            return "recaptures"
        victim = chess.PAWN if board.is_en_passant(move) else board.piece_type_at(move.to_square)
        return f"takes the {PIECE_NAMES[victim]} on {chess.square_name(move.to_square)}"
    if board.gives_check(move):
        return "check"
    if move.promotion:
        return f"promotes to {_a(move.promotion)}"
    return ""


def _label_san(board: chess.Board, line: list, k: int) -> str:
    """'9...Qb6': move k of the line (0 = the move itself), numbered."""
    b = board.copy(stack=False)
    for mv in line[:k]:
        b.push(mv)
    mv = line[k]
    n = b.fullmove_number
    return f"{n}. {b.san(mv)}" if b.turn == chess.WHITE else f"{n}...{b.san(mv)}"


def explain_all(board: chess.Board, engine, depth: Optional[int] = None) -> dict:
    """Every legal move with a verdict, best first.
    -> {fen, depth, moves: [{uci, san, win, drop, label, text, line}]}"""
    legal = list(board.legal_moves)
    if not legal:
        raise ValueError("No legal moves here: the game is over.")
    depth = depth or min(DEPTH, config.settings.depth)
    cap = config.settings.max_seconds
    try:
        engine.configure({"Clear Hash": None})
    except Exception:
        pass
    infos = engine.analyse(chess.Board(board.fen()),
                           chess.engine.Limit(depth=depth, time=cap if cap and cap > 0 else None),
                           multipv=len(legal))
    me = board.turn
    rows = []
    for info in infos:
        pv = list(info.get("pv", []))
        if not pv or pv[0] not in legal:
            continue
        sc = info["score"].white()
        mate = white_mate(sc) if sc.is_mate() else None
        cp = None if sc.is_mate() else sc.score()
        w = win_pct(cp, mate, me == chess.WHITE)
        rows.append({"move": pv[0], "pv": pv, "win": 50.0 if w is None else w,
                     "mate": mate, "depth": info.get("depth", depth)})
    seen = {r["move"] for r in rows}
    for mv in legal:                       # a move the engine left out: score it alone
        if mv not in seen:
            info = engine.analyse(chess.Board(board.fen()), chess.engine.Limit(depth=max(4, depth - 4)),
                                  root_moves=[mv])
            sc = info["score"].white()
            mate = white_mate(sc) if sc.is_mate() else None
            w = win_pct(None if sc.is_mate() else sc.score(), mate, me == chess.WHITE)
            rows.append({"move": mv, "pv": list(info.get("pv", [mv])), "win": 50.0 if w is None else w,
                         "mate": mate, "depth": info.get("depth", 0)})
    rows.sort(key=lambda r: -r["win"])
    best = rows[0]["win"]
    out = []
    for k, r in enumerate(rows):
        mv, pv = r["move"], r["pv"]
        san = board.san(mv)
        after = board.copy(stack=False)
        after.push(mv)
        drop = max(0.0, best - r["win"])
        mate = r["mate"]
        mine_mate = mate is not None and (mate > 0) == (me == chess.WHITE)
        kind = _kind(board, mv)
        if after.is_checkmate():
            label, text = "Best", "Checkmate!"
        elif mine_mate:
            label, text = ("Best" if drop < EQUAL else "Good"), f"Forces mate in {abs(mate)}"
        elif mate is not None:
            label, text = "Blunder", f"Allows mate in {abs(mate)}" + (
                f": {_label_san(board, pv, 1)}" if len(pv) > 1 else "")
        elif drop < EQUAL:
            label = "Best"
            text = ("Best move" if k == 0 else "As good as the best") + (f": {kind}" if kind else "")
        elif drop < GOOD:
            label, text = "Good", "Good" + (f": {kind}" if kind else "")
        else:
            label = label_for_drop(drop)
            lost, won = trade(board, pv)
            words = _material_words(lost, won)
            ref = _label_san(board, pv, 1) if len(pv) > 1 else ""
            if words.startswith("loses"):
                reason = words + (f" (after {ref})" if ref else "")
            elif kind == "check":
                reason = "the check is easily answered" + (f" ({ref})" if ref else "")
            else:
                reason = f"refuted by {ref}" if ref else "the position gets worse"
            text = f"{label}: {reason}"
        out.append({"uci": mv.uci(), "san": san, "win": round(r["win"], 1),
                    "drop": round(drop, 1), "label": label, "text": text,
                    "line": board.variation_san(pv[:6]) if pv else san})
    return {"fen": board.fen(), "depth": min(r["depth"] for r in rows) if rows else depth,
            "moves": out}
