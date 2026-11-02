"""Your games from Lichess and Chess.com, with stand-ins for both sites."""
import datetime
import io
import urllib.error

import pytest

from chesscoach import config, library, online_games

LICHESS = """[Event "Rated rapid game"]
[Site "https://lichess.org/abcd1234"]
[UTCDate "2026.10.30"]
[UTCTime "20:00:00"]
[White "youamer"]
[Black "rival"]
[Result "1-0"]

1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7# 1-0

[Event "Rated blitz game"]
[Site "https://lichess.org/efgh5678"]
[UTCDate "2026.10.29"]
[UTCTime "18:00:00"]
[White "other"]
[Black "youamer"]
[Result "0-1"]

1. f3 e5 2. g4 Qh4# 0-1
"""

CHESSCOM_MONTH = """[Event "Live Chess"]
[Site "Chess.com"]
[Date "2026.10.12"]
[White "Youamer"]
[Black "x"]
[Result "1/2-1/2"]
[UTCDate "2026.10.12"]
[UTCTime "10:00:00"]
[Link "https://www.chess.com/game/live/111"]

1. d4 d5 1/2-1/2
"""


@pytest.fixture(autouse=True)
def settings(tmp_path):
    config.settings = config.load_settings()
    config.settings.library_dir = str(tmp_path)
    config.settings.player_name = "Amr"


def test_lichess_games_come_in_once_oldest_first(tmp_path):
    calls = []
    now = datetime.datetime(2026, 11, 2, 8, 0)
    r = online_games.import_games("lichess", "youamer", "month", now=now,
                                  fetch=lambda url, tok: calls.append(url) or LICHESS)
    assert r == {"file": "Lichess.pgn", "added": 2, "skipped": 0, "user": "youamer", "site": "Lichess"}
    assert calls[0].startswith("https://lichess.org/api/games/user/youamer?")
    assert "clocks=true" in calls[0] and "since=" in calls[0] and "max=2000" in calls[0]
    text = (tmp_path / "Lichess.pgn").read_text()
    assert text.index("efgh5678") < text.index("abcd1234")          # played first, written first
    # the same two games again: nothing new
    r = online_games.import_games("lichess", "youamer", "new", now=now, fetch=lambda u, t: LICHESS)
    assert r["added"] == 0 and r["skipped"] == 2
    # "new": since the last import
    seen = []
    online_games.import_games("lichess", "youamer", "new", now=now,
                              fetch=lambda u, t: seen.append(u) or "")
    since = int(seen[0].split("since=")[1].split("&")[0])
    assert abs(since / 1000 - (now - datetime.timedelta(hours=1)).timestamp()) < 2


def test_chesscom_months_from_the_archive_list(tmp_path):
    asked = []
    archives = {"archives": [f"https://api.chess.com/pub/player/youamer/games/2026/{m:02d}"
                             for m in (8, 9, 10)]}
    r = online_games.import_games(
        "chesscom", "Youamer", "month", now=datetime.datetime(2026, 11, 2),
        fetch_json=lambda url: asked.append(url) or archives,
        fetch=lambda url, tok: asked.append(url) or (CHESSCOM_MONTH if url.endswith("10/pgn") else ""))
    assert asked[0] == "https://api.chess.com/pub/player/youamer/games/archives"
    assert asked[1:] == ["https://api.chess.com/pub/player/youamer/games/2026/10/pgn"]   # October on
    assert r["added"] == 1 and r["file"] == "Chess.com.pgn"


def test_imported_games_are_yours_by_your_online_name(tmp_path):
    online_games.import_games("lichess", "youamer", "all", fetch=lambda u, t: LICHESS)
    config.settings.lichess_user = "youamer"
    assert config.my_names() == ["amr", "youamer"]
    mine = library.query(mine="white")["games"]
    assert [g["white"] for g in mine] == ["youamer"]
    assert len(library.query(mine="any")["games"]) == 2


def test_errors_are_said_plainly(monkeypatch):
    with pytest.raises(online_games.OnlineError, match="name"):
        online_games.import_games("lichess", "", "new", fetch=lambda u, t: "")

    def missing(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO())
    monkeypatch.setattr(online_games.urllib.request, "urlopen", missing)
    with pytest.raises(online_games.OnlineError, match="No such player"):
        online_games.import_games("chesscom", "nobody-here", "new")

    def offline(req, timeout):
        raise urllib.error.URLError("down")
    monkeypatch.setattr(online_games.urllib.request, "urlopen", offline)
    with pytest.raises(online_games.OnlineError, match="internet"):
        online_games.import_games("lichess", "youamer", "new")
