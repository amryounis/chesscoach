"""
Regression test on a real game (Homer vs Fritz, 0-1). Needs Stockfish.
Guards the coaching facts that took the most work to get right.
"""
import chess.engine
import pytest

from chesscoach import config
from chesscoach.engine import run_stockfish
from chesscoach.features import compare_lines

pytestmark = pytest.mark.engine


@pytest.fixture
def analyse(stockfish):
    config.settings = config.load_settings(stockfish_path=stockfish)
    sf = chess.engine.SimpleEngine.popen_uci(stockfish)
    yield lambda b: run_stockfish(b, depth=18, engine=sf)
    sf.quit()


def test_8_bg4_lesson_is_king_shelter(homer_game, analyse):
    boards, moves = homer_game
    i = 15                                   # 8...Bg4 (engine prefers ...g5)
    eb, ea = analyse(boards[i]), analyse(boards[i + 1])
    if eb.best_move != "g5":
        pytest.skip(f"engine preferred {eb.best_move} on this machine")
    out = compare_lines(boards[i], moves[i], eb, ea)
    engine_side = out[1:out.index("- In favour of the PLAYER'S MOVE (what the player gained):")]
    assert any("Black's king shelter" in line for line in engine_side)
