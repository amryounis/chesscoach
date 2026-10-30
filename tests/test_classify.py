"""Lucas-style move classes and the per-side, per-phase counts."""
from types import SimpleNamespace as NS

import chess

from chesscoach.classify import classify, stats


def ea(best="", cp=0, mate=None, alts=(), line=()):
    return NS(best_move=best, score_cp=cp, score_mate=mate, alt_scores=list(alts),
              best_line=list(line))


def _cls(fen, san, label, before, after, book=False):
    b = chess.Board(fen)
    return classify(b, b.parse_san(san), label, book, before, after)


def test_errors_book_best_and_acceptable():
    s = chess.STARTING_FEN
    assert _cls(s, "e4", "Excellent", ea("e4", 30), ea(cp=30), book=True) == "book"
    assert _cls(s, "e4", "Blunder", ea("d4"), ea()) == "??"
    assert _cls(s, "e4", "Inaccuracy", ea("d4"), ea()) == "?!"
    assert _cls(s, "e4", "Excellent", ea("e4", 30), ea(cp=30)) == "best"
    assert _cls(s, "a3", "Good", ea("e4", 30), ea(cp=0)) == "ok"


def test_only_move_is_good_and_a_sacrifice_brilliant(monkeypatch):
    import chesscoach.classify as cl
    fen = "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1"
    only = ea("Rd1", 0, None, [(-300, None)])                 # the next best loses 30+ points
    assert _cls(fen, "Rd1", "Excellent", only, ea(cp=0)) == "!"
    near = ea("Rd1", 0, None, [(-40, None)])                  # others nearly as good
    assert _cls(fen, "Rd1", "Excellent", near, ea(cp=0)) == "best"
    # a sacrifice in the line (motifs.py decides that; tested there)
    monkeypatch.setattr(cl, "line_motifs", lambda *a, **k: ["sacrifice"])
    assert _cls(fen, "Rd1", "Excellent", only, ea(cp=0)) == "!!"
    assert _cls(fen, "Rb1", "Good", ea("Rd1", 0), ea(cp=-20)) == "!?"


def test_no_flourish_in_a_decided_game():
    """33.Kb5 in Homer vs Fritz: White was lost; a 'sacrifice' there is not !?."""
    import chesscoach.classify as cl
    fen = "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1"
    cl_motifs = cl.line_motifs
    try:
        cl.line_motifs = lambda *a, **k: ["sacrifice"]
        lost = ea("Rd1", -1500, None, [(-1800, None)])
        assert _cls(fen, "Rb1", "Excellent", lost, ea(cp=-1500)) == "ok"
        assert _cls(fen, "Rd1", "Excellent", lost, ea(cp=-1500)) != "!!"
    finally:
        cl.line_motifs = cl_motifs


def test_stats_per_side_and_phase():
    mid = "r1bq1rk1/pp2bppp/2n1pn2/2pp4/3P4/2PBPN2/PP1N1PPP/R1BQ1RK1 w - - 0 14"
    moves = [{"color": "white", "mark": "best", "book": False, "fen_before": mid,
              "win_before": 50, "win_after": 50},
             {"color": "white", "mark": "?", "book": False, "fen_before": mid,
              "win_before": 50, "win_after": 25},
             {"color": "black", "mark": "book", "book": True, "fen_before": chess.STARTING_FEN,
              "win_before": 50, "win_after": 50}]
    s = stats(moves)
    w = s["white"]
    assert w["counts"]["best"] == 1 and w["counts"]["?"] == 1 and w["best_pct"] == 50
    assert w["phases"]["middlegame"]["moves"] == 2 and w["phases"]["opening"]["accuracy"] is None
    assert 0 < w["phases"]["middlegame"]["accuracy"] < 100
    assert s["black"]["analysed"] == 0 and s["black"]["best_pct"] is None
