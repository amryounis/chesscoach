"""
The Lichess opening explorer: what masters, or Lichess players of a rating,
play in a position, with their results.

This is the one feature of chesscoach that uses the internet, and only when
you ask for it: choose a Lichess source in the opening explorer. Since 2026
Lichess answers explorer requests only with a personal API token (one with
no permissions is enough; it can read public data and nothing else):
lichess.org -> Preferences -> API access tokens -> create one, tick nothing,
and paste it in Settings (or set CHESSCOACH_LICHESS_TOKEN).

Every answer is kept in lichess_cache.db in your games folder, so a position
looked up once works offline afterwards; an answer older than CACHE_DAYS is
asked for again when there is a connection, and the old one is shown when
there isn't. One request at a time, as Lichess asks; after a 429 (too many
requests) nothing is sent for a minute.
"""

from __future__ import annotations

import datetime
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

import chess

from . import __version__, config

HOST = "https://explorer.lichess.ovh"
SOURCES = {"masters": "Lichess masters", "lichess": "Lichess players"}
SPEEDS = ("blitz", "rapid", "classical")
# Lichess's rating groups: a group holds players from its number up to the next
BANDS = (0, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2500)
CACHE_DAYS = 30
TIMEOUT = 10
_LOCK = threading.Lock()
_quiet_until = 0.0                  # after a 429: send nothing until then


class LichessError(Exception):
    """Something the player should read: no token, refused, offline, busy."""


def bands_around(elo: int) -> list:
    """The rating group a player of `elo` is in, and the one above: the
    games of players a little stronger, the ones worth copying."""
    i = max(k for k, b in enumerate(BANDS) if elo >= b)
    return list(BANDS[i:i + 2])


def cache_path() -> Path:
    from .library import library_dir
    return library_dir() / "lichess_cache.db"


def _connect(path: Optional[Path] = None) -> sqlite3.Connection:
    path = path or cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS answers (key TEXT PRIMARY KEY, fetched REAL, body TEXT)")
    return con


def _key(source: str, board: chess.Board, params: dict) -> str:
    extra = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return f"{source}|{board.epd()}|{extra}"


def _get(url: str, token: str) -> dict:
    """One request; LichessError with a plain reason on failure. The token
    goes in the header only, never in a message or the log."""
    global _quiet_until
    if time.time() < _quiet_until:
        raise LichessError("Lichess asked for a pause; try again in a minute.")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json",
        "User-Agent": f"chesscoach/{__version__} (offline chess coach)"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise LichessError("Lichess refused the token. Create a new one at lichess.org "
                               "(Preferences, API access tokens; no permissions needed) and "
                               "paste it in Settings.") from None
        if e.code == 429:
            _quiet_until = time.time() + 60
            raise LichessError("Lichess asked for a pause (too many requests); "
                               "try again in a minute.") from None
        raise LichessError(f"Lichess answered with an error ({e.code}); try again later.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise LichessError("Lichess can't be reached: no internet connection?") from None
    except ValueError:
        raise LichessError("Lichess sent an answer chesscoach could not read.") from None


def _shape(board: chess.Board, body: dict) -> dict:
    """Lichess's answer in the explorer's own shape."""
    moves = []
    for m in body.get("moves") or []:
        try:
            mv = chess.Move.from_uci(m["uci"])
        except (KeyError, ValueError):
            continue
        if mv not in board.legal_moves:
            continue
        w, d, b = int(m.get("white", 0)), int(m.get("draws", 0)), int(m.get("black", 0))
        n = w + d + b
        mine = w if board.turn == chess.WHITE else b
        moves.append({"uci": mv.uci(), "san": board.san(mv), "games": n, "white": w,
                      "draws": d, "black": b, "rating": m.get("averageRating"),
                      "score": round(100 * (mine + d / 2) / n) if n else None})
    op = body.get("opening") or None
    return {"moves": moves,
            "games": int(body.get("white", 0)) + int(body.get("draws", 0)) + int(body.get("black", 0)),
            "opening": {"eco": op.get("eco", ""), "name": op.get("name", "")} if op else None}


def explore(board: chess.Board, source: str = "masters", token: Optional[str] = None,
            ratings: Optional[list] = None, speeds: Optional[list] = None,
            get: Optional[Callable] = None, path: Optional[Path] = None) -> dict:
    """What `source` ("masters" or "lichess") plays in this position.
    -> {moves: [{uci, san, games, white, draws, black, rating, score}],
        games, opening, source, cached (ISO date of the answer), stale}.
    score: points per game in % for the side to move. `get` replaces the
    HTTP call (tests)."""
    if source not in SOURCES:
        raise ValueError(f"Unknown Lichess source: {source}")
    token = (config.settings.lichess_token if token is None else token).strip()
    params = {"fen": board.fen(), "moves": 20, "topGames": 0}
    if source == "lichess":
        params["ratings"] = ",".join(str(r) for r in (ratings or bands_around(config.settings.player_elo)))
        params["speeds"] = ",".join(speeds or SPEEDS)
        params["recentGames"] = 0
    key = _key(source, board, {k: v for k, v in params.items() if k != "fen"})
    with _LOCK:
        con = _connect(path)
        try:
            row = con.execute("SELECT fetched, body FROM answers WHERE key = ?", (key,)).fetchone()
            fresh = row and time.time() - row[0] < CACHE_DAYS * 86400
            if not fresh:
                if not token:
                    if row:
                        return _out(board, source, row, stale=True)
                    raise LichessError("The Lichess explorer needs a Lichess token: create one at "
                                       "lichess.org (Preferences, API access tokens; tick no "
                                       "permissions) and paste it in Settings.")
                url = f"{HOST}/{source}?" + urllib.parse.urlencode(params)
                try:
                    body = (get or _get)(url, token)
                except LichessError:
                    if row:                        # offline: the old answer, said so
                        return _out(board, source, row, stale=True)
                    raise
                row = (time.time(), json.dumps(body))
                con.execute("INSERT OR REPLACE INTO answers VALUES (?, ?, ?)", (key, *row))
                con.commit()
            return _out(board, source, row, stale=False)
        finally:
            con.close()


def _out(board: chess.Board, source: str, row, stale: bool) -> dict:
    out = _shape(board, json.loads(row[1]))
    out.update(source=source, label=SOURCES[source], stale=stale,
               cached=datetime.datetime.fromtimestamp(row[0]).isoformat(timespec="minutes"))
    return out
