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
    assert st["result"]["accuracy"]["black"] is not None
    # Save PGN: the reviewed game, annotated, for any chess program
    import chess.pgn, io

    class SaveTo:
        def create_file_dialog(self, kind, **kw):
            return str(tmp_out)
    tmp_out = DATA.parent / "_tmp_export.pgn"
    api._window = SaveTo()
    try:
        assert api.save_annotated_pgn_dialog() == {"saved": str(tmp_out)}
        g = chess.pgn.read_game(io.StringIO(tmp_out.read_text(encoding="utf-8")))
        assert not g.errors and len(list(g.mainline_moves())) == 78
        assert any(n.nags for n in g.mainline()) and g.headers["Annotator"].startswith("chesscoach")
    finally:
        tmp_out.unlink(missing_ok=True)


def test_play_then_review_the_game(api):
    st = api.new_game("white", 1500)
    assert st["orientation"] == "white" and "e2" in st["dests"]
    assert st["opponent_info"]["name"] == st["opponent"]           # who you face, said plainly
    assert api.play_opponent(800)["effective_elo"] == 1320            # Maia missing here
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


def test_game_and_settings_are_locked_while_a_review_runs(api, monkeypatch):
    """Review feedback: switching games during a review put its result on
    the other game, and settings changes mixed depths within one review."""
    import threading
    from chesscoach.gui import api as api_mod
    from conftest import DATA
    gate = threading.Event()

    def slow_review(game, *a, should_stop=None, **kw):
        gate.wait(10)
        raise api_mod.Cancelled()

    monkeypatch.setattr(api_mod, "review_game", slow_review)
    first = api.load_pgn_file(str(DATA / "homer_vs_fritz.pgn"))
    assert first["key"]
    assert api.start_review("black") == {"ok": True}
    try:
        for res in (api.select_game(0), api.load_pgn_text("1. e4 e5 *"),
                    api.save_settings({"depth": 12})):
            assert "A review is running" in res["error"]
    finally:
        gate.set()
        api._job.join(5)
    assert "error" not in api.select_game(0)                  # free again afterwards


def test_games_are_kept_and_reviews_come_back(api):
    """Played games go to Played.pgn by themselves; a finished review is kept
    whole, and opening that game again (from the library or the file)
    brings the review back without running Stockfish."""
    st = api.new_game("white", 1500)
    api.play_move("e2e4")
    st = api.resign()
    assert st["over"] and st["saved_to"] == "Played.pgn"
    games = api.library_list({})
    assert games["total"] == 1 and games["games"][0]["result"] == "0-1"
    api.new_game("white", 1500)                                # nothing played: not kept
    assert api.library_list({})["total"] == 1

    api.load_pgn_file(str(DATA / "homer_vs_fritz.pgn"))
    assert "review" not in api.select_game(0)
    api.start_review("black", False)
    for _ in range(600):
        st = api.review_status()
        if st["state"] != "running":
            break
        time.sleep(0.1)
    assert st["state"] == "finished" and st["result"]["saved_to"] == "Reviewed.pgn"
    lib = api.library_list({"reviewed": True})
    assert lib["total"] == 1 and lib["games"][0]["reviewed"]
    opened = api.library_open(lib["games"][0]["id"])
    assert opened["review"]["side"] == "black" and opened["review"]["moves"][15]["label"]
    assert api._review is not None and api.save_report_dialog  # usable as a review again
    again = api.load_pgn_file(str(DATA / "homer_vs_fritz.pgn"))
    assert again["review"]["reviewed_at"]
    ex = api.library_explore(["g1f3"], "", "")
    assert ex["line"] == "1. Nf3" and ex["moves"][0]["san"] == "d5" and "g8" in ex["dests"]
