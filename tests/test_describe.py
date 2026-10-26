from conftest import TEST_GAME, position

from chesscoach.describe import describe_move, describe_move_effects


def test_capture_names_both_sides():
    b, m = position(TEST_GAME, 16)          # 8...cxd4
    assert describe_move(b, m) == "Black captures White's pawn on d4 with the pawn from c5"


def test_checkmate_is_called_checkmate(homer_game):
    boards, moves = homer_game
    assert describe_move(boards[-2], moves[-1]).endswith("delivering CHECKMATE")


def test_ignored_threat_is_reported():
    b, m = position(TEST_GAME, 16)          # 8...cxd4 leaves Nf6 attacked
    effects = describe_move_effects(b, m)
    assert any("IGNORES a threat" in e and "knight on f6" in e for e in effects)


def test_lost_defender_is_reported_not_invented():
    b, m = position(TEST_GAME, 17)          # 9.Nxd4 stops defending e5
    effects = describe_move_effects(b, m)
    assert any("no longer defends the White pawn on e5" in e and "NO defenders" in e
               for e in effects)
    # and it correctly removes the threat to c3/e3 (ministral inverted this)
    assert any("removes the threat to the White bishop on e3" in e for e in effects)


def test_counter_attack_is_not_called_ignoring():
    b, _ = position(TEST_GAME, 17)
    effects = describe_move_effects(b, b.parse_san("exf6"))
    assert any("counter-attack" in e for e in effects)
    assert not any("IGNORES" in e for e in effects)


def test_forks_and_castling_made_possible_are_named():
    # Amr's game (Chessmaster 10 named the fork; we only listed the attacks).
    import chess
    from chesscoach.describe import describe_move_effects
    b = chess.Board()
    effects = {}
    for san in "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 g6 Bc4 a6 O-O Bg7 Bf4 e5".split():
        m = b.parse_san(san)
        effects[san] = describe_move_effects(b, m)
        b.push(m)
    assert ("It is a fork: the pawn on e5 attacks the White knight on d4 and the White "
            "bishop on f4 at once") in effects["e5"]
    assert "It clears the way for White to castle kingside" in effects["Bc4"]
    assert not any("fork" in e for e in effects["Bf4"])
    # Two defended pawns are not a fork, and a forker that is simply lost is not either.
    b = chess.Board("4k3/2p1p3/3p4/8/4N3/8/8/4K3 w - - 0 1")
    assert not any("fork" in e for e in describe_move_effects(b, b.parse_san("Nf6+")))


def test_move_shapes_back_the_notes_with_arrows():
    import chess.pgn
    from chesscoach.describe import move_notes, move_shapes
    from conftest import DATA
    g = chess.pgn.read_game(open(DATA / "homer_vs_fritz.pgn", encoding="utf-8"))
    b, shapes = g.board(), {}
    for m in g.mainline_moves():
        shapes[b.san(m) + str(b.fullmove_number)] = (move_shapes(b, m), move_notes(b, m))
        b.push(m)
    # 6...h6 attacks the bishop on g5: a red arrow h6-g5.
    assert shapes["h66"][0] == [{"from": "h6", "to": "g5", "kind": "attack"}]
    # 13...Qb6: the note says it ignores the threat to c4, so an arrow shows it,
    # plus what the queen attacks and newly defends.
    qb6, notes = shapes["Qb613"]
    assert {"from": "a4", "to": "c4", "kind": "threat"} in qb6 and "IGNORES" in notes[0]
    assert {"from": "b6", "to": "b2", "kind": "attack"} in qb6
    assert any(s["kind"] == "defend" for s in qb6)
    # A check is an attack on the king.
    assert {"from": "f7", "to": "e8", "kind": "attack"} in shapes["gxf7+12"][0]


def test_a_pawn_attack_always_gets_an_arrow():
    # 2.c4 against the pawn on d5 (defended, equal value): the point of the move.
    import chess
    from chesscoach.describe import move_shapes
    b = chess.Board()
    for san in ("Nf3", "d5"):
        b.push_san(san)
    assert move_shapes(b, b.parse_san("c4")) == [{"from": "c4", "to": "d5", "kind": "attack"}]
