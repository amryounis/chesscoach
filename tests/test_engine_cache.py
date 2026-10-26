"""The Stockfish cache: same answers, from the file the second time."""
import chess
import chess.engine
import pytest

from chesscoach.engine import run_stockfish
from chesscoach.engine_cache import CachedEngine
from chesscoach.concepts import search

pytestmark = pytest.mark.engine

FEN = "rn1qk2r/p3pP2/2p2b1p/8/2pP2b1/2N1PN2/PP3PP1/R2QK2R b KQkq - 0 12"


def test_second_review_comes_from_the_cache(stockfish, tmp_path):
    db = tmp_path / "c.sqlite"
    b = chess.Board(FEN)
    e = CachedEngine(chess.engine.SimpleEngine.popen_uci(stockfish), db)
    first = run_stockfish(b, depth=12, multipv=3, engine=e)
    s1 = search(e, b, depth=10, exclude=b.parse_san("Kxf7"))
    e.quit()
    assert e.misses == 2 and e.hits == 0

    e = CachedEngine(chess.engine.SimpleEngine.popen_uci(stockfish), db)
    again = run_stockfish(b, depth=12, multipv=3, engine=e)
    s2 = search(e, b, depth=10, exclude=b.parse_san("Kxf7"))
    assert e.hits == 2 and e.misses == 0
    assert again == first and (s2.cp, s2.mate, s2.pv) == (s1.cp, s1.mate, s1.pv)
    # A different depth, line count or move restriction is a different search.
    run_stockfish(b, depth=11, multipv=3, engine=e)
    run_stockfish(b, depth=12, multipv=2, engine=e)
    search(e, b, depth=10)
    assert e.misses == 3
    # Not depth-limited: straight to the engine, nothing stored.
    e.analyse(b, chess.engine.Limit(nodes=2000))
    assert (e.hits, e.misses) == (2, 3)
    e.quit()


def test_mate_scores_survive_the_round_trip(stockfish, tmp_path):
    b = chess.Board("6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1")     # Rd8#
    e = CachedEngine(chess.engine.SimpleEngine.popen_uci(stockfish), tmp_path / "c.sqlite")
    first = run_stockfish(b, depth=8, multipv=1, engine=e)
    again = run_stockfish(b, depth=8, multipv=1, engine=e)
    e.quit()
    assert first.score_mate == again.score_mate == 1 and again.best_move == "Rd8#"


def test_open_stockfish_respects_the_setting(stockfish, monkeypatch, tmp_path):
    from chesscoach import config, engine_cache
    config.settings = config.load_settings(stockfish_path=stockfish)
    monkeypatch.setattr(engine_cache, "default_path", lambda: tmp_path / "c.sqlite")
    monkeypatch.setattr(config.settings, "engine_cache", True)
    e = engine_cache.open_stockfish()
    assert isinstance(e, CachedEngine)
    e.quit()
    monkeypatch.setattr(config.settings, "engine_cache", False)
    e = engine_cache.open_stockfish()
    assert not isinstance(e, CachedEngine)
    e.quit()
