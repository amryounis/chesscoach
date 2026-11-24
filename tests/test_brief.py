"""The coach's sheet for "Explain this position": written by code, true by construction."""
import chess
import chess.engine
import pytest

from chesscoach import brief, config

# Amr's test position (2026-11-24): ChatGPT, Gemini and ChessBase-style
# answers missed that White's c5 pawn has no defender and that Black's
# f8 bishop is aimed at it behind e7, which is why the engine likes b4.
AMR = "rn1qkbnr/pp2pppp/8/2PpPb2/8/8/PPP2PPP/RNBQKBNR w KQkq - 0 1"


@pytest.fixture
def sf(stockfish):
    config.settings = config.load_settings(stockfish_path=stockfish)
    e = chess.engine.SimpleEngine.popen_uci(stockfish)
    yield e
    e.quit()


def test_the_pawn_with_no_defender_and_what_is_aimed_at_it():
    b = chess.Board(AMR)
    assert brief.aimed_through_own_pawn(b, chess.C5) == [(chess.F8, chess.E7)]
    lines = brief.pawn_lines(b)
    assert ("White's pawn on c5 has no defender. Black's bishop on f8 is aimed at it, behind "
            "its own pawn on e7: once that pawn moves, it attacks it.") in lines
    assert "White has doubled pawns on the c-file (c2 and c5)." in lines
    assert brief.protects(b, chess.Move.from_uci("b2b4")) == [
        "protects the pawn on c5, which had no defender"]


def test_kings_pieces_and_threats():
    b = chess.Board(AMR)
    kings = brief.king_lines(b)
    assert kings[0].startswith("White's king is still in the centre on e1 and can still castle "
                               "on either side")
    pieces = brief.piece_lines(b)
    assert "White has not developed a knight or bishop yet." in pieces
    assert "Black's bishop on f5 is undefended." in pieces
    att = brief.attack_lines(b)
    assert "Black's bishop on f5 attacks White's pawn on c2, defended by the queen on d1." in att


def test_the_sheet_on_amrs_position(sf):
    br = brief.build(chess.Board(AMR), sf)
    t = br.text()
    assert t.startswith("**Overview**: White is to move, and ")
    assert "White is up a pawn." in t and "Pawns: White has eight, Black seven." in t
    cands = dict(br.sections)["Candidate moves for White"]
    b4 = [c for c in cands if c.startswith("b4")]
    assert b4 and "It protects the pawn on c5, which had no defender." in b4[0]
    assert any(a.startswith("Qxd5??: loses a queen") for a in dict(br.sections)["Moves to avoid"])
    # no section claims a pawn break Black can't make
    assert "...c5" not in t and "c-pawn" not in t


def test_each_line_says_what_to_draw(sf):
    br = brief.build(chess.Board(AMR), sf)
    lines = [x for _, ls in br.sections for x in ls]
    marks = br.marks()
    assert len(marks) == len(lines)
    c5 = [m for x, m in zip(lines, marks) if x.startswith("White's pawn on c5")][0]
    assert c5 == {"main": False, "shapes": [
        {"orig": "c5", "brush": "red"}, {"orig": "f8", "dest": "c5", "brush": "threat"},
        {"orig": "e7", "brush": "yellow"}]}
    b4 = [m for x, m in zip(lines, marks) if x.startswith("b4")][0]
    assert b4["main"] and {"orig": "b4", "dest": "c5", "brush": "defend"} in b4["shapes"]
    qxd5 = [m for x, m in zip(lines, marks) if x.startswith("Qxd5")][0]
    assert qxd5["shapes"] == [{"orig": "d1", "dest": "d5", "brush": "red"},
                              {"orig": "d8", "dest": "d5", "brush": "paleRed"}]
