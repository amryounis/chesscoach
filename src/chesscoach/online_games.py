"""
Your own games from Lichess and Chess.com, into your games folder.

Online only when you press Import (Games tab, or `chesscoach import`). Both
sites publish a player's games as PGN without an account:

  Lichess    lichess.org/api/games/user/<name>: newest first, `since` a time.
             A Lichess token (Settings) is sent if there is one: Lichess
             then serves the download faster. Clock times and the opening
             name come with each game.
  Chess.com  api.chess.com/pub/player/<name>/games/archives lists the months
             that have games; each month downloads as one PGN.

FICS has no such service (ficsgames.org is a search form), so a FICS file
downloaded there comes in with "Add PGN file…" as before.

The games go to Lichess.pgn / Chess.com.pgn in your games folder. A game
already there (same game link) is skipped, so importing again adds only
new games. Each import takes at most MAX_GAMES, newest first.
"""

from __future__ import annotations

import datetime
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Optional

import chess.pgn

from . import __version__, config

SITES = {"lichess": "Lichess", "chesscom": "Chess.com"}
FILES = {"lichess": "Lichess.pgn", "chesscom": "Chess.com.pgn"}
PERIODS = {"new": "New since the last import", "month": "The last month",
           "year": "The last year", "all": "Everything"}
MAX_GAMES = 2000
TIMEOUT = 60                       # seconds without data before giving up (a long download streams)


class OnlineError(Exception):
    """Something the player should read."""


def _request(url: str, accept: str, token: str = "") -> bytes:
    headers = {"Accept": accept, "User-Agent": f"chesscoach/{__version__} (offline chess coach)"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                    timeout=TIMEOUT) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise OnlineError("No such player there: check the name.") from None
        if e.code == 429:
            raise OnlineError("The site asked for a pause (too many requests); "
                              "try again in a minute.") from None
        if e.code in (401, 403) and token:
            raise OnlineError("Lichess refused the token in Settings; remove it or "
                              "create a new one.") from None
        raise OnlineError(f"The site answered with an error ({e.code}); try again later.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise OnlineError("The site can't be reached: no internet connection?") from None


def get_text(url: str, token: str = "") -> str:
    return _request(url, "application/x-chess-pgn", token).decode("utf-8", errors="replace")


def get_json(url: str) -> dict:
    try:
        return json.loads(_request(url, "application/json").decode("utf-8"))
    except ValueError:
        raise OnlineError("The site sent an answer chesscoach could not read.") from None


# --- downloads -------------------------------------------------------------

def lichess_pgn(user: str, since: Optional[datetime.datetime], max_games: int = MAX_GAMES,
                token: str = "", fetch: Callable = get_text) -> str:
    params = {"max": max_games, "clocks": "true", "opening": "true", "evals": "true",
              "moves": "true", "tags": "true"}
    if since is not None:
        params["since"] = int(since.timestamp() * 1000)
    url = (f"https://lichess.org/api/games/user/{urllib.parse.quote(user)}?"
           + urllib.parse.urlencode(params))
    return fetch(url, token)


def chesscom_pgn(user: str, since: Optional[datetime.datetime], max_games: int = MAX_GAMES,
                 fetch_json: Callable = get_json, fetch: Callable = get_text) -> str:
    base = f"https://api.chess.com/pub/player/{urllib.parse.quote(user.lower())}/games/archives"
    months = list(fetch_json(base).get("archives") or [])
    if since is not None:
        first = (since.year, since.month)
        months = [m for m in months if tuple(int(x) for x in m.rstrip("/").split("/")[-2:]) >= first]
    out, n = [], 0
    for m in reversed(months):                        # newest month first, up to max_games
        text = fetch(m.rstrip("/") + "/pgn", "")
        out.insert(0, text)
        n += text.count("[Event ")
        if n >= max_games:
            break
    return "\n\n".join(out)


# --- into the games folder ------------------------------------------------------

def game_key(game: chess.pgn.Game) -> str:
    """The game's link on its site (Lichess: Site; Chess.com: Link), else its
    moves: two different games can have the same short sequence of moves."""
    for tag in ("Link", "Site"):
        v = game.headers.get(tag, "")
        if v.startswith("http"):
            return v
    from .library import game_id
    return "moves:" + game_id(game)


def _games(text: str) -> list:
    out, f = [], io.StringIO(text)
    while True:
        g = chess.pgn.read_game(f)
        if g is None:
            break
        if g.errors or g.next() is None:
            continue
        out.append(g)
    return out


def _state_path(folder: Path) -> Path:
    return folder / "imports.json"


def _state(folder: Path) -> dict:
    try:
        data = json.loads(_state_path(folder).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _since(period: str, last: Optional[float], now: datetime.datetime) -> Optional[datetime.datetime]:
    if period == "month":
        return now - datetime.timedelta(days=31)
    if period == "year":
        return now - datetime.timedelta(days=366)
    if period == "new":
        # since the last import; the first time, the last month
        return (datetime.datetime.fromtimestamp(last) - datetime.timedelta(hours=1) if last
                else now - datetime.timedelta(days=31))
    return None                                         # everything (newest MAX_GAMES)


def import_games(site: str, user: str, period: str = "new", folder: Optional[Path] = None,
                 fetch: Optional[Callable] = None, fetch_json: Optional[Callable] = None,
                 now: Optional[datetime.datetime] = None) -> dict:
    """Download `user`'s games from `site` ("lichess" / "chesscom") and add
    the new ones to that site's file in the games folder.
    -> {file, added, skipped, user, site}"""
    from . import library
    if site not in SITES:
        raise ValueError(f"Unknown site: {site}")
    user = (user or "").strip()
    if not user or any(c in user for c in "/?#&% "):
        raise OnlineError("Type the player's name on that site.")
    folder = folder or library.library_dir()
    folder.mkdir(parents=True, exist_ok=True)
    now = now or datetime.datetime.now()
    key = f"{site}:{user.lower()}"
    state = _state(folder)
    since = _since(period, (state.get(key) or {}).get("last"), now)
    if site == "lichess":
        text = lichess_pgn(user, since, token=(config.settings.lichess_token or "").strip(),
                           fetch=fetch or get_text)
    else:
        text = chesscom_pgn(user, since, fetch_json=fetch_json or get_json, fetch=fetch or get_text)
    games = _games(text)
    path = folder / FILES[site]
    with library._LOCK:
        have = {game_key(g) for g in library._games_in(path)} if path.exists() else set()
        new = []
        for g in games:
            k = game_key(g)
            if k not in have:
                have.add(k)
                new.append(g)
        # the file in the order the games were played (Lichess sends newest first)
        new.sort(key=lambda g: (g.headers.get("UTCDate") or g.headers.get("Date", ""),
                                g.headers.get("UTCTime") or g.headers.get("StartTime", "")))
        if new:
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                for g in new:
                    f.write(str(g) + "\n\n")
    state[key] = {"last": time.mktime(now.timetuple()), "user": user}
    try:
        _state_path(folder).write_text(json.dumps(state, indent=1), encoding="utf-8")
    except OSError:
        pass
    return {"file": FILES[site], "added": len(new), "skipped": len(games) - len(new),
            "user": user, "site": SITES[site]}
