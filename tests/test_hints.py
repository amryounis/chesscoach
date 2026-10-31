"""Hints one step at a time, the blunder alert, and the help kept in the PGN."""
import io

import chess
import chess.engine
import chess.pgn
import pytest

from chesscoach import config, hints


@pytest.fixture
def sf(stockfish):
    e = chess.engine.SimpleEngine.popen_uci(stockfish)
    yield e
    e.quit()


@pytest.fixture(autouse=True)
def settings(stockfish):
    config.settings = config.load_settings(stockfish_path=stockfish, maia_cmd="no-such-maia",
                                           depth=12)


def _steps(board, sf, **kw):
    h = hints.hint(board, sf, **kw)
    assert len(h["steps"]) == hints.STEPS
    return h, [s["text"] for s in h["steps"]]


def test_mate_in_one_is_said_first_and_given_away_last(sf):
    b = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR w KQkq - 4 4")
    h, t = _steps(b, sf)
    assert t[0] == "There is checkmate in one move."
    assert "f3" not in t[0] and "f7" not in t[0]          # step 1 gives nothing away
    assert t[1] == "The move takes on f7."
    assert t[2] == "Your queen on f3 goes there."
    assert t[3].startswith("Qxf7#.") and "only one move" not in " ".join(t)
    assert h["best_uci"] == "f3f7"
    assert h["steps"][3]["shapes"] == [{"orig": "f3", "dest": "f7", "brush": "green"}]


def test_a_threat_to_meet(sf):
    # White threatens Qxf7#; Black must see it.
    b = chess.Board("r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR b KQkq - 3 3")
    h, t = _steps(b, sf)
    assert t[0] == "Watch out: White threatens checkmate with Qxf7#."
    assert h["steps"][0]["shapes"] == [{"orig": "f3", "dest": "f7", "brush": "threat"}]


def test_a_free_piece_is_a_tactic_without_naming_it(sf):
    b = chess.Board("r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR b KQkq - 3 3")
    h, t = _steps(b, sf)
    assert t[0] == hints.TACTIC_HINT["hanging_piece"]
    assert "h5" not in t[0] and "queen" not in t[0]
    assert h["best_uci"] == "f6h5"


def test_quiet_positions_say_what_kind_of_move(sf):
    _, t = _steps(chess.Board(), sf)
    assert t[0].startswith("No tactic here.") or t[0].startswith("Nothing urgent.")


def test_practice_hints_follow_the_reviews_move(sf):
    # Force a move the engine might not pick first: the hint is about it.
    b = chess.Board()
    h, t = _steps(b, sf, best=chess.Move.from_uci("d2d4"))
    assert h["best_uci"] == "d2d4" and t[1] == "The move goes to d4."
    assert "The engine's line: 1. d4" in t[3]


def test_no_hint_when_the_game_is_over(sf):
    b = chess.Board("rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3")
    with pytest.raises(ValueError, match="over"):
        hints.hint(b, sf)


def test_alert_on_a_blunder_but_not_on_a_good_move(sf):
    b = chess.Board("r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR b KQkq - 3 3")
    bad = hints.alert(b, chess.Move.from_uci("f6g4"), sf, 20)      # ignores Qxf7#
    assert bad and bad["label"] == "Blunder" and bad["san"] == "Ng4"
    assert bad["text"] == "Are you sure about Ng4? It looks like a blunder. Check what White can do after it."
    assert "Qxf7" not in bad["text"]                                # never says why
    assert hints.alert(b, chess.Move.from_uci("d7d6"), sf, 20) is None
    assert hints.alert(b, chess.Move.from_uci("f6g4"), sf, 0) is None   # off


def test_alert_is_quiet_for_the_only_legal_move(sf):
    b = chess.Board("7k/8/8/8/8/8/6q1/7K w - - 0 1")             # Kxg2 is the only move
    assert b.legal_moves.count() == 1
    assert hints.alert(b, next(iter(b.legal_moves)), sf, 10) is None


def test_help_is_read_back_from_the_pgn():
    text = ("1. e4 { [%hint 2] } e5 2. Qh5 { [%alert Qh5] } Nc6 3. Bc4 { [%alert Qf3] } Nf6 *")
    help_ = hints.help_taken(chess.pgn.read_game(io.StringIO(text)))
    assert help_ == [
        {"ply": 0, "san": "e4", "hint": 2, "alerts": [], "changed": False},
        {"ply": 2, "san": "Qh5", "hint": 0, "alerts": ["Qh5"], "changed": False},
        {"ply": 4, "san": "Bc4", "hint": 0, "alerts": ["Qf3"], "changed": True},
    ]
