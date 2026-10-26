"""Phase 7g: the move-note vocabulary, checked move by move against
Chessmaster 10's auto-annotation of Homer vs Fritz (and where we differ on
purpose)."""
import chess.pgn

from chesscoach.events import move_events, notes_and_shapes
from conftest import DATA


def _events():
    g = chess.pgn.read_game(open(DATA / "homer_vs_fritz.pgn", encoding="utf-8"))
    b, out = g.board(), {}
    for m in g.mainline_moves():
        label = (f"{b.fullmove_number}." if b.turn else f"{b.fullmove_number}...") + b.san(m)
        out[label] = [(e.kind, e.text) for e in move_events(b, m)]
        b.push(m)
    return out


EV = None


def ev(label):
    global EV
    EV = EV or _events()
    return EV[label]


def texts(label):
    return [t for _, t in ev(label)]


def test_discovered_defence_and_defending_a_threatened_piece():
    # CM: "9.e3 Protects White's pawn at c4"; "14.O-O-O Removes the threat on b2";
    # "14...Qa6 Removes the threat on Black's pawn at c4".
    assert ("It opens the line for the bishop on f1, which now defends the White pawn on c4 "
            "against the pawn on d5") in texts("9.e3")
    assert "It defends the White pawn on b2, which the queen on b6 was attacking" in texts("14.O-O-O")
    assert "It defends the Black pawn on c4, which the queen on a4 was attacking" in texts("14...Qa6")


def test_standing_threats_are_not_repeated():
    # The gambit pawn on c4 hung from move 2 to 9; CM said nothing, nor do we now.
    for label in ("3.d4", "4.Nc3", "5.Bg5", "6.h4", "8.h5"):
        assert not any(k == "threat" for k, _ in ev(label)), label
    # A new threat left standing is still said: 11.hxg6 leaves the bishop on c4.
    assert ev("11.hxg6")[0][0] == "threat"


def test_recaptures_material_and_pins():
    # CM: "7.Bxf6 Partially pins Black's pawn at e7 and Black's bishop at g7".
    assert {"It pins the Black pawn on e7 to the queen on d8",
            "It pins the Black bishop on g7 to the rook on h8"} <= set(texts("7.Bxf6"))
    # CM: "7...Bxf6 Frees Black's pawn at e7 from the pin. … Material is even."
    t = texts("7...Bxf6")
    assert t[0] == "It recaptures on f6" and "It frees the Black pawn on e7 from a pin" in t
    assert "Material is level: a knight against a bishop" in t
    # Mid-exchange the balance is not given (7.Bxf6 can be taken back).
    assert not any(k == "material" for k, _ in ev("7.Bxf6"))
    assert "It pins the White pawn on g2 to the rook on h1" in texts("17...Bxf3")
    assert "Black is up a rook and a pawn" in texts("31...Kxf6")


def test_pawn_events_checks_and_promotion():
    # CM: "12.gxf7+ Checks…, adds a promote threat at f7, creates a passed pawn on f7".
    assert texts("12.gxf7+")[:3] == ["It gives check", "The pawn on f7 is one step from promoting",
                                    "It creates a passed pawn on f7"]
    assert "It pushes the passed pawn to d4" in texts("26...d4")
    # CM: "28...e4+ Checks White's king with a discovered check".
    assert texts("28...e4+")[0] == "It gives a discovered check: the bishop on f6 now checks the king"
    assert "The pawn promotes to a queen on d1" in texts("34...d1=Q")
    assert texts("39...Ne5#") == ["Checkmate"]


def test_notes_and_arrows_agree():
    g = chess.pgn.read_game(open(DATA / "homer_vs_fritz.pgn", encoding="utf-8"))
    b = g.board()
    for i, m in enumerate(g.mainline_moves()):
        if i == 16:                                         # 9.e3
            notes, shapes = notes_and_shapes(b, m)
            assert {"from": "f1", "to": "c4", "kind": "defend"} in shapes
            assert "f1" in notes[0]
        b.push(m)


def test_a_piece_that_can_simply_be_taken():
    # The Chessmaster game: 9.Bxe5 dxe5 and 10.Bxf7+ Kxf7 each gave a bishop for a pawn.
    import chess
    b = chess.Board()
    out = {}
    for san in "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 g6 Bc4 a6 O-O Bg7 Bf4 e5 Bxe5 dxe5 Bxf7+".split():
        m = b.parse_san(san)
        out[san] = [e.text for e in move_events(b, m)]
        b.push(m)
    assert out["Bxe5"][0] == ("It takes the pawn, but the pawn on d6 can take the bishop back: "
                              "a bishop for a pawn")
    assert "It takes the pawn, but the king on e8 can take the bishop back: a bishop for a pawn" \
        in out["Bxf7+"]
    assert not any("can take it" in t for t in texts("2.c4"))        # a gambit pawn
