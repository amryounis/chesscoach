from chesscoach.assessment import compute_assessment, eval_phrase, win_pct


def test_win_pct_symmetry():
    assert win_pct(0, None, True) == 50
    assert abs(win_pct(300, None, True) + win_pct(300, None, False) - 100) < 1e-9
    assert win_pct(None, 3, True) == 100 and win_pct(None, 3, False) == 0


def test_blunder_from_large_drop():
    # 8...cxd4 in the test game: -0.57 -> +3.02, Black moved
    assert compute_assessment(-57, 302, None, None, False)[0] == "Blunder"


def test_winning_position_swings_are_not_blunders():
    # -6.28 -> -5.42 for Black while completely winning: still fine
    assert compute_assessment(-628, -542, None, None, False)[0] == "Excellent"


def test_losing_a_forced_mate_is_a_blunder():
    assert compute_assessment(None, 100, 5, None, True)[0] == "Blunder"


def test_delivering_mate_is_excellent():
    assert compute_assessment(None, None, -1, 0, False, delivered_mate=True) == ("Excellent", 0)


def test_eval_phrase_bands():
    assert eval_phrase(10, None) == ("the position is roughly equal", False)
    assert eval_phrase(-182, None)[0] == "Black is clearly better"
    assert eval_phrase(302, None)[0] == "White is much better"
    assert eval_phrase(900, None) == ("White is winning", True)
    assert eval_phrase(None, -3) == ("Black has a forced checkmate", True)
