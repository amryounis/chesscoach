import chess
import pytest

from chesscoach import config
from chesscoach.play import PlayGame


@pytest.fixture
def game(stockfish, monkeypatch):
    # Force the Stockfish fallback so the test doesn't depend on Maia
    config.settings = config.load_settings(stockfish_path=stockfish, maia_cmd="no-such-maia")
    g = PlayGame(chess.WHITE, elo=1500)
    yield g
    g.close()


def test_falls_back_to_stockfish_without_maia(game):
    assert game.engine_name.startswith("Stockfish")


def test_player_then_engine_move(game):
    assert game.player_move("e2e4") == "e4"
    reply = game.engine_move()
    assert reply and len(game.board.move_stack) == 2
    assert game.board.turn == chess.WHITE


def test_illegal_and_out_of_turn_moves_rejected(game):
    with pytest.raises(ValueError, match="Illegal"):
        game.player_move("e2e5")
    game.player_move("e2e4")
    with pytest.raises(ValueError, match="not your move"):
        game.player_move("d2d4")


def test_takeback_returns_to_player_turn(game):
    game.player_move("e2e4")
    game.engine_move()
    game.takeback()
    assert game.board.move_stack == [] and game.board.turn == chess.WHITE


def test_resign_and_pgn(game):
    game.player_move("d2d4")
    game.engine_move()
    game.resign()
    assert game.over and game.result() == "0-1"
    assert "You resigned" in game.status()
    pgn = game.pgn()
    assert '[White "Player"]' in pgn and "1. d4" in pgn and pgn.strip().endswith("0-1")


def test_promotion_detection(stockfish):
    config.settings = config.load_settings(stockfish_path=stockfish, maia_cmd="no-such-maia")
    with PlayGame(chess.WHITE) as g:
        g.board = chess.Board("8/4P3/8/8/8/8/k7/4K3 w - - 0 1")
        assert g.needs_promotion("e7", "e8")
        assert not g.needs_promotion("e1", "e2")
        assert g.player_move("e7e8q") == "e8=Q"


def test_engine_opens_when_player_is_black(stockfish):
    config.settings = config.load_settings(stockfish_path=stockfish, maia_cmd="no-such-maia")
    with PlayGame(chess.BLACK) as g:
        assert g.engine_move() is not None and g.board.turn == chess.BLACK
