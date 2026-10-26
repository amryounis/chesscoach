"""The plan behind a quiet engine move (after DecodeChess's Plans panel on
Kramnik-Leko 2001, move 21)."""
import chess
import chess.engine
import pytest

from chesscoach import config
from chesscoach.engine import run_stockfish
from chesscoach.narrator import prepare_move
from chesscoach.plans import find_plan

pytestmark = pytest.mark.engine
KL = "2rq1rk1/R2n1ppp/4p3/2pb4/5B2/6P1/1Q2PPBP/3R2K1 w - - 0 21"


@pytest.fixture
def sf(stockfish):
    config.settings = config.load_settings(stockfish_path=stockfish)
    e = chess.engine.SimpleEngine.popen_uci(stockfish)
    yield e
    e.quit()


def test_qa1_prepares_bxd5_which_fails_at_once_to_qb6(sf):
    b = chess.Board(KL)
    p = find_plan(sf, b, b.parse_san("Qa1"))
    if p is None:
        pytest.skip("this Stockfish does not see the plan at depth 18")
    assert p.idea == "Bxd5" and p.plan_win - p.now_win >= 8
    assert p.problem_reply == "Qb6" and p.vacated == "b2"
    assert "hits the queen on b2" in p.problem_text
    facts = p.facts("White")
    assert facts[0].startswith("PLAN: the engine's move (White moves the queen from b2 to a1)")
    assert "Bxd5" not in " ".join(facts)               # in words, no notation for the coach


def test_no_plan_for_forcing_moves_or_ideas_that_work_at_once(sf):
    b = chess.Board(KL)
    assert find_plan(sf, b, b.parse_san("Bxd5")) is None          # a capture is not quiet
    s = chess.Board()
    assert find_plan(sf, s, s.parse_san("e4")) is None


def test_the_coach_gets_the_plan_when_the_player_missed_it(sf):
    b = chess.Board(KL)
    played = b.parse_san("Bxd5")
    a = b.copy()
    a.push(played)
    eb, ea = run_stockfish(b, engine=sf), run_stockfish(a, engine=sf)
    if eb.best_move != "Qa1":
        pytest.skip(f"engine preferred {eb.best_move}")
    prep = prepare_move(b, played, eb, ea, None, engine=sf)
    if not prep["plan"]:
        pytest.skip("plan not found at this depth")
    assert prep["key_facts"][0].startswith("PLAN:") and prep["plan"]["move"] == "Qa1"
    assert "PLAN:" in prep["prompt"]
