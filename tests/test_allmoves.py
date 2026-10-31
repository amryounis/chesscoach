"""Every legal move with a short verdict."""
import chess
import chess.engine
import pytest

from chesscoach import config
from chesscoach.allmoves import explain_all, trade


@pytest.fixture
def sf(stockfish):
    config.settings = config.load_settings(stockfish_path=stockfish, maia_cmd="no-such-maia",
                                           depth=18)
    e = chess.engine.SimpleEngine.popen_uci(stockfish)
    yield e
    e.quit()


def _board(sans):
    b = chess.Board()
    for s in sans.split():
        b.push_san(s)
    return b


def test_every_legal_move_gets_a_verdict_best_first(sf):
    b = _board("e4 c5 Nf3 Nc6 Nc3 e6 d4 cxd4")
    r = explain_all(b, sf)
    moves = {m["san"]: m for m in r["moves"]}
    assert len(r["moves"]) == b.legal_moves.count()
    assert r["moves"][0]["san"] == "Nxd4"
    assert moves["Nxd4"]["text"] == "Best move: recaptures" and moves["Nxd4"]["label"] == "Best"
    assert moves["Nd5"]["label"] == "Blunder" and moves["Nd5"]["text"].startswith(
        "Blunder: loses a knight (after 5...exd5")
    assert moves["Qxd4"]["text"].startswith("Blunder: loses a queen for a knight and a pawn")
    wins = [m["win"] for m in r["moves"]]
    assert wins == sorted(wins, reverse=True)


def test_mates_are_named(sf):
    b = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 4 4")
    moves = {m["san"]: m for m in explain_all(b, sf)["moves"]}
    assert moves["Qxf7#"]["text"] == "Checkmate!"
    b = chess.Board("r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR b KQkq - 3 3")
    moves = {m["san"]: m for m in explain_all(b, sf)["moves"]}
    assert moves["a6"]["text"] == "Allows mate in 1: 4. Qxf7#"


def test_trades_cancel_and_promotions_count():
    b = _board("e4 d5")
    lost, won = trade(b, [b.parse_san("exd5"), chess.Move.from_uci("d8d5")])
    assert lost == [] and won == []                     # pawn for pawn
    b = chess.Board("8/4P3/8/8/8/8/k7/6K1 w - - 0 1")
    assert trade(b, [chess.Move.from_uci("e7e8q")]) == ([chess.PAWN], [chess.QUEEN])


def test_no_moves_in_a_finished_game(sf):
    b = chess.Board("rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3")
    with pytest.raises(ValueError, match="game is over"):
        explain_all(b, sf)
