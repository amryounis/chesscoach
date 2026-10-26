"""
A persistent cache of Stockfish searches (SQLite, next to the config file).

Reviewing a game again - after a settings change, a new version, or while
tuning prompts - asks Stockfish the same questions again; the answers do
not change. Each search is stored under everything that decides its
result: the engine's name and version, the position (full FEN), the depth,
the number of lines and any restriction of the moves searched. Only
depth-limited searches are cached; a search by time or nodes is passed
straight through.

Not cached: the move history behind a position. Stockfish sees it (for
repetitions), so the same FEN reached by different move orders shares one
entry; with the full FEN as key (move counters included) that is rare.

Turn it off with `engine_cache = false`; delete the file to clear it.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

import chess
import chess.engine

FILE = "engine_cache.sqlite"


def default_path() -> Path:
    from .config import user_config_path
    return user_config_path().parent / FILE


def _encode(info: dict) -> dict:
    s = info["score"].white()
    return {"cp": None if s.is_mate() else s.score(),
            "mate": s.mate() if s.is_mate() else None,
            "pv": [m.uci() for m in info.get("pv", [])],
            "depth": info.get("depth")}


def _decode(d: dict) -> dict:
    score = chess.engine.Mate(d["mate"]) if d["mate"] is not None else chess.engine.Cp(d["cp"])
    out = {"score": chess.engine.PovScore(score, chess.WHITE),
           "pv": [chess.Move.from_uci(u) for u in d["pv"]]}
    if d.get("depth") is not None:
        out["depth"] = d["depth"]
    return out


class CachedEngine:
    """A python-chess engine whose depth-limited analyse() calls are
    answered from the cache when it can. Everything else is the engine's."""

    def __init__(self, engine, path: Optional[Path] = None):
        self._engine = engine
        self._name = (getattr(engine, "id", None) or {}).get("name", "engine")
        self.path = Path(path or default_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS search "
                         "(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self._db.commit()
        self._lock = threading.Lock()
        self.hits = self.misses = 0

    def _key(self, board, depth, multipv, root_moves) -> str:
        roots = ",".join(sorted(m.uci() for m in root_moves)) if root_moves else ""
        return f"{self._name}|{board.fen()}|d{depth}|m{multipv or 1}|r{roots}"

    def analyse(self, board: chess.Board, limit: chess.engine.Limit, *,
                multipv: Optional[int] = None, root_moves=None, **kw):
        set_ = {k: v for k, v in dataclasses.asdict(limit).items() if v is not None}
        if kw or set(set_) != {"depth"}:
            return self._engine.analyse(board, limit, multipv=multipv,
                                        root_moves=root_moves, **kw)
        key = self._key(board, limit.depth, multipv, root_moves)
        with self._lock:
            row = self._db.execute("SELECT value FROM search WHERE key = ?",
                                   (key,)).fetchone()
        if row:
            self.hits += 1
            stored = json.loads(row[0])
            return ([_decode(d) for d in stored] if multipv is not None
                    else _decode(stored))
        self.misses += 1
        info = self._engine.analyse(board, limit, multipv=multipv, root_moves=root_moves)
        value = [_encode(i) for i in info] if multipv is not None else _encode(info)
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO search VALUES (?, ?)",
                             (key, json.dumps(value)))
            self._db.commit()
        return info

    def quit(self):
        try:
            self._engine.quit()
        finally:
            with self._lock:
                self._db.close()

    def __getattr__(self, name):
        return getattr(self._engine, name)


def open_stockfish():
    """Stockfish from the settings, behind the cache when it is on."""
    from . import config
    config.settings.require_stockfish()
    sf = chess.engine.SimpleEngine.popen_uci(config.settings.stockfish_path)
    if not config.settings.engine_cache:
        return sf
    try:
        return CachedEngine(sf)
    except (OSError, sqlite3.Error):
        return sf                        # an unwritable cache must not stop a review
