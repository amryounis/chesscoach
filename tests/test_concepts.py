"""Tier 2: engine-verified threats, piece roles and key facts. Needs Stockfish."""
import chess
import chess.engine
import pytest

from chesscoach import config
from chesscoach.concepts import analyse_move, equal_note, role, threat
from chesscoach.describe import describe_move_effects
from chesscoach.engine import run_stockfish
from chesscoach.evaluate import load_keys
from chesscoach.features import compare_lines

pytestmark = pytest.mark.engine

ROOK = "2b1k2r/1r4pp/1pp5/p7/2P2B2/3B1N2/1P1K1PPP/R6R w k - 1 18"


@pytest.fixture(scope="module")
def sf():
    from conftest import find_stockfish
    path = find_stockfish()
    if not path:
        pytest.skip("no Stockfish")
    config.settings = config.load_settings(stockfish_path=path)
    e = chess.engine.SimpleEngine.popen_uci(path)
    yield e
    e.quit()


def key(kid):
    k = load_keys(ids=[kid])[0]
    b = k.board()
    a = b.copy()
    a.push(k.played())
    return k, b, a


def test_real_threat_found(sf):
    # Before 8...Bg4, White threatens hxg6 — the reason ...g5 was better.
    _, b, _ = key("homer-8-bg4")
    t = threat(sf, b)
    assert t and t.side == chess.WHITE
    assert t.words == "White captures Black's pawn on g6 with the pawn from h5"


def test_extra_tempo_is_not_a_threat(sf):
    # After 9.Nxd4, a free move for White would take on c6, but that gains
    # nothing over other uses of the tempo (Nxc6 bxc6 is an even trade).
    _, _, a = key("test-9-nxd4")
    assert threat(sf, a) is None


def test_quiet_moves_are_not_threats(sf):
    # After 12...Kxf7 a free move would develop the knight: not a threat.
    _, _, a = key("homer-12-kxf7")
    assert threat(sf, a) is None


def test_no_threat_when_in_check(sf):
    _, _, a = key("rook-check")
    assert threat(sf, a) is None


def test_role_of_a_piece_holding_something(sf):
    # Black's rook on e8 guards the back rank: without it, Rd8 is mate.
    b = chess.Board("4r1k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1")
    r = role(sf, b, chess.E8)
    assert r is not None
    assert r.stops == "White moves the rook from d1 to d8, delivering CHECKMATE"
    # A piece doing nothing in particular has no role.
    b = chess.Board("4r1k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1")
    b.set_piece_at(chess.A8, chess.Piece(chess.KNIGHT, chess.BLACK))
    assert role(sf, b, chess.A8) is None


def test_equal_moves_note(sf):
    k, b, _ = key("rook-check")
    eb = run_stockfish(b, depth=18, engine=sf)
    note = equal_note(b, k.played(), eb)
    assert "practically equal" in note
    assert "rook from a1 to e1" in note


@pytest.mark.parametrize("kid,expected", [
    ("homer-8-bg4", "did not deal with a threat"),
    ("homer-9-dxc4", "pawn on c4 has no defender"),
    ("test-9-nxd4", "no longer defends the White pawn on e5"),
])
def test_key_facts_lead_with_what_matters(sf, kid, expected):
    k, b, a = key(kid)
    mv = k.played()
    eb, ea = run_stockfish(b, depth=18, engine=sf), run_stockfish(a, depth=18, engine=sf)
    c = analyse_move(sf, b, mv, eb, ea, describe_move_effects(b, mv),
                     compare_lines(b, mv, eb, ea))
    assert 1 <= len(c.key_facts) <= 5
    assert any(expected in f for f in c.key_facts[:2]), c.key_facts


def test_prompt_puts_key_facts_first(sf):
    from chesscoach.narrator import prepare_move
    k, b, a = key("homer-8-bg4")
    eb, ea = run_stockfish(b, depth=18, engine=sf), run_stockfish(a, depth=18, engine=sf)
    prep = prepare_move(b, k.played(), eb, ea, None, engine=sf)
    p = prep["prompt"]
    assert p.index("KEY FACTS") < p.index("WHAT THE MOVE DID")
    assert "the KEY FACTS first" in p
    config.settings.concepts = False
    try:
        assert "KEY FACTS" not in prepare_move(b, k.played(), eb, ea, None, engine=sf)["prompt"]
    finally:
        config.settings.concepts = True


def test_no_role_for_a_piece_that_stops_nothing_specific(sf):
    # The first phase-3 run said the bishop on c8 was 'holding' g6 before
    # 8...Bg4: removing it weakens Black, but White's best move (hxg6) is
    # the same with or without it, and the bishop doesn't cover g6.
    _, b, _ = key("homer-8-bg4")
    assert role(sf, b, chess.C8) is None


def test_a_pawn_moving_away_is_not_the_line_going_for_it():
    from chesscoach.concepts import _pv_captures
    # After 5...c6 the engine's line is cxd5: the c4 pawn moves, it is not taken.
    b = chess.Board("rnbqk2r/ppp1ppbp/5np1/3p2B1/2PP4/2N2N2/PP2PPPP/R2QKB1R b KQkq - 1 5")
    b.push_san("c6")
    assert not _pv_captures(b, ["cxd5", "cxd5"], chess.C4)
    assert _pv_captures(b, ["cxd5", "cxd5"], chess.D5)


def test_equal_moves_are_written_by_code(sf):
    from chesscoach.narrator import prepare_move
    k, b, a = key("rook-check")
    eb, ea = run_stockfish(b, depth=18, engine=sf), run_stockfish(a, depth=18, engine=sf)
    prep = prepare_move(b, k.played(), eb, ea, None, engine=sf)
    # Which rook check Stockfish ranks first flips with hash state and build;
    # either way the comment must say the two are equal.
    first = "White moves the rook from h1 to e1, giving check. "
    assert prep["fixed"] in (
        first + "The engine preferred another move (White moves the rook from a1 to e1, "
                "giving check), but the two are practically equal.",
        first + "Another move (White moves the rook from a1 to e1, giving check) is "
                "practically as good.")


def test_equal_moves_both_ways():
    # Whether the played move is the engine's first or second choice, two
    # moves within 3 points of winning chance are called equal.
    from chesscoach.concepts import equal_sentence
    from chesscoach.engine import EngineAnalysis
    b = chess.Board(ROOK)
    mv = b.parse_san("Rhe1+")
    first = EngineAnalysis(score_cp=900, best_move="Rhe1+",
                           best_line=["Rhe1+", "Kd8"],
                           alt_lines=[(880, ["Rae1+", "Kd8"])], alt_scores=[(880, None)])
    assert equal_sentence(b, mv, first) == (
        "Another move (White moves the rook from a1 to e1, giving check) is practically as good.")
    second = EngineAnalysis(score_cp=900, best_move="Rae1+",
                            best_line=["Rae1+", "Kd8"],
                            alt_lines=[(880, ["Rhe1+", "Kd8"])], alt_scores=[(880, None)])
    assert equal_sentence(b, mv, second).startswith("The engine preferred another move")
    far = EngineAnalysis(score_cp=900, best_move="Rhe1+", best_line=["Rhe1+"],
                         alt_lines=[(100, ["Rae1+"])], alt_scores=[(100, None)])
    assert equal_sentence(b, mv, far) == ""
