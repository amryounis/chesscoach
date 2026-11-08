"""The advanced analyzer (uciboard) opened from chesscoach, in its own window."""
import chess
import pytest

from chesscoach import config
from chesscoach.gui import advanced
from chesscoach.gui.api import Api

uciboard = pytest.importorskip("uciboard")


class FakeEvents:
    def __init__(self):
        self.closing = Hook()


class Hook(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self


class FakeWindow:
    def __init__(self, title, url, js_api, **kw):
        self.title, self.url, self.js_api, self.kw = title, url, js_api, kw
        self.events = FakeEvents()
        self.calls = []

    def evaluate_js(self, code):
        self.calls.append(("js", code))

    def restore(self):
        self.calls.append(("restore",))

    def show(self):
        self.calls.append(("show",))

    def destroy(self):
        self.calls.append(("destroy",))
        for fn in self.events.closing:
            fn()


@pytest.fixture
def api(stockfish, monkeypatch, tmp_path):
    config.settings = config.load_settings(stockfish_path=stockfish, maia_cmd="no-such-maia",
                                           depth=10)
    monkeypatch.setattr(config, "user_config_path", lambda: tmp_path / "config.toml")
    windows = []

    def create(*args, **kw):
        windows.append(FakeWindow(*args, **kw))
        return windows[-1]

    a = Api()
    a._analyzer = advanced.Analyzer(create_window=create)
    a.windows = windows
    yield a
    a.shutdown()


def test_review_game_opens_at_the_move_on_the_board(api):
    api.load_pgn_text("[White \"Me\"]\n[Black \"You\"]\n\n1. e4 {a comment} e5 (1... c5) 2. Nf3 Nc6 3. Bb5 a6 *")
    res = api.open_analyzer("review", 3, "")
    assert res["reused"] is False and res["version"] == uciboard.__version__
    (w,) = api.windows
    assert w.url.startswith("http://127.0.0.1:") and w.url.endswith("/index.html")
    assert "advanced analysis" in w.title and w.kw["min_size"] == (900, 600)
    st = w.js_api.state()
    after3 = chess.Board()
    for san in ("e4", "e5", "Nf3"):
        after3.push_san(san)
    assert st["fen"] == after3.fen()                      # the move shown in Review
    assert st["moves"] == ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"] and st["ply"] == 3
    assert st["info"]["white"] == "Me" and st["info"]["black"] == "You"
    assert w.js_api._paused                               # its engine waits for Analyse
    assert w.js_api._window is w                          # its dialogs open on its own window


def test_opening_again_reuses_the_window_and_a_position_needs_no_moves(api):
    api.load_pgn_text("1. d4 d5 *")
    api.open_analyzer("review", None, "")
    fen = "6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1"
    res = api.open_analyzer("position", None, fen)
    assert res["reused"] is True and len(api.windows) == 1
    w = api.windows[0]
    assert ("js", "location.reload()") in w.calls and ("show",) in w.calls
    assert w.js_api.state()["fen"] == fen
    assert "error" in api.open_analyzer("position", None, "not a fen")


def test_closing_the_window_stops_its_engine_and_the_next_open_makes_a_new_one(api):
    api.load_pgn_text("1. e4 *")
    api.open_analyzer("review", 1, "")
    w = api.windows[0]
    stopped = []
    w.js_api.shutdown = lambda *a: stopped.append(True)
    for fn in w.events.closing:
        fn()
    assert stopped and not api._analyzer.is_open
    api.open_analyzer("review", 1, "")
    assert len(api.windows) == 2
    api.shutdown()                                        # chesscoach closing closes it too
    assert ("destroy",) in api.windows[1].calls and not api._analyzer.is_open


def test_nothing_to_show_and_not_installed(api, monkeypatch):
    assert "first" in api.open_analyzer("review", None, "")["error"]
    monkeypatch.setattr(advanced, "available", lambda: False)
    err = api.open_analyzer("position", None, chess.STARTING_FEN)["error"]
    assert "isn't installed" in err and advanced.INSTALL in err
