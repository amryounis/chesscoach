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


# --- The opponent is said plainly (review feedback, 0.11.x) ---------------

from chesscoach.play import describe_opponent, preview_opponent


def test_fallback_is_named_with_its_real_strength(game):
    op = game.opponent
    assert op["engine"] == "stockfish" and not op["human_like"]
    assert op["effective_elo"] == 1500 and not op["clamped"]
    assert "Maia isn't installed" in op["note"] and "not like a human" in op["note"]


def test_low_ratings_say_they_are_clamped(monkeypatch):
    monkeypatch.setattr(config, "settings", config.load_settings(maia_cmd="no-such-maia"))
    op = preview_opponent(800)
    assert op["name"] == "Stockfish 1320" and op["effective_elo"] == 1320 and op["clamped"]
    assert "lowest setting" in op["note"] and "stronger than the 800 you chose" in op["note"]
    hi = describe_opponent("stockfish", 3500, "failed")
    assert hi["effective_elo"] == 3190 and "highest setting" in hi["note"]
    assert hi["note"].startswith("Maia didn't start")


def test_maia_preview_when_installed(monkeypatch):
    import chesscoach.play as play
    monkeypatch.setattr(play.shutil, "which", lambda cmd: "/usr/bin/" + cmd)
    op = preview_opponent(900)
    assert op == {**op, "engine": "maia", "name": "Maia 900", "effective_elo": 900,
                  "clamped": False, "human_like": True}


def test_takeback_as_black_keeps_the_engines_first_move(stockfish, monkeypatch):
    """Review feedback: taking back before Black's first move undid White's
    opening move, leaving White to move and the player (Black) stuck."""
    monkeypatch.setattr(config, "settings",
                        config.load_settings(stockfish_path=stockfish, maia_cmd="no-such-maia"))
    with PlayGame(chess.BLACK, elo=1500) as g:
        g.engine_move()
        g.takeback()                                  # nothing of Black's to undo
        assert len(g.board.move_stack) == 1 and g.board.turn == chess.BLACK
        assert g.legal_dests()
        g.player_move(next(iter(g.board.legal_moves)).uci())
        g.engine_move()
        g.takeback()                                  # undoes Black's move and the reply
        assert len(g.board.move_stack) == 1 and g.board.turn == chess.BLACK


def test_takeback_after_the_game_ends_on_your_move(game):
    # 1.e4 f6 2.d4 g5 3.Qh5#: the game ends on the player's (White's) move
    for uci in ("e2e4", "f7f6", "d2d4", "g7g5"):
        game.board.push_uci(uci)
    game.board.push_uci("d1h5")                       # checkmate by the player (White)
    assert game.over
    game.takeback()
    assert game.board.turn == chess.WHITE and len(game.board.move_stack) == 4
