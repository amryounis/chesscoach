"""
Your games: a folder of ordinary PGN files, with an index to search them.

  Played.pgn     every game played in the app, saved as it ends (or when a
                 new one starts before it ends: result "*")
  Reviewed.pgn   every reviewed game as an annotated PGN (a re-review
                 replaces it)
  *.pgn          files you add; any chess program can open all of them
  reviews/       each review in full (JSON), so opening a reviewed game shows
                 its review again instead of running Stockfish again
  library.db     the index (SQLite): rebuilt for any file whose size or time
                 changed, so files edited elsewhere stay in step

The PGN files are the data; the index can always be deleted and rebuilt.
The first 30 plies of every game also go into a positions table, which is
what the opening explorer reads.
"""

from __future__ import annotations

import datetime
import hashlib
import io
import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Optional

import chess
import chess.pgn

from . import config

PLAYED, REVIEWED = "Played.pgn", "Reviewed.pgn"
EXPLORER_PLIES = 30
SCHEMA = 1


def library_dir() -> Path:
    d = config.settings.library_dir
    return Path(d).expanduser() if d else config.user_config_path().parent / "games"


def game_id(game: chess.pgn.Game) -> str:
    """The same id as history.game_id: start position and moves."""
    moves = " ".join(m.uci() for m in game.mainline_moves())
    return hashlib.sha1((game.board().fen() + " " + moves).encode()).hexdigest()[:12]


# --- the index ---------------------------------------------------------------

def _connect(folder: Path) -> sqlite3.Connection:
    folder.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(folder / "library.db")
    con.row_factory = sqlite3.Row
    v = con.execute("PRAGMA user_version").fetchone()[0]
    if v != SCHEMA:
        con.executescript("""
            DROP TABLE IF EXISTS files; DROP TABLE IF EXISTS games; DROP TABLE IF EXISTS positions;
            CREATE TABLE files (name TEXT PRIMARY KEY, mtime REAL, size INTEGER);
            CREATE TABLE games (id INTEGER PRIMARY KEY, file TEXT, offset INTEGER, number INTEGER,
                white TEXT, black TEXT, white_elo TEXT, black_elo TEXT, result TEXT, date TEXT,
                event TEXT, eco TEXT, opening TEXT, plies INTEGER, gid TEXT);
            CREATE INDEX games_file ON games(file);
            CREATE INDEX games_gid ON games(gid);
            CREATE TABLE positions (epd TEXT, uci TEXT, san TEXT, game INTEGER, ply INTEGER);
            CREATE INDEX positions_epd ON positions(epd);
        """)
        con.execute(f"PRAGMA user_version = {SCHEMA}")
        con.commit()
    return con


def pgn_files(folder: Optional[Path] = None) -> list:
    """The library's PGN files: Played and Reviewed first, then the rest."""
    folder = folder or library_dir()
    if not folder.exists():
        return []
    names = sorted(p.name for p in folder.glob("*.pgn") if p.is_file())
    first = [n for n in (PLAYED, REVIEWED) if n in names]
    return first + [n for n in names if n not in first]


def _index_file(con: sqlite3.Connection, folder: Path, name: str) -> int:
    con.execute("DELETE FROM positions WHERE game IN (SELECT id FROM games WHERE file = ?)", (name,))
    con.execute("DELETE FROM games WHERE file = ?", (name,))
    path = folder / name
    n = 0
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        while True:
            offset = f.tell()
            game = chess.pgn.read_game(f)
            if game is None:
                break
            moves = list(game.mainline_moves())
            if not moves and not game.headers.get("White"):
                continue
            n += 1
            h = game.headers
            eco, opening_name = h.get("ECO", ""), h.get("Opening", "")
            if not eco and not opening_name:
                eco, opening_name = _opening_of(game.board(), moves)
            cur = con.execute(
                "INSERT INTO games (file, offset, number, white, black, white_elo, black_elo, "
                "result, date, event, eco, opening, plies, gid) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (name, offset, n, h.get("White", "?"), h.get("Black", "?"),
                 h.get("WhiteElo", ""), h.get("BlackElo", ""), h.get("Result", "*"),
                 h.get("Date", "????.??.??"), h.get("Event", "?"), eco,
                 opening_name, len(moves), game_id(game)))
            gid = cur.lastrowid
            b = game.board()
            rows = []
            for i, m in enumerate(moves[:EXPLORER_PLIES]):
                rows.append((b.epd(), m.uci(), b.san(m), gid, i))
                b.push(m)
            con.executemany("INSERT INTO positions VALUES (?,?,?,?,?)", rows)
    st = path.stat()
    con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?)", (name, st.st_mtime, st.st_size))
    return n


def _opening_of(board: chess.Board, moves: list) -> tuple:
    """(ECO, name) of the last named position the game reached, from the
    openings table, for games whose PGN has no ECO/Opening headers."""
    from .opening import name_at
    b, best = board.copy(), ("", "")
    for m in moves[:EXPLORER_PLIES]:
        b.push(m)
        hit = name_at(b)
        if hit:
            best = hit
    return best


def refresh(folder: Optional[Path] = None) -> sqlite3.Connection:
    """Bring the index in step with the files; return the connection."""
    folder = folder or library_dir()
    con = _connect(folder)
    on_disk = set(pgn_files(folder))
    known = {r["name"]: r for r in con.execute("SELECT * FROM files")}
    for name in set(known) - on_disk:
        con.execute("DELETE FROM positions WHERE game IN (SELECT id FROM games WHERE file = ?)", (name,))
        con.execute("DELETE FROM games WHERE file = ?", (name,))
        con.execute("DELETE FROM files WHERE name = ?", (name,))
    for name in on_disk:
        st = (folder / name).stat()
        k = known.get(name)
        if k is None or k["mtime"] != st.st_mtime or k["size"] != st.st_size:
            _index_file(con, folder, name)
    con.commit()
    return con


# --- reading -----------------------------------------------------------------

SORTS = {"date": "date", "white": "white", "black": "black", "result": "result",
         "event": "event", "eco": "eco", "plies": "plies", "file": "file", "number": "number"}


def query(text: str = "", file: str = "", result: str = "", mine: str = "",
          reviewed: bool = False, sort: str = "date", desc: bool = True,
          limit: int = 500, folder: Optional[Path] = None) -> dict:
    """Games matching the filters: {"games": [row dict], "total": n}.
    text: in either player, the event or the opening; mine: "white" /
    "black" / "any" for games where your name (Settings) has that colour."""
    folder = folder or library_dir()
    con = refresh(folder)
    where, args = [], []
    if file:
        where.append("file = ?"); args.append(file)
    if text:
        like = f"%{text.strip()}%"
        where.append("(white LIKE ? OR black LIKE ? OR event LIKE ? OR opening LIKE ? OR eco LIKE ?)")
        args += [like] * 5
    if result in ("1-0", "0-1", "1/2-1/2", "*"):
        where.append("result = ?"); args.append(result)
    name = (config.settings.player_name or "").strip()
    if mine and name:
        if mine == "white":
            where.append("white = ? COLLATE NOCASE"); args.append(name)
        elif mine == "black":
            where.append("black = ? COLLATE NOCASE"); args.append(name)
        else:
            where.append("(white = ? COLLATE NOCASE OR black = ? COLLATE NOCASE)"); args += [name, name]
    reviewed_ids = {p.stem for p in (folder / "reviews").glob("*.json")} if (folder / "reviews").exists() else set()
    if reviewed:
        if not reviewed_ids:
            return {"games": [], "total": 0}
        where.append(f"gid IN ({','.join('?' * len(reviewed_ids))})"); args += sorted(reviewed_ids)
    sql_where = (" WHERE " + " AND ".join(where)) if where else ""
    total = con.execute(f"SELECT COUNT(*) FROM games{sql_where}", args).fetchone()[0]
    col = SORTS.get(sort, "date")
    rows = con.execute(f"SELECT * FROM games{sql_where} ORDER BY {col} {'DESC' if desc else 'ASC'}, "
                       f"id {'DESC' if desc else 'ASC'} LIMIT ?", args + [int(limit)]).fetchall()
    con.close()
    games = [dict(r) | {"reviewed": r["gid"] in reviewed_ids} for r in rows]
    return {"games": games, "total": total}


def load(game_row: int, folder: Optional[Path] = None) -> tuple:
    """(game, file name) for an index row id."""
    folder = folder or library_dir()
    con = refresh(folder)
    r = con.execute("SELECT file, offset FROM games WHERE id = ?", (int(game_row),)).fetchone()
    con.close()
    if r is None:
        raise ValueError("That game is no longer in the library.")
    with open(folder / r["file"], encoding="utf-8", errors="replace", newline="") as f:
        f.seek(r["offset"])
        game = chess.pgn.read_game(f)
    if game is None:
        raise ValueError("That game could not be read.")
    return game, r["file"]


# --- writing -----------------------------------------------------------------

def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _games_in(path: Path) -> list:
    out = []
    if path.exists():
        with open(path, encoding="utf-8", errors="replace", newline="") as f:
            while (g := chess.pgn.read_game(f)) is not None:
                out.append(g)
    return out


def append_game(game: chess.pgn.Game, name: str = PLAYED, folder: Optional[Path] = None) -> Path:
    folder = folder or library_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(str(game) + "\n\n")
    return path


def save_played(game: chess.pgn.Game, replaces: str = "",
                folder: Optional[Path] = None) -> str:
    """Keep a played game in Played.pgn. `replaces`: the id this same game
    was saved under before (taken back and played on, or finished after an
    interrupted save), so the file holds its last version once. Returns the
    game's id."""
    folder = folder or library_dir()
    path = folder / PLAYED
    gid = game_id(game)
    if replaces and path.exists():
        games = _games_in(path)
        if games and game_id(games[-1]) == replaces:
            games[-1] = game
            _write_atomic(path, "".join(str(g) + "\n\n" for g in games))
            return gid
    append_game(game, PLAYED, folder)
    return gid


def save_review(review, game: Optional[chess.pgn.Game] = None,
                folder: Optional[Path] = None) -> Path:
    """The review in full (reviews/<id>.json) and as an annotated PGN in
    Reviewed.pgn, replacing an earlier review of the same game."""
    from .history import game_id as review_id
    from .pgn_export import annotated_game
    folder = folder or library_dir()
    (folder / "reviews").mkdir(parents=True, exist_ok=True)
    gid = review_id(review)
    data = review.to_dict() if hasattr(review, "to_dict") else dict(review)
    data["reviewed_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    _write_atomic(folder / "reviews" / f"{gid}.json", json.dumps(data))
    path = folder / REVIEWED
    kept = [g for g in _games_in(path) if game_id(g) != gid]
    kept.append(annotated_game(review))
    _write_atomic(path, "".join(str(g) + "\n\n" for g in kept))
    return path


def saved_review(game: chess.pgn.Game, folder: Optional[Path] = None) -> Optional[dict]:
    folder = folder or library_dir()
    path = folder / "reviews" / f"{game_id(game)}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def add_file(src: str, folder: Optional[Path] = None) -> tuple:
    """Copy a PGN file into the library (the original is left alone).
    Returns (file name, number of games)."""
    folder = folder or library_dir()
    folder.mkdir(parents=True, exist_ok=True)
    src_p = Path(src)
    stem, name, k = src_p.stem, src_p.name, 1
    while (folder / name).exists() or name in (PLAYED, REVIEWED):
        k += 1
        name = f"{stem} ({k}).pgn"
    shutil.copyfile(src_p, folder / name)
    con = refresh(folder)
    n = con.execute("SELECT COUNT(*) FROM games WHERE file = ?", (name,)).fetchone()[0]
    con.close()
    if n == 0:
        (folder / name).unlink()
        refresh(folder).close()
        raise ValueError("No games found in that file.")
    return name, n


def remove_game(game_row: int, folder: Optional[Path] = None) -> None:
    """Take one game out of its file (the file is rewritten without it)."""
    folder = folder or library_dir()
    con = refresh(folder)
    r = con.execute("SELECT file, number FROM games WHERE id = ?", (int(game_row),)).fetchone()
    con.close()
    if r is None:
        raise ValueError("That game is no longer in the library.")
    path = folder / r["file"]
    text = path.read_text(encoding="utf-8", errors="replace")
    games, n = [], 0
    f = io.StringIO(text)
    while (g := chess.pgn.read_game(f)) is not None:
        if not list(g.mainline_moves()) and not g.headers.get("White"):
            continue
        n += 1
        if n != r["number"]:
            games.append(g)
    _write_atomic(path, "".join(str(g) + "\n\n" for g in games))
    refresh(folder).close()


# --- the opening explorer ------------------------------------------------------

def explore(board: chess.Board, mine: str = "", file: str = "",
            folder: Optional[Path] = None) -> dict:
    """What was played from this position in the library's games:
    {"moves": [{uci, san, games, white, draws, black, score}], "games": n}.
    score: points per game in % for `mine`'s colour when it is "white" or
    "black", else for the side to move. mine: "white" / "black" / "any":
    only games where you (Settings name) had that colour."""
    folder = folder or library_dir()
    con = refresh(folder)
    where, args = ["p.epd = ?"], [board.epd()]
    name = (config.settings.player_name or "").strip()
    if mine and name:
        col = {"white": "g.white = ? COLLATE NOCASE", "black": "g.black = ? COLLATE NOCASE"}.get(
            mine, "(g.white = ? COLLATE NOCASE OR g.black = ? COLLATE NOCASE)")
        where.append(col); args += [name] * col.count("?")
    if file:
        where.append("g.file = ?"); args.append(file)
    rows = con.execute(
        "SELECT p.uci, p.san, g.result, COUNT(*) AS n FROM positions p JOIN games g ON g.id = p.game "
        f"WHERE {' AND '.join(where)} GROUP BY p.uci, g.result", args).fetchall()
    con.close()
    by = {}
    for r in rows:
        m = by.setdefault(r["uci"], {"uci": r["uci"], "san": r["san"], "games": 0,
                                     "white": 0, "draws": 0, "black": 0})
        m["games"] += r["n"]
        key = {"1-0": "white", "0-1": "black", "1/2-1/2": "draws"}.get(r["result"])
        if key:
            m[key] += r["n"]
    out = []
    for m in by.values():
        decided = m["white"] + m["draws"] + m["black"]
        pov_white = mine == "white" if mine in ("white", "black") else board.turn == chess.WHITE
        mine_pts = (m["white"] if pov_white else m["black"]) + m["draws"] / 2
        m["score"] = round(100 * mine_pts / decided) if decided else None
        out.append(m)
    out.sort(key=lambda m: (-m["games"], m["san"]))
    return {"moves": out, "games": sum(m["games"] for m in out)}
