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


# --- Theory claims need evidence (review feedback, 0.11.x) -----------------

from types import SimpleNamespace

from chesscoach.moments import LEFT_BOOK, LEFT_NAMED, moment_context, pick_critical
from chesscoach.opening import theory_exit


def _quiet_evals(n):
    return [SimpleNamespace(best_move=None, alt_scores=[], score_cp=0, score_mate=None)
            for _ in range(n + 1)]


def test_no_book_never_claims_move_one_left_theory():
    boards, moves = _game(CM10)
    no_book = [False] * len(moves)                      # what book_status gives with no book
    theory = theory_exit(boards, moves, no_book)
    assert theory == {"ply": 10, "source": "names",
                      "opening": "Sicilian Defense: Dragon Variation"}
    picks = pick_critical(boards, moves, _quiet_evals(len(moves)), ["Good"] * len(moves),
                          no_book, [chess.WHITE, chess.BLACK], theory=theory)
    assert 0 not in picks
    assert picks == {10: LEFT_NAMED}
    # The move notes and the moment picker agree on where theory ended.
    notes = opening_notes(boards, moves, no_book)
    assert [i for i, n in enumerate(notes) if "left_theory" in n] == [10]
    assert notes[10]["left_theory"]["source"] == "names"


def test_no_evidence_no_claim():
    # From a middlegame position nothing is a named opening, and with no
    # book there is nothing to say about theory at all.
    b = chess.Board("r1bq1rk1/pp2bppp/2n1pn2/2pp4/3P4/2PBPN2/PP1N1PPP/R1BQ1RK1 w - - 0 8")
    boards, moves = [b.copy()], []
    for san in "dxc5 Bxc5 e4 dxe4".split():
        m = b.parse_san(san)
        moves.append(m)
        b.push(m)
        boards.append(b.copy())
    no_book = [False] * len(moves)
    assert theory_exit(boards, moves, no_book) == {}
    assert not any("left_theory" in n for n in opening_notes(boards, moves, no_book))
    picks = pick_critical(boards, moves, _quiet_evals(len(moves)), ["Good"] * len(moves),
                          no_book, [chess.WHITE, chess.BLACK], theory={})
    assert picks == {}


def test_book_evidence_wins_and_a_fully_booked_game_never_leaves():
    boards, moves = _game(CM10)
    book = [True] * 6 + [False] * (len(moves) - 6)
    theory = theory_exit(boards, moves, book)
    assert theory["ply"] == 6 and theory["source"] == "book"
    picks = pick_critical(boards, moves, _quiet_evals(len(moves)), ["Good"] * len(moves),
                          book, [chess.WHITE], theory=theory)
    assert picks == {6: LEFT_BOOK}
    note, prefix, _ = moment_context(6, LEFT_BOOK, boards, moves, ["Good"] * len(moves),
                                     book, theory=theory)
    assert prefix.startswith("This is where the game left known opening theory")
    assert theory_exit(boards, moves, [True] * len(moves)) == {}


def test_named_lines_wording_does_not_claim_theory():
    boards, moves = _game(CM10)
    no_book = [False] * len(moves)
    theory = theory_exit(boards, moves, no_book)
    note, prefix, _ = moment_context(10, LEFT_NAMED, boards, moves, ["Good"] * len(moves),
                                     no_book, theory=theory)
    assert prefix == "This is where the game left the named opening lines. "
    assert "Sicilian Defense: Dragon Variation" in note and "opening book" not in note
