"""The Lichess opening explorer (online, opt-in), with a stand-in for Lichess."""
import io
import time
import urllib.error

import chess
import pytest

from chesscoach import config, lichess

START = {"white": 600, "draws": 300, "black": 100, "opening": None, "moves": [
    {"uci": "e2e4", "san": "e4", "white": 400, "draws": 150, "black": 50, "averageRating": 2410},
    {"uci": "d2d4", "san": "d4", "white": 200, "draws": 150, "black": 50, "averageRating": 2420},
    {"uci": "e7e5", "san": "e5", "white": 1, "draws": 0, "black": 0}]}       # not legal here: dropped


@pytest.fixture(autouse=True)
def settings(tmp_path):
    config.settings = config.load_settings(player_elo=1700)
    config.settings.library_dir = str(tmp_path)
    lichess._quiet_until = 0.0


def test_moves_results_and_score_for_the_side_to_move():
    calls = []
    out = lichess.explore(chess.Board(), "masters", token="t0k",
                          get=lambda url, tok: calls.append((url, tok)) or START)
    assert [m["san"] for m in out["moves"]] == ["e4", "d4"]
    e4 = out["moves"][0]
    assert e4["games"] == 600 and e4["rating"] == 2410 and e4["score"] == round(100 * 475 / 600)
    assert out["games"] == 1000 and out["label"] == "Lichess masters" and not out["stale"]
    url, tok = calls[0]
    assert url.startswith("https://explorer.lichess.ovh/masters?") and "fen=rnbqkbnr" in url
    assert tok == "t0k" and "t0k" not in url                     # the token is never in the URL


def test_lichess_players_are_asked_around_your_rating():
    calls = []
    lichess.explore(chess.Board(), "lichess", token="t", get=lambda url, tok: calls.append(url) or START)
    assert "ratings=1600%2C1800" in calls[0] and "speeds=blitz%2Crapid%2Cclassical" in calls[0]
    assert lichess.bands_around(950) == [0, 1000] and lichess.bands_around(2600) == [2500]


def test_answers_are_kept_and_work_offline():
    n = []
    get = lambda url, tok: n.append(1) or START                      # noqa: E731
    lichess.explore(chess.Board(), "masters", token="t", get=get)
    lichess.explore(chess.Board(), "masters", token="t", get=get)
    assert len(n) == 1                                                  # the second from the cache
    # an old answer and no connection: the old answer, said so
    con = lichess._connect()
    con.execute("UPDATE answers SET fetched = ?", (time.time() - 40 * 86400,))
    con.commit()
    con.close()

    def offline(url, tok):
        raise lichess.LichessError("Lichess can't be reached")
    out = lichess.explore(chess.Board(), "masters", token="t", get=offline)
    assert out["stale"] and out["moves"][0]["san"] == "e4"
    out = lichess.explore(chess.Board(), "masters", token="", get=offline)   # no token: the old one too
    assert out["stale"]


def test_without_a_token_it_says_how_to_get_one():
    with pytest.raises(lichess.LichessError, match="API access tokens"):
        lichess.explore(chess.Board(), "masters", token="", get=lambda u, t: START)


def test_refused_token_and_too_many_requests_are_said_plainly(monkeypatch):
    def refuse(req, timeout):
        assert req.get_header("Authorization") == "Bearer secret"
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO())
    monkeypatch.setattr(lichess.urllib.request, "urlopen", refuse)
    with pytest.raises(lichess.LichessError, match="refused the token") as e:
        lichess.explore(chess.Board(), "masters", token="secret")
    assert "secret" not in str(e.value)

    def busy(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, io.BytesIO())
    monkeypatch.setattr(lichess.urllib.request, "urlopen", busy)
    with pytest.raises(lichess.LichessError, match="pause"):
        lichess.explore(chess.Board(), "lichess", token="secret")
    with pytest.raises(lichess.LichessError, match="pause"):     # and nothing is sent for a minute
        lichess.explore(chess.Board(), "masters", token="secret")


def test_an_environment_token_is_not_written_to_the_settings_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CHESSCOACH_LICHESS_TOKEN", "from-env")
    s = config.load_settings()
    assert s.lichess_token == "from-env"
    path = config.save_settings(s, tmp_path / "c.toml")
    assert "from-env" not in path.read_text()
