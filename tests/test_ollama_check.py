"""A review says Ollama is missing up front, not in every comment."""
import pytest

from chesscoach import config, narrator
from chesscoach.narrator import NarratorUnavailable, check_ollama


def _down():
    raise ConnectionError("Failed to connect to Ollama")


def test_not_running(monkeypatch):
    monkeypatch.setattr(narrator.ollama, "list", _down)
    with pytest.raises(NarratorUnavailable, match="Ollama isn't running"):
        check_ollama("qwen3:14b")


def test_model_not_pulled(monkeypatch):
    monkeypatch.setattr(narrator.ollama, "list",
                        lambda: {"models": [{"model": "ministral-3:latest"}]})
    with pytest.raises(NarratorUnavailable, match="ollama pull qwen3:14b"):
        check_ollama("qwen3:14b")
    check_ollama("ministral-3")                       # ":latest" counts


def test_object_style_listing(monkeypatch):
    class M:
        model = "qwen3:14b"

    class L:
        models = [M()]

    monkeypatch.setattr(narrator.ollama, "list", lambda: L())
    check_ollama("qwen3:14b")


def test_review_stops_before_the_engine_pass(monkeypatch, stockfish):
    import chess.pgn
    from chesscoach import review
    monkeypatch.setattr(config, "settings", config.load_settings(stockfish_path=stockfish))
    monkeypatch.setattr(narrator.ollama, "list", _down)
    seen = []
    game = chess.pgn.read_game(open("tests/data/homer_vs_fritz.pgn", encoding="utf-8"))
    with pytest.raises(NarratorUnavailable):
        review.review_game(game, "black", progress=lambda *a: seen.append(a))
    assert seen == []                                  # no position was analysed


def test_review_stops_if_ollama_goes_away(monkeypatch, stockfish):
    import chess.pgn
    import io
    from chesscoach import review
    monkeypatch.setattr(config, "settings",
                        config.load_settings(stockfish_path=stockfish, depth=8))
    state = {"up": True}

    def listing():
        if not state["up"]:
            _down()
        return {"models": [{"model": config.settings.model}]}

    def chat(*a, **kw):
        if kw.get("messages") == []:                  # warm-up
            return {"message": {"content": ""}}
        state["up"] = False
        raise ConnectionError("Failed to connect to Ollama")

    monkeypatch.setattr(narrator.ollama, "list", listing)
    monkeypatch.setattr(narrator.ollama, "chat", chat)
    game = chess.pgn.read_game(io.StringIO("1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7# 1-0"))
    with pytest.raises(NarratorUnavailable, match="isn't running"):
        review.review_game(game, "black")


def test_gui_start_review_says_so_at_once(monkeypatch, stockfish, tmp_path):
    from chesscoach.gui.api import Api
    monkeypatch.setattr(config, "settings", config.load_settings(stockfish_path=stockfish))
    monkeypatch.setattr(narrator.ollama, "list", _down)
    api = Api()
    api.load_pgn_file("tests/data/homer_vs_fritz.pgn")
    res = api.start_review("black")
    assert "Ollama isn't running" in res["error"]
    assert api.review_status()["state"] == "idle"
    api.shutdown()
