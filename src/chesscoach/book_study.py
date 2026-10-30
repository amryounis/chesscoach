"""
Opening study: an opening book walked once, the idea of every popular move
explained by the coach, for the opening explorer.

"Don't memorise lines, understand the ideas": for each position the book
reaches in practice, and each move the book plays there, the coach writes
two to four sentences on what the move is for (development, the centre,
king safety, the typical plan of the opening), from facts code works out:

  - the opening it reaches (Lichess opening table) and that opening's idea
  - what the move does: develops a piece, gains central squares, opens a
    line for a bishop or the queen, castles, attacks or defends a piece
  - how often the book plays it, the other book moves here, and the book's
    main answers to it, with their opening names
  - Stockfish's verdict after it, and whether Stockfish prefers another move

Every comment goes through the board check used for game reviews; a false
claim gets one rewrite, then its sentence is removed.

How much of the book: a full book has hundreds of thousands of positions,
far more than a local model can comment on. The walk follows a move only
when the book gives it a real share of the position (min_share), and a
line only while it stays common (the product of the shares, min_line), up
to `plies` half-moves; each position is studied once, however it is
reached. `chesscoach book study --dry-run` says how many moves that is and
roughly how long it takes. The run is resumable: what is done is kept.

Everything goes into openings.db in the games folder.
"""

from __future__ import annotations

import datetime
import re
import sqlite3
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import chess
import chess.engine
import chess.polyglot

from . import config
from .assessment import eval_phrase, win_pct
from .describe import PIECE_NAMES, describe_move, move_notes, side_name
from .opening import idea_for, name_at

DEFAULTS = {"plies": 20, "min_share": 0.05, "min_line": 0.002, "max_moves": 4}
CENTRE = (chess.D4, chess.E4, chess.D5, chess.E5)
SCHEMA = 1


# --- which positions and moves --------------------------------------------------

@dataclass
class Plan:
    positions: dict = field(default_factory=dict)     # epd -> {fen, ply, line, moves}
    order: list = field(default_factory=list)         # epds, shallowest first

    @property
    def moves(self) -> int:
        return sum(len(p["moves"]) for p in self.positions.values())


def _san_line(sans: list, start_ply: int = 0) -> str:
    out = []
    for i, s in enumerate(sans):
        ply = start_ply + i
        if ply % 2 == 0:
            out.append(f"{ply // 2 + 1}.{s}")
        elif i == 0:
            out.append(f"{ply // 2 + 1}...{s}")
        else:
            out.append(s)
    return " ".join(out)


def plan(book_path: str, plies: int = DEFAULTS["plies"], min_share: float = DEFAULTS["min_share"],
         min_line: float = DEFAULTS["min_line"], max_moves: int = DEFAULTS["max_moves"]) -> Plan:
    """Walk the book from the start position, shallowest first."""
    if not book_path or not Path(book_path).exists():
        raise ValueError("No opening book: give a Polyglot .bin file, or set book_path in "
                         "Settings / the config file.")
    p = Plan()
    q = deque([(chess.Board(), 1.0, [])])
    with chess.polyglot.open_reader(book_path) as reader:
        while q:
            board, prob, sans = q.popleft()
            epd = board.epd()
            if epd in p.positions or len(sans) >= plies:
                continue
            weights: dict = {}
            for e in reader.find_all(board):
                if e.move in board.legal_moves:
                    weights[e.move] = weights.get(e.move, 0) + e.weight
            total = sum(weights.values())
            if not total:
                continue
            kept = []
            for mv, w in sorted(weights.items(), key=lambda x: (-x[1], x[0].uci()))[:max_moves]:
                share = w / total
                if share < min_share:
                    continue
                kept.append({"uci": mv.uci(), "san": board.san(mv), "weight": w,
                             "share": round(share, 4)})
                if prob * share >= min_line:
                    nb = board.copy(stack=False)
                    nb.push(mv)
                    q.append((nb, prob * share, sans + [board.san(mv)]))
            if kept:
                p.positions[epd] = {"fen": board.fen(), "ply": len(sans),
                                    "line": _san_line(sans), "moves": kept}
                p.order.append(epd)
    return p


def estimate(p: Plan, secs_per_comment: float = 15.0, secs_per_position: float = 1.5) -> dict:
    by_ply: dict = {}
    for pos in p.positions.values():
        by_ply[pos["ply"]] = by_ply.get(pos["ply"], 0) + len(pos["moves"])
    n_pos = len(p.positions) + p.moves             # positions, and each move's resulting position
    hours = (p.moves * secs_per_comment + n_pos * secs_per_position) / 3600
    return {"positions": len(p.positions), "moves": p.moves, "by_ply": dict(sorted(by_ply.items())),
            "hours": round(hours, 1)}


# --- storage ----------------------------------------------------------------------

def study_path() -> Path:
    from .library import library_dir
    return library_dir() / "openings.db"


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    path = path or study_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    if con.execute("PRAGMA user_version").fetchone()[0] != SCHEMA:
        con.executescript("""
            DROP TABLE IF EXISTS positions; DROP TABLE IF EXISTS moves; DROP TABLE IF EXISTS meta;
            CREATE TABLE positions (epd TEXT PRIMARY KEY, fen TEXT, ply INTEGER, line TEXT,
                eco TEXT, name TEXT, cp INTEGER, mate INTEGER, best_san TEXT, depth INTEGER);
            CREATE TABLE moves (epd TEXT, uci TEXT, san TEXT, weight INTEGER, share REAL,
                idea TEXT, issues TEXT, model TEXT, written TEXT, PRIMARY KEY (epd, uci));
            CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
        """)
        con.execute(f"PRAGMA user_version = {SCHEMA}")
        con.commit()
    return con


def _store_plan(con: sqlite3.Connection, p: Plan, book_path: str) -> None:
    for epd in p.order:
        pos = p.positions[epd]
        board = chess.Board(pos["fen"])
        hit = name_at(board)
        con.execute("INSERT INTO positions (epd, fen, ply, line, eco, name) VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(epd) DO UPDATE SET ply = MIN(ply, excluded.ply)",
                    (epd, pos["fen"], pos["ply"], pos["line"],
                     hit[0] if hit else "", hit[1] if hit else ""))
        for m in pos["moves"]:
            con.execute("INSERT INTO moves (epd, uci, san, weight, share) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(epd, uci) DO UPDATE SET weight = excluded.weight, "
                        "share = excluded.share", (epd, m["uci"], m["san"], m["weight"], m["share"]))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('book', ?)", (str(book_path),))
    con.commit()


def _evaluate(con: sqlite3.Connection, engine, board: chess.Board, depth: int) -> sqlite3.Row:
    epd = board.epd()
    row = con.execute("SELECT * FROM positions WHERE epd = ?", (epd,)).fetchone()
    if row is not None and (row["cp"] is not None or row["mate"] is not None or board.is_game_over()):
        return row
    from .engine import run_stockfish
    ea = run_stockfish(board, depth=depth, multipv=1, engine=engine)
    hit = name_at(board)
    if row is None:
        con.execute("INSERT INTO positions (epd, fen, ply, line, eco, name) VALUES (?,?,?,?,?,?)",
                    (epd, board.fen(), None, "", hit[0] if hit else "", hit[1] if hit else ""))
    con.execute("UPDATE positions SET cp = ?, mate = ?, best_san = ?, depth = ? WHERE epd = ?",
                (ea.score_cp, ea.score_mate, ea.best_move, ea.depth, epd))
    con.commit()
    return con.execute("SELECT * FROM positions WHERE epd = ?", (epd,)).fetchone()


# --- the facts behind one move's idea -------------------------------------------------

def _num(board: chess.Board, san: str) -> str:
    return f"{board.fullmove_number}.{san}" if board.turn == chess.WHITE else \
        f"{board.fullmove_number}...{san}"


def _centre(board: chess.Board, color: bool) -> set:
    return {sq for sq in CENTRE if board.attackers(color, sq)} | \
        {sq for sq in CENTRE if (p := board.piece_at(sq)) and p.color == color}


def _reach(board: chess.Board, sq: int) -> int:
    return len(board.attacks(sq))


def development_facts(board: chess.Board, move: chess.Move) -> list:
    """What an opening move does, in plain words, from the board alone."""
    me = board.turn
    who = side_name(me)
    piece = board.piece_at(move.from_square)
    after = board.copy(stack=False)
    after.push(move)
    out = []
    if board.is_castling(move):
        out.append(f"{who} castles {'kingside' if board.is_kingside_castling(move) else 'queenside'}: "
                   "the king goes to safety and a rook comes towards the centre")
    elif piece and piece.piece_type in (chess.KNIGHT, chess.BISHOP):
        home = chess.BB_RANK_1 if me == chess.WHITE else chess.BB_RANK_8
        if chess.BB_SQUARES[move.from_square] & home:
            out.append(f"It develops the {PIECE_NAMES[piece.piece_type]} (its first move)")
    gained = _centre(after, me) - _centre(board, me)
    if gained:
        names = ", ".join(sorted(chess.square_name(s) for s in gained))
        out.append(f"{who} now controls or occupies {'the central square' if len(gained) == 1 else 'the central squares'} {names}")
    if piece and piece.piece_type == chess.PAWN:
        for sq, p in after.piece_map().items():
            if p.color == me and p.piece_type in (chess.BISHOP, chess.QUEEN) and sq != move.to_square:
                gain = _reach(after, sq) - _reach(board, sq)
                if gain >= 2:
                    out.append(f"It opens a diagonal for the {PIECE_NAMES[p.piece_type]} on "
                               f"{chess.square_name(sq)} ({gain} more squares)")
    if board.is_capture(move):
        cap = board.piece_at(move.to_square)
        if cap:
            out.append(f"It captures the {PIECE_NAMES[cap.piece_type]} on {chess.square_name(move.to_square)}")
    return out


def move_facts(board: chess.Board, move: chess.Move, here: sqlite3.Row, after_row: sqlite3.Row,
               siblings: list, replies: list) -> list:
    """The facts given to the coach for one book move. `siblings`: the book
    moves in this position [{san, share}], `replies`: the book's answers
    after it [{san, share}]."""
    me = board.turn
    san = board.san(move)
    after = board.copy(stack=False)
    after.push(move)
    facts = []
    if here["line"]:
        facts.append(f"The position is after {here['line']}")
    before_name = name_at(board)
    after_name = name_at(after)
    if after_name and (not before_name or after_name[1] != before_name[1]):
        facts.append(f"{_num(board, san)} reaches the {after_name[1]} ({after_name[0]})")
        key, idea = idea_for(after_name[1])
        if idea:
            facts.append(f"The general idea of the {key}: {idea}")
    elif before_name:
        facts.append(f"The opening is the {before_name[1]} ({before_name[0]})")
    facts.append(describe_move(board, move))
    facts += development_facts(board, move)
    # Stockfish: how good the move is, against its own first choice
    best_win = win_pct(here["cp"], here["mate"], me == chess.WHITE)
    after_win = win_pct(after_row["cp"], after_row["mate"], me == chess.WHITE)
    fine = best_win is None or after_win is None or best_win - after_win < 5
    for n in move_notes(board, move):
        if "IGNORES a threat" in n and fine:
            continue                    # in the book and sound: an "attacked" pawn is theory
        if n not in facts:
            facts.append(n)
    facts.append(f"Stockfish after {_num(board, san)}: {eval_phrase(after_row['cp'], after_row['mate'])[0]}")
    if here["best_san"] and here["best_san"] != san:
        facts.append(f"Stockfish's first choice here is {_num(board, here['best_san'])}"
                     + ("; this move is about as good" if fine else "; this move gives up a little"))
    me_share = next((s["share"] for s in siblings if s["san"] == san), None)
    if me_share is not None:
        top = max(s["share"] for s in siblings)
        tied = sum(1 for s in siblings if s["share"] == top) > 1
        lead = ("The book's most played move here" if me_share == top and not tied else
                "One of the book's main moves here" if me_share == top else "A book move here")
        facts.append(f"{lead} ({round(100 * me_share)}% of the book's games)")
    others = [s for s in siblings if s["san"] != san]
    if others:
        facts.append("Other book moves here: " + ", ".join(
            f"{_num(board, s['san'])}{' (' + s['name'] + ')' if s.get('name') else ''} {round(100 * s['share'])}%"
            for s in others))
    if replies:
        facts.append(f"The book's main answers to {_num(board, san)}: " + ", ".join(
            f"{_num(after, r['san'])}{' (' + r['name'] + ')' if r.get('name') else ''} {round(100 * r['share'])}%"
            for r in replies[:3]))
    return facts


# --- the coach's comment ------------------------------------------------------------

SYSTEM = """You are a chess coach explaining an opening to a club player (1200-1800) who should understand ideas, not memorise moves.

You are given verified facts about one move in an opening. In 2 to 4 sentences, explain the idea behind the move: what it aims at (development, the centre, king safety, the typical plan of this opening) and how it fits with the moves the book expects next.

Rules:
- Use only the facts given. Do not invent threats, tactics, pieces or squares.
- You may name the moves that appear in the facts, in the same notation; no other moves and no variations.
- No numbers for evaluations or percentages. Plain prose, no headings, no lists, no bold.
- Do not use the words: winning, losing, decisive, crushing, best move."""

_SAN = re.compile(r"(?<![\w-])(?:\d+\.(?:\.\.)?)?((?:[KQRBN][a-h]?[1-8]?x?[a-h][1-8]|[a-h]x?[a-h]?[1-8](?:=[QRBN])?|O-O(?:-O)?)[+#]?)(?![\w-])")
BANNED = ("winning", "losing", "decisive", "crushing", "best move")


def text_issues(text: str, allowed_san: set) -> list:
    issues = []
    for m in _SAN.finditer(text):
        tok = m.group(1).rstrip("+#")
        if tok not in allowed_san and len(tok) > 2:
            issues.append(f"names a move that is not in the facts: {m.group(0)}")
    low = text.lower()
    issues += [f"banned word used: '{w}'" for w in BANNED if re.search(rf"\b{w}\b", low)]
    if re.search(r"\d+(?:\.\d+)?\s*%|[+-]\d\.\d\d", text):
        issues.append("gives a number for an evaluation or a percentage")
    return issues


def tidy(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text or "")
    text = re.sub(r"^\s*(?:[-*]|#+)\s*", "", text, flags=re.M)
    return " ".join(text.split())


def write_idea(model: str, board: chess.Board, move: chess.Move, facts: list,
               boards: list, allowed_san: set, llm=None) -> tuple:
    """(text, issues): the coach's comment, checked; a false claim or a
    broken rule gets one rewrite, then the sentence with it is removed."""
    from .grounding import ground, remove_failed
    from .narrator import call_llm
    llm = llm or call_llm
    prompt = ("FACTS (verified):\n" + "\n".join(f"- {f}" for f in facts)
              + f"\n\nExplain the idea behind {_num(board, board.san(move))}.")
    msgs = [{"role": "user", "content": prompt}]
    text, err = llm(model, msgs, system=SYSTEM)
    if err:
        raise RuntimeError(err)
    text = tidy(text)

    def check(t):
        fails = ground(t, boards, move, played=boards[1:])
        return text_issues(t, allowed_san), fails
    issues, fails = check(text)
    if issues or fails:
        fix = ("Your explanation has problems:\n" + "\n".join(f"- {i}" for i in issues)
               + "".join(f"\n- false on the board: \"{f.claim}\" ({f.correction})" for f in fails)
               + "\nRewrite it, fixing only those problems. Leave out anything you cannot "
                 "state from the facts.")
        text2, err2 = llm(model, msgs + [{"role": "assistant", "content": text},
                                        {"role": "user", "content": fix}], system=SYSTEM)
        if not err2:
            text2 = tidy(text2)
            i2, f2 = check(text2)
            if len(i2) + len(f2) <= len(issues) + len(fails):
                text, issues, fails = text2, i2, f2
    if fails:
        text = remove_failed(text, fails)
    # a sentence that still breaks a rule goes too
    if issues:
        keep = [s for s in re.split(r"(?<=[.!?])\s+", text) if not text_issues(s, allowed_san)]
        text = " ".join(keep)
    return tidy(text), issues + [f"removed: {f.claim}" for f in fails]


# --- the run --------------------------------------------------------------------------

def run(book_path: str, model: str, plies: int = DEFAULTS["plies"],
        min_share: float = DEFAULTS["min_share"], min_line: float = DEFAULTS["min_line"],
        max_moves: int = DEFAULTS["max_moves"], depth: int = 16, redo: bool = False,
        progress: Optional[Callable] = None, should_stop: Optional[Callable] = None,
        llm=None, path: Optional[Path] = None) -> dict:
    """Study the book: evaluate every position, comment on every move.
    Already-written ideas are kept unless `redo`. Returns counts."""
    progress = progress or (lambda *a: None)
    stop = should_stop or (lambda: False)
    p = plan(book_path, plies, min_share, min_line, max_moves)
    con = connect(path)
    _store_plan(con, p, book_path)
    config.settings.require_stockfish()
    if llm is None:
        from .narrator import check_ollama
        check_ollama(model)
    engine = chess.engine.SimpleEngine.popen_uci(config.settings.stockfish_path)
    done = written = 0
    t0 = time.time()
    todo = [(epd, m) for epd in p.order for m in p.positions[epd]["moves"]]
    try:
        for epd, m in todo:
            if stop():
                break
            done += 1
            row = con.execute("SELECT idea FROM moves WHERE epd = ? AND uci = ?", (epd, m["uci"])).fetchone()
            if row and row["idea"] and not redo:
                continue
            board = chess.Board(p.positions[epd]["fen"])
            move = chess.Move.from_uci(m["uci"])
            here = _evaluate(con, engine, board, depth)
            after = board.copy(stack=False)
            after.push(move)
            after_row = _evaluate(con, engine, after, depth)
            siblings = [dict(s, name=(name_at(_after(board, s["uci"])) or ("", ""))[1])
                        for s in p.positions[epd]["moves"]]
            child = p.positions.get(after.epd())
            replies = [dict(r, name=(name_at(_after(after, r["uci"])) or ("", ""))[1])
                       for r in (child["moves"] if child else _book_replies(book_path, after))]
            facts = move_facts(board, move, here, after_row, siblings, replies)
            allowed = {s["san"] for s in siblings} | {r["san"] for r in replies} | \
                      ({here["best_san"]} if here["best_san"] else set())
            boards = [board, after] + [_after(after, r["uci"]) for r in replies[:3]]
            text, issues = write_idea(model, board, move, facts, boards, allowed, llm=llm)
            con.execute("UPDATE moves SET idea = ?, issues = ?, model = ?, written = ? "
                        "WHERE epd = ? AND uci = ?",
                        (text, "; ".join(issues), model,
                         datetime.datetime.now().isoformat(timespec="seconds"), epd, m["uci"]))
            con.commit()
            written += 1
            left = (time.time() - t0) / written * (len(todo) - done)
            progress(done, len(todo), f"{p.positions[epd]['line'] or 'start'}: {_num(board, m['san'])}",
                     left)
    finally:
        engine.quit()
        con.close()
    return {"moves": len(todo), "written": written, "done": done}


def _after(board: chess.Board, uci: str) -> chess.Board:
    b = board.copy(stack=False)
    b.push(chess.Move.from_uci(uci))
    return b


def _book_replies(book_path: str, board: chess.Board, n: int = 3) -> list:
    weights: dict = {}
    with chess.polyglot.open_reader(book_path) as r:
        for e in r.find_all(board):
            if e.move in board.legal_moves:
                weights[e.move] = weights.get(e.move, 0) + e.weight
    total = sum(weights.values()) or 1
    top = sorted(weights.items(), key=lambda x: -x[1])[:n]
    return [{"uci": mv.uci(), "san": board.san(mv), "share": round(w / total, 4)} for mv, w in top]


# --- reading, for the explorer ----------------------------------------------------------

def at(board: chess.Board, path: Optional[Path] = None) -> Optional[dict]:
    """The study's view of a position: {eco, name, verdict, best_san,
    moves: [{uci, san, share, idea, verdict}]}, or None if not studied."""
    path = path or study_path()
    if not path.exists():
        return None
    con = connect(path)
    try:
        row = con.execute("SELECT * FROM positions WHERE epd = ?", (board.epd(),)).fetchone()
        moves = con.execute("SELECT * FROM moves WHERE epd = ? ORDER BY share DESC",
                            (board.epd(),)).fetchall()
        if row is None and not moves:
            return None
        out_moves = []
        for m in moves:
            after = _after(board, m["uci"])
            arow = con.execute("SELECT cp, mate FROM positions WHERE epd = ?", (after.epd(),)).fetchone()
            out_moves.append({"uci": m["uci"], "san": m["san"], "share": m["share"],
                              "idea": m["idea"] or "",
                              "verdict": (eval_phrase(arow["cp"], arow["mate"])[0]
                                          if arow and (arow["cp"] is not None or arow["mate"] is not None)
                                          else "")})
        return {"eco": row["eco"] if row else "", "name": row["name"] if row else "",
                "verdict": (eval_phrase(row["cp"], row["mate"])[0]
                            if row and (row["cp"] is not None or row["mate"] is not None) else ""),
                "best_san": row["best_san"] if row else "", "moves": out_moves}
    finally:
        con.close()


def idea(board: chess.Board, move: chess.Move, path: Optional[Path] = None) -> str:
    """The idea written for `move` in `board`, "" if none."""
    path = path or study_path()
    if not path.exists():
        return ""
    con = connect(path)
    try:
        r = con.execute("SELECT idea FROM moves WHERE epd = ? AND uci = ?",
                        (board.epd(), move.uci())).fetchone()
        return (r["idea"] or "") if r else ""
    finally:
        con.close()


def summary(path: Optional[Path] = None) -> dict:
    path = path or study_path()
    if not path.exists():
        return {"positions": 0, "moves": 0, "ideas": 0, "book": ""}
    con = connect(path)
    try:
        return {"positions": con.execute("SELECT COUNT(*) FROM positions WHERE ply IS NOT NULL").fetchone()[0],
                "moves": con.execute("SELECT COUNT(*) FROM moves").fetchone()[0],
                "ideas": con.execute("SELECT COUNT(*) FROM moves WHERE idea IS NOT NULL AND idea != ''").fetchone()[0],
                "book": (con.execute("SELECT v FROM meta WHERE k = 'book'").fetchone() or [""])[0]}
    finally:
        con.close()
