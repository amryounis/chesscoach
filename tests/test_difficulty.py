"""How hard an error's better move was to find, and forced lines."""
import chess
import chess.engine
import pytest

from chesscoach import config
from chesscoach.assessment import win_pct
from chesscoach.difficulty import difficulty, forced_line


@pytest.fixture
def sf(stockfish):
    config.settings = config.load_settings(stockfish_path=stockfish, maia_cmd="no-such-maia")
    e = chess.engine.SimpleEngine.popen_uci(stockfish)
    yield e
    e.quit()


def _deep(e, b, me):
    if b.is_checkmate():
        return 0.0 if b.turn == me else 100.0
    s = e.analyse(b, chess.engine.Limit(depth=16))["score"].white()
    return win_pct(None if s.is_mate() else s.score(), s.mate() if s.is_mate() else None,
                   me == chess.WHITE)


def _judge(e, fen, best, played, maia=""):
    b = chess.Board(fen)
    bm, pm = b.parse_san(best), b.parse_san(played)
    a1, a2 = b.copy(), b.copy()
    a1.push(bm)
    a2.push(pm)
    return difficulty(e, b, pm, bm, _deep(e, a1, b.turn), _deep(e, a2, b.turn), maia, 1500)


def test_a_free_queen_is_in_plain_sight(sf):
    d = _judge(sf, "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR b KQkq - 3 3",
               "Nxh5", "d6", maia="Nxh5")
    assert d["level"] == "plain" and d["label"] == "In plain sight"
    assert "Nxh5" not in d["fact"] and "d6" not in d["fact"]      # no notation for the coach
    assert d["text"] == ("In plain sight: even a two-move look shows Nxh5 is better than d6. "
                         "Players around 1500 usually find Nxh5 here.")


def test_a_pawn_fork_paid_off_through_a_pin_is_hard_to_see(sf):
    d = _judge(sf, "r1bqk2r/pppp1ppp/3b1n2/1B6/N2pP3/8/PPPP1PPP/R1BQ1RK1 w kq - 0 8",
               "e5", "c3", maia="c3")
    assert d["level"] == "deep"
    assert d["text"].startswith("Hard to see: e5 only proves better several moves deep")
    assert d["text"].endswith("c3 is what players around 1500 usually play here.")


def test_nothing_to_judge(sf):
    b = chess.Board()
    e4 = b.parse_san("e4")
    assert difficulty(sf, b, e4, e4, 55, 55) is None                 # the engine's own move
    assert difficulty(sf, b, b.parse_san("d4"), e4, 55, 53) is None  # a tiny difference


def test_a_line_of_checks_that_mates_is_forced():
    b = chess.Board("r6k/6pp/8/6N1/2Q5/8/6PP/6K1 w - - 0 1")
    f = forced_line(b, ["Nf7+", "Kg8", "Nh6+", "Kh8", "Qg8+", "Rxg8", "Nf7#"])
    assert f["text"] == ("In the line 1. Nf7+ Kg8 2. Nh6+ Kh8 3. Qg8+ Rxg8 4. Nf7#, White gives "
                         "check on every move, so Black's replies are forced, and it ends in "
                         "checkmate.")
    assert f["fact"] == ("In the engine's line, White gives check on every move, so Black's "
                         "replies are forced, and it ends in checkmate.")
    assert "Nf7" not in f["fact"]                    # the coach's prompt allows no notation


def test_checks_that_win_nothing_are_not_called_forced():
    b = chess.Board("6k1/5ppp/8/8/8/8/5PPP/3QR1K1 w - - 0 1")
    assert forced_line(b, ["Qd8+", "Kh7", "Qd3+", "Kg8"]) is None
    assert forced_line(b, []) is None
