from conftest import TEST_GAME, position

from chesscoach.engine import EngineAnalysis
from chesscoach.features import compare_lines, extract_features
from chesscoach.tactics import extract_tactical_features


def after(ply):
    b, m = position(TEST_GAME, ply)
    b.push(m)
    return b


def test_negatives_are_explicit():
    t = extract_tactical_features(after(16), EngineAnalysis())
    assert "Pins: none" in t
    assert "Mate threats: none" in t
    assert "defended by: NOBODY" not in t      # nothing hanging after 8...cxd4


def test_hanging_piece_is_marked():
    t = extract_tactical_features(after(17), EngineAnalysis())
    assert "White pawn on e5 [HANGING]" in t


def test_exchange_in_progress_hides_material():
    s = extract_features(after(16)).summary(exchange_in_progress=True)
    assert "NOT ASSESSED" in s and "Material balance" not in s


def test_feature_summary_states_none():
    s = extract_features(after(16)).summary()
    assert "Bishop pair: both sides have it" in s
    assert "Knight outposts: none" in s


def test_compare_lines_sorts_by_side():
    b, m = position(TEST_GAME, 17)          # 9.Nxd4 vs engine's exf6
    eb = EngineAnalysis(score_cp=302, best_move="exf6",
                        best_line="exf6 dxe3 fxe7 exf2+ Rxf2".split())
    ea = EngineAnalysis(score_cp=-182, best_move="Nxe5",
                        best_line="Nxe5 f4 Ng6 a3 Re8".split())
    out = compare_lines(b, m, eb, ea)
    assert out[0].startswith("- In favour of the ENGINE'S CHOICE")
    eng = out[1:out.index("- In favour of the PLAYER'S MOVE (what the player gained):")]
    assert any("material" in line for line in eng)   # exf6 wins material
