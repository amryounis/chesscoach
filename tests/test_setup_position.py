"""Setting up a position by hand: what is wrong, in plain words."""
from chesscoach.setup_position import check


def test_a_legal_position_is_cleaned_of_what_cannot_be():
    r = check("1nbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
    assert r["ok"] and r["fen"].split()[2] == "KQk"          # no a8 rook: no Black O-O-O
    assert r["castling_possible"] == {"K": True, "Q": True, "k": True, "q": False}
    r = check("4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2")
    assert r["ok"] and r["ep_squares"] == ["d6"] and r["fen"].split()[3] == "d6"
    r = check("4k3/8/8/3pP3/8/8/8/4K3 w - c6 0 2")           # no pawn just moved there
    assert r["fen"].split()[3] == "-"


def test_what_is_wrong_is_said_plainly():
    assert check("8/8/8/8/8/8/8/8 w - - 0 1")["problems"] == [
        "The board is empty: put the two kings on it, at least."]
    assert "Black has no king." in check("8/8/8/8/8/8/8/4K3 w - - 0 1")["problems"]
    assert check("4k3/8/8/8/8/8/8/P3K3 w - - 0 1")["problems"] == [
        "A pawn is on the first or the last rank, where pawns can't stand."]
    r = check("4k3/4R3/8/8/8/8/8/4K3 w - - 0 1")
    assert not r["ok"] and r["problems"] == [
        "Black's king is in check, but it is White's move: make it Black's move, "
        "or take the check away."]
    assert not check("not a fen")["ok"]


def test_a_finished_position_opens_with_a_note():
    r = check("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
    assert r["ok"] and r["problems"] == ["Note: this is stalemate; there is nothing to play."]
