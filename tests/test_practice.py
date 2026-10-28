"""Practice: your reviewed mistakes come back as 'what would you play?'"""
import chess
import chess.engine
import pytest

from chesscoach import config, practice


def _move(**kw):
    base = {"ply": 20, "move_label": "11. Kg2", "color": "white", "san": "Kg2",
            "uci": "h1g2", "label": "Blunder", "fen_before": "3q3k/8/8/8/8/8/8/3Q3K w - - 0 1",
            "best_san": "Qxd8+", "best_uci": "d1d8", "win_before": 99.0, "win_after": 50.0,
            "annotations": {"m": "**What happened**: The queen on d8 was free."},
            "themes": [{"tag": "missed-capture"}]}
    base.update(kw)
    return base


def test_only_mistakes_with_an_engine_move_become_drills():
    d = practice.make_drill(_move(), "g1", "vs Fritz (2026.10.01)")
    assert d["id"] == "g1-20" and d["fen"].startswith("3q3k") and d["best_san"] == "Qxd8+"
    assert d["explanation"].startswith("**What happened**") and d["themes"] == ["missed-capture"]
    assert practice.make_drill(_move(label="Inaccuracy"), "g1")["label"] == "Inaccuracy"
    assert practice.make_drill(_move(label="Good"), "g1") is None
    assert practice.make_drill(_move(best_uci=""), "g1") is None


def test_history_records_without_the_engine_move_are_skipped():
    old = {"id": "a", "color": "white", "white": "Me", "black": "X", "date": "?",
           "moves": [{k: v for k, v in _move().items() if k not in ("best_uci", "best_san")}]}
    new = {"id": "b", "color": "white", "white": "Me", "black": "Y", "date": "2026.10.02",
           "moves": [_move(explanation="Free queen.")]}
    drills = practice.drills_from_history([old, new])
    assert [d["id"] for d in drills] == ["b-20"]
    assert drills[0]["explanation"] == "Free queen." and drills[0]["game_name"] == "vs Y (2026.10.02)"


def test_missed_drills_come_back_first(tmp_path):
    path = tmp_path / "practice.json"
    a, b, c = ({"id": i, "label": "Mistake"} for i in "abc")
    i, bl = {"id": "i", "label": "Inaccuracy"}, {"id": "bl", "label": "Blunder"}
    assert [d["id"] for d in practice.order([i, c, bl], {})] == ["bl", "c", "i"]
    practice.record_attempt("a", "best", path)
    practice.record_attempt("b", "miss", path)
    prog = practice.load_progress(path)
    assert prog["a"]["solved"] == 1 and prog["b"]["tries"] == 1 and prog["b"]["solved"] == 0
    # c never tried and b missed last time come before a, which was solved
    assert [d["id"] for d in practice.order([a, b, c], prog)][-1] == "a"


@pytest.fixture
def sf(stockfish, monkeypatch):
    monkeypatch.setattr(config, "settings", config.load_settings(stockfish_path=stockfish,
                                                                 depth=10))
    eng = chess.engine.SimpleEngine.popen_uci(stockfish)
    yield eng
    eng.quit()


def test_judging_best_repeat_and_miss(sf):
    drill = practice.make_drill(_move(), "g1")
    best = practice.judge(drill, "d1d8", engine=sf)
    assert best["verdict"] == "best" and best["line"][0] == "Qxd8+"
    rep = practice.judge(drill, "h1g2", engine=sf)
    assert rep["verdict"] == "repeat" and "you played in the game, a blunder" in rep["message"]
    miss = practice.judge(drill, "h1h2", engine=sf)
    assert miss["verdict"] == "miss" and miss["label"] in ("Mistake", "Blunder")
    assert "The engine's move was Qxd8+" in miss["message"]
    shown = practice.judge(drill, None, engine=sf)
    assert shown["verdict"] == "revealed" and shown["line"][0] == "Qxd8+"
    with pytest.raises(ValueError, match="Illegal"):
        practice.judge(drill, "a1a8", engine=sf)


def test_another_winning_move_is_accepted(sf):
    # Queen and rook against a bare king: many moves keep the win.
    fen = "7k/8/8/8/8/8/8/QR5K w - - 0 1"
    drill = practice.make_drill(_move(fen_before=fen, best_san="Qa7", best_uci="a1a7",
                                      san="Rb2", uci="b1b2", win_before=None), "g2")
    res = practice.judge(drill, "a1a2", engine=sf)
    assert res["verdict"] == "good" and "also works" in res["message"]
    assert res["line"][0] == "Qa7"


def test_practice_file_never_breaks_the_history(tmp_path, monkeypatch):
    """0.12.0 wrote practice.json into the history folder, and the history
    loader read it as a game ("KeyError: 'id'")."""
    import json
    from chesscoach import history
    hist = tmp_path / "history"
    hist.mkdir()
    (hist / "practice.json").write_text(json.dumps({"g1-20": {"tries": 1}}))
    (hist / "abc.json").write_text(json.dumps(
        {"id": "abc", "color": "white", "white": "Me", "black": "X", "date": "?",
         "moves": [_move(themes=[])]}))
    assert [r["id"] for r in history.load(hist)] == ["abc"]
    history.patterns(history.load(hist))
    # The progress file moves out of the history folder.
    monkeypatch.setattr(history, "history_dir", lambda: hist)
    path = practice.progress_path()
    assert path == tmp_path / "practice.json" and path.exists()
    assert not (hist / "practice.json").exists()
    assert practice.load_progress(path) == {"g1-20": {"tries": 1}}


def test_queue_tags_say_why():
    assert practice.queue_tag({}) == "New"
    assert practice.queue_tag({"tries": 2, "solved": 1, "last_verdict": "miss"}) == "Missed last time"
    assert practice.queue_tag({"tries": 1, "solved": 1, "last_verdict": "best"}) == "Solved once"
    assert practice.queue_tag({"tries": 3, "solved": 2, "last_verdict": "good"}) == "Solved 2 times"


def test_a_model_error_is_never_the_coachs_explanation():
    """Review feedback: "Failed to connect to Ollama..." was stored as the
    comment and shown under "The coach said in the review"."""
    for err in ("Failed to connect to Ollama. Please check that Ollama is running "
                "(is 'qwen3:14b' pulled? run: ollama list)",
                "model 'qwen3:14b' not found (is 'qwen3:14b' pulled? run: ollama list)"):
        assert practice.explanation_of({"annotations": {"m": err}}) == ""
        assert practice.make_drill(_move(annotations={"m": err}), "g")["explanation"] == ""
    assert practice.explanation_of(_move()).startswith("**What happened**")
