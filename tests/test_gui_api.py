"""The GUI's Python side, exercised without a window."""
import time

import pytest

from chesscoach import config, narrator
from chesscoach.gui.api import Api
from conftest import DATA


@pytest.fixture
def api(stockfish, monkeypatch, tmp_path):
    config.settings = config.load_settings(stockfish_path=stockfish, maia_cmd="no-such-maia",
                                           depth=10)
    monkeypatch.setattr(config, "user_config_path", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(narrator.ollama, "chat", lambda model, messages, options=None, **kw: {
        "message": {"content": "**Assessment**: Inaccuracy\n**What happened**: A reason."}})
    a = Api()
    yield a
    a.shutdown()


def test_bad_pgn_returns_error(api):
    assert "error" in api.load_pgn_text("this is not a game")


def test_review_runs_in_background_and_returns_moves(api):
    summary = api.load_pgn_file(str(DATA / "homer_vs_fritz.pgn"))
    assert len(summary["moves"]) == 78 and summary["moves"][15]["move_label"] == "8...Bg4"
    assert api.start_review("black", False) == {"ok": True}
    assert "error" in api.start_review("black", False)        # one at a time
    for _ in range(600):
        st = api.review_status()
        if st["state"] != "running":
            break
        time.sleep(0.1)
    assert st["state"] == "finished", st
    moves = st["result"]["moves"]
    assert len(moves) == 78
    moments = [m for m in moves if m["reason"]]
    assert moments and all(m["color"] == "black" for m in moments)
    assert moves[-1]["reason"] == "delivered checkmate" and moves[-1]["verdict"] == "checkmate"
    assert all(m["annotations"] for m in moments)
    assert all(len(m["best_uci"]) >= 4 for m in moves[:-1])


def test_play_then_review_the_game(api):
    st = api.new_game("white", 1500)
    assert st["orientation"] == "white" and "e2" in st["dests"]
    st = api.play_move("e2e4")
    assert st["played"] == "e4" and st["reply"] and len(st["moves"]) == 2
    assert "error" in api.play_move("e2e4")                    # pawn already moved
    st = api.takeback()
    assert st["moves"] == []
    api.play_move("d2d4")
    st = api.resign()
    assert st["over"] and st["result"] == "0-1"
    summary = api.review_played_game()
    assert summary["side"] == "white" and summary["moves"][0]["san"] == "d4"


def test_settings_are_saved(api, tmp_path):
    res = api.save_settings({"model": "other-model", "depth": "14"})
    assert res["model"] == "other-model" and res["depth"] == 14
    assert 'model = "other-model"' in (tmp_path / "config.toml").read_text()


def test_a_file_with_many_annotated_games(tmp_path):
    from chesscoach.gui.api import Api
    from conftest import DATA
    api = Api()
    first = api.load_pgn_file(str(DATA / "abasov_annotated.pgn"))
    assert len(first["games"]) == 25 and first["index"] == 0
    assert first["tree"]["annotated"] and len(first["tree"]["evp"]) == 102
    caruana = api.select_game(15)
    assert caruana["headers"]["Black"] == "Caruana, Fabiano" and caruana["index"] == 15
    assert "error" in api.select_game(99)
    plain = api.load_pgn_file(str(DATA / "homer_vs_fritz.pgn"))
    assert plain["games"] == [] and plain["tree"] is not None   # its two comments count
