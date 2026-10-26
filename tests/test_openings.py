"""Phase 7f: opening names and ideas, leaving theory, notes under every move."""
import chess

from chesscoach.describe import move_notes
from chesscoach.opening import idea_for, name_at, opening_notes

CM10 = "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 g6 Bc4 a6 O-O Bg7 Bf4 e5 Bxe5 dxe5 Bxf7+ Kxf7"


def _game(sans):
    b = chess.Board()
    boards, moves = [b.copy()], []
    for san in sans.split():
        m = b.parse_san(san)
        moves.append(m)
        b.push(m)
        boards.append(b.copy())
    return boards, moves


def test_names_ideas_and_where_theory_ended():
    boards, moves = _game(CM10)
    notes = opening_notes(boards, moves)
    names = {i: n["opening"]["name"] for i, n in enumerate(notes) if "opening" in n}
    assert names[1] == "Sicilian Defense" and names[9] == "Sicilian Defense: Dragon Variation"
    assert "d6 pawn stops e5" in notes[9]["opening"]["idea"]
    assert "idea" not in notes[3]["opening"]            # same idea not repeated
    assert not any(n == "Sicilian Defense" for i, n in names.items() if i > 1)   # no going back
    left = notes[10]["left_theory"]                     # 6.Bc4 leaves the named lines
    assert left["opening"] == "Sicilian Defense: Dragon Variation"
    assert [a["san"] for a in left["alternatives"]] == ["Be3", "Be2"]
    # Transpositions are named by position, not by move order.
    b = chess.Board()
    for san in "d4 Nf6 c4 e6 Nc3 Bb4".split():
        b.push_san(san)
    assert name_at(b)[1] == "Nimzo-Indian Defense"
    assert idea_for("Sicilian Defense: Dragon Variation, Yugoslav Attack")[0] == \
        "Sicilian Defense: Dragon Variation"
    assert idea_for("Amar Opening") == ("", "")


def test_every_idea_names_a_real_opening():
    import gzip, json
    from importlib import resources
    from chesscoach.opening import _ideas
    with resources.files("chesscoach").joinpath("data/openings.json.gz").open("rb") as f:
        names = {v[1] for v in json.loads(gzip.decompress(f.read())).values()}
    for key in _ideas():
        assert any(n == key or n.startswith(key + ":") or n.startswith(key + ",")
                   for n in names), key


def test_move_notes_put_the_point_first():
    boards, moves = _game(CM10)
    assert move_notes(boards[15], moves[15]) == [
        "It is a fork: the pawn on e5 attacks the White knight on d4 and the White "
        "bishop on f4 at once"]
    assert move_notes(boards[10], moves[10])[0] == "It clears the way for White to castle kingside"
    notes = move_notes(boards[18], moves[18])               # 10.Bxf7+
    assert notes[0] == "It gives check" and "bishop for a pawn" in notes[1]
    assert notes[2].startswith("It IGNORES a threat")
