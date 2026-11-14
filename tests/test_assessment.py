import pytest
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
    assert compute_assessment(-628, -542, None, None, False)[0] in ("Excellent", "Good")


def test_lichess_thresholds():
    """Lichess's .1 / .2 / .3 on its -1..1 scale: 5 / 10 / 15 points of win%.
    Amr's game (Player - Stockfish 1500, 2026.11.14), Black's moves:"""
    a = lambda before, after: compute_assessment(before, after, None, None, False)[0]
    assert a(29, 337) == "Blunder"            # 10...O-O, a piece lost (drop 25)
    assert a(324, 841) == "Blunder"           # 20...Bf5, the queen (drop 19); was "Inaccuracy"
    assert a(276, 473) == "Mistake"           # 12...Be6 (drop 12)
    assert a(828, 1027) in ("Excellent", "Good")   # 21...Bxc5: the game was already lost
    from chesscoach.assessment import label_for_drop
    assert [label_for_drop(d) for d in (1.9, 4.9, 5, 9.9, 10, 14.9, 15)] == [
        "Excellent", "Good", "Inaccuracy", "Inaccuracy", "Mistake", "Mistake", "Blunder"]
    assert win_pct(1500, None, True) == win_pct(1000, None, True)      # capped at 10 pawns


def test_mates_judged_as_lichess_does():
    # allowing a mate: by how lost the mover already was
    assert compute_assessment(-300, None, None, -4, True)[0] == "Blunder"
    assert compute_assessment(-800, None, None, -4, True)[0] == "Mistake"
    assert compute_assessment(-1200, None, None, -4, True)[0] == "Inaccuracy"
    assert compute_assessment(300, None, None, 4, False)[0] == "Blunder"      # Black allows
    # losing your own mate: by how much is left
    assert compute_assessment(None, 1200, 3, None, True)[0] == "Inaccuracy"
    assert compute_assessment(None, 800, 3, None, True)[0] == "Mistake"
    assert compute_assessment(None, None, -3, 2, False)[0] == "Blunder"       # Black's mate, now White's
    # a longer mate is still a mate
    assert compute_assessment(None, None, 3, 5, True)[0] == "Excellent"


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


@pytest.mark.engine
def test_a_delivered_mate_keeps_its_winner(stockfish):
    """Review feedback: Stockfish says "mate 0" for a checkmated position and
    .mate() dropped the sign, so White got 0% after White had mated."""
    import chess
    from chesscoach import config
    from chesscoach.assessment import eval_phrase, win_pct
    from chesscoach.engine import run_stockfish
    config.settings = config.load_settings(stockfish_path=stockfish, depth=8)
    white_mated_black = chess.Board("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1")
    white_mated_black.push_san("Ra8#")
    ea = run_stockfish(white_mated_black)
    assert ea.score_mate == 1 and win_pct(ea.score_cp, ea.score_mate, True) == 100.0
    assert eval_phrase(ea.score_cp, ea.score_mate)[0].startswith("White")
    fools = chess.Board()
    for san in "f3 e5 g4 Qh4#".split():
        fools.push_san(san)
    ea = run_stockfish(fools)
    assert ea.score_mate == -1 and win_pct(ea.score_cp, ea.score_mate, False) == 100.0


@pytest.mark.engine
def test_no_position_takes_longer_than_the_cap(stockfish, monkeypatch):
    """A depth the engine can't reach quickly stops at max_seconds, so one
    hard position can't stall a review for minutes."""
    import time
    import chess
    from chesscoach import config
    from chesscoach.engine import run_stockfish
    monkeypatch.setattr(config, "settings",
                        config.load_settings(stockfish_path=stockfish, depth=60, max_seconds=0.5))
    t = time.time()
    ea = run_stockfish(chess.Board("r1bq1rk1/pp2bppp/2n1pn2/2pp4/3P4/2PBPN2/PP1N1PPP/R1BQ1RK1 w - - 0 8"))
    assert time.time() - t < 3 and ea.best_move and ea.depth < 60
