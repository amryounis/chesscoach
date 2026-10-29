"""Tactical motifs in engine lines, and accuracy % (Lichess's method)."""
import chess

from chesscoach.accuracy import game_accuracy, move_accuracy
from chesscoach.motifs import line_motifs


def _m(fen, line, mate=None):
    return line_motifs(chess.Board(fen), line.split(), mate)


def test_fork():
    assert "fork" in _m("r3k3/8/8/1N6/8/8/8/4K3 w - - 0 1", "Nc7+ Kd7 Nxa8")
    # attacking two things is not a fork unless the line wins one
    assert "fork" not in _m("r3k3/8/8/1N6/8/8/8/4K3 w - - 0 1", "Nc7+ Kd7")


def test_skewer():
    assert "skewer" in _m("7q/8/8/7k/8/8/4K3/R7 w - - 0 1", "Rh1+ Kg5 Rxh8")


def test_pin():
    assert "pin" in _m("4k3/4n3/8/8/8/8/8/K6R w - - 0 1", "Re1 Kf7 Rxe7+")


def test_hanging_piece():
    assert _m("4k3/8/8/3n4/8/8/8/3QK3 w - - 0 1", "Qxd5") == ["hanging_piece"]


def test_back_rank_mate():
    assert _m("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1", "Ra8#") == ["mate", "back_rank_mate"]


def test_double_check_and_promotion():
    # Bc6+ moves off the e-file: the rook on e1 checks too (and it is discovered)
    tags = _m("4k3/8/8/8/4B3/8/8/4R1K1 w - - 0 1", "Bc6+")
    assert "double_check" in tags and "discovered_attack" in tags
    assert "promotion" in _m("8/4P3/8/8/8/8/k7/4K3 w - - 0 1", "e8=Q")


def test_nothing_in_a_quiet_line():
    assert _m(chess.STARTING_FEN, "e4 e5 Nf3 Nc6") == []


def test_move_accuracy():
    assert move_accuracy(60, 60) == 100 and move_accuracy(60, 70) == 100
    assert 95 < move_accuracy(60, 59) <= 100
    assert move_accuracy(80, 20) < 15


def test_game_accuracy_sides():
    # White keeps everything; Black throws away 40 points once.
    wins = [50, 50, 50, 50, 90, 90, 90, 90, 90]
    acc = game_accuracy(wins)
    assert acc["white"] == 100.0 and acc["black"] < 60
    assert game_accuracy([50]) == {"white": None, "black": None}
    # From a position with Black to move, the first move is Black's.
    assert game_accuracy([50, 90, 90], white_first=False)["black"] < 60


def test_conservative_tags_from_a_real_game():
    """Homer vs Fritz, 11.hxg6: the engine had Bxf7+ Kxf7 Ne5+ (a fork of the
    king and the g4 bishop, uncovering the queen on it). The bishop won at
    the end is not a 'hanging piece'; hxg6 onto the 6th rank is no
    'advanced pawn'; a knight given for a bishop is no 'sacrifice'."""
    b = chess.Board("rn1qk2r/p3pp2/2p2bpp/1p5P/2BP2b1/2N1PN2/PP3PP1/R2QK2R w KQkq - 0 11")
    tags = line_motifs(b, "Bxf7+ Kxf7 Ne5+ Bxe5 Qxg4 Bf6".split())
    assert "fork" in tags and "discovered_attack" in tags
    assert "hanging_piece" not in tags and "sacrifice" not in tags
    b2 = chess.Board("rn1qk2r/p3pp2/2p2bpp/1p5P/2pP2b1/2N1PN2/PP3PP1/R2QK2R w KQkq - 0 11")
    assert "advanced_pawn" not in line_motifs(b2, "hxg6 fxg6".split())
    assert "advanced_pawn" in _m("4k3/8/2P5/8/8/8/8/4K3 w - - 0 1", "c7")
