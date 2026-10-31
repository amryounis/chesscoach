"""The GUI's Python side, exercised without a window."""
import time

import chess

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
    stats = st["result"]["stats"]
    assert stats["black"]["analysed"] == 39 and sum(stats["black"]["counts"].values()) == 39
    assert all(m["mark"] for m in moves) and any(m["wdl"] for m in moves)
    log = (config.user_config_path().parent / "chesscoach.log").read_text(encoding="utf-8")
    assert "review timing" in log and "engine:" in log and "comments:" in log
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


def test_library_open_resets_the_picker_and_bad_reviews_are_ignored(api):
    """Review feedback: the picker kept the previous file's games, and a
    malformed kept review stopped the game from opening."""
    from chesscoach import library
    assert len(api.load_pgn_file(str(DATA / "abasov_annotated.pgn"))["games"]) == 25
    library.add_file(str(DATA / "homer_vs_fritz.pgn"))
    row = api.library_list({"text": "Homer"})["games"][0]
    opened = api.library_open(row["id"])
    assert opened["games"] == [] and "error" not in opened
    import chess.pgn
    with open(DATA / "homer_vs_fritz.pgn", encoding="utf-8") as f:
        gid = library.game_id(chess.pgn.read_game(f))
    rdir = library.library_dir() / "reviews"
    rdir.mkdir(parents=True, exist_ok=True)
    for junk in ('{"moves": ["x", 1]}', '[1, 2]', '{"moves": [{"ply": "a"}]}'):
        (rdir / f"{gid}.json").write_text(junk)
        out = api.library_open(row["id"])
        assert "error" not in out and "review" not in out


def test_hints_come_one_step_at_a_time_and_are_kept(api):
    api.new_game("white", 1500)
    first = api.play_hint()
    assert first["n"] == 1 and len(first["steps"]) == 1 and first["total"] == 4
    for _ in range(5):                                # never past the move
        last = api.play_hint()
    assert last["n"] == 4 and last["steps"][0] == first["steps"][0]
    api.play_move("e2e4")
    assert "[%hint 4]" in api.copy_pgn("play")["text"]


def test_blunder_alert_asks_before_playing(api):
    api.new_game("white", 1500)
    g = api._play
    assert "alert" not in api.play_move("e2e4")       # fine: no question
    g.board.set_fen("r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 0 4")
    st = api.play_move("h5h7")                        # gives the queen for a pawn
    assert st["alert"]["san"] == "Qxh7" and st["moves"] == [] and not st["played"]
    st = api.play_move("h5h7", True)                  # played anyway
    assert st["played"] == "Qxh7"
    assert "[%alert Qxh7]" in api.copy_pgn("play")["text"]
    config.settings.blunder_alert = 0                 # off: no question
    api.new_game("white", 1500)
    api._play.board.set_fen("r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 0 4")
    assert "alert" not in api.play_move("h5h7")


def test_hints_are_only_for_your_move(api):
    assert "error" in api.play_hint()                 # no game
    api.new_game("white", 1500)
    api.resign()
    assert "error" in api.play_hint()


def test_paste_a_fen_or_a_pgn_and_copy_it_back(api):
    out = api.paste_text("  r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 4 4\n")
    assert out["moves"] == [] and out["headers"]["Event"] == "Pasted position"
    assert "nothing to review" in api.start_review("white")["error"]
    assert "error" not in api.explain_position(-1)
    assert "error" in api.paste_text("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBN w KQkq - 0 1")
    assert "error" in api.paste_text("   ")
    out = api.paste_text("1. e4 e5 2. Nf3 Nc6 *")
    assert [m["san"] for m in out["moves"]] == ["e4", "e5", "Nf3", "Nc6"]
    assert api.copy_pgn("review")["text"].endswith("1. e4 e5 2. Nf3 Nc6 *")
    assert "error" in api.copy_pgn("play")


def test_practice_hints_past_the_first_mean_solved_with_hints(api, monkeypatch, tmp_path):
    from chesscoach import practice
    monkeypatch.setattr(practice, "progress_path", lambda: tmp_path / "practice.json")
    drill = {"id": "d1", "fen": chess.STARTING_FEN, "best_uci": "e2e4", "best_san": "e4",
             "played_uci": "a2a3", "label": "Mistake", "win_before": 55.0, "explanation": ""}
    api._drills = {"d1": drill}
    assert api.practice_hint("d1")["steps"][0]
    api.practice_hint("d1")                           # the square: a real help
    res = api.practice_answer("d1", "e2e4")
    assert res["verdict"] == "hinted" and res["hints"] == 2
    assert practice.queue_tag(res["progress"]) == "Solved with hints last time"
    res = api.practice_answer("d1", "e2e4")           # alone this time
    assert res["verdict"] == "best"


def test_the_opening_is_named_in_play_review_and_the_saved_game(api):
    api.new_game("white", 1500)
    st = api.play_move("e2e4")
    assert st["opening"] and st["opening"]["name"]                # 1.e4 and the reply are named
    pgn = api.copy_pgn("play")["text"]
    assert '[Opening "' in pgn and '[ECO "' in pgn
    out = api.paste_text("1. c4 e6 2. Nc3 Nf6 3. d4 Bb4 4. a3 *")   # the Nimzo by transposition
    assert out["start_opening"] is None
    assert out["moves"][5]["opening_now"]["name"] == "Nimzo-Indian Defense"
    assert out["moves"][6]["opening_now"]["name"] == "Nimzo-Indian Defense: Sämisch Variation" \
        or out["moves"][6]["opening_now"]["name"].startswith("Nimzo-Indian Defense")
    pos = api.paste_text("rnbqkb1r/pp1ppppp/5n2/2p5/2P5/2N5/PP1PPPPP/R1BQKBNR w KQkq - 2 3")
    assert pos["moves"] == []                                     # a position: named if the table names it
