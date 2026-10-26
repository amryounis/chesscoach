import shutil
from pathlib import Path

import chess
import chess.pgn
import pytest

from chesscoach import config

DATA = Path(__file__).parent / "data"

# The test game from early development: Black leaves the f6 knight
# attacked by the e5 pawn and takes on d4 instead (8...cxd4), then
# White recaptures with the knight instead of taking the knight (9.Nxd4).
TEST_GAME = ("e4 e6 d4 d5 Nc3 Nf6 Be3 c5 Nf3 Nc6 Be2 Be7 "
             "O-O O-O e5 cxd4 Nxd4").split()


def position(moves: list, ply: int):
    """Board before move number `ply` (1-based) and that move."""
    b = chess.Board()
    for san in moves[:ply - 1]:
        b.push_san(san)
    return b, b.parse_san(moves[ply - 1])


@pytest.fixture
def homer_game():
    with open(DATA / "homer_vs_fritz.pgn", encoding="utf-8") as f:
        game = chess.pgn.read_game(f)
    moves = list(game.mainline_moves())
    boards = [game.board()]
    for m in moves:
        b = boards[-1].copy()
        b.push(m)
        boards.append(b)
    return boards, moves


def find_stockfish():
    s = config.load_settings()
    return s.stockfish_path or shutil.which("stockfish") or (
        "/usr/games/stockfish" if Path("/usr/games/stockfish").exists() else "")


@pytest.fixture
def stockfish():
    path = find_stockfish()
    if not path:
        pytest.skip("no Stockfish found (set CHESSCOACH_STOCKFISH_PATH)")
    return path
