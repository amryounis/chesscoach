"""Tier 1 facts: the gaps the October 2026 model comparison exposed."""
import chess

from chesscoach.facts import (castling_lines, legal_move_line, loose_pieces,
                              material_lines, play_line, position_facts)
from chesscoach.features import extract_features
from chesscoach.tactics import extract_tactical_features
from chesscoach.engine import EngineAnalysis

ROOK_CHECK = "2b1k2r/1r4pp/1pp5/p7/2P2B2/3B1N2/1P1K1PPP/R6R w k - 1 18"


def test_material_named_piece_by_piece():
    lines = material_lines(chess.Board(ROOK_CHECK))
    assert lines[0] == "White has: the king, two rooks, two bishops, a knight, five pawns"
    assert lines[1] == "Black has: the king, two rooks, a bishop, five pawns"
    assert "White is up a bishop and a knight" in lines[2]
    assert "about 6 pawns' worth" in lines[2]


def test_material_level_and_mixed():
    assert "Material is level" in material_lines(chess.Board())[-1]
    # White has a rook, Black a bishop and two pawns: different pieces.
    b = chess.Board("4k3/pp6/8/8/8/8/8/R3K3 w - - 0 1")
    b.set_piece_at(chess.C6, chess.Piece(chess.BISHOP, chess.BLACK))
    line = material_lines(b)[-1]
    assert "White is up a rook" in line and "Black is up a bishop and two pawns" in line
    assert "roughly balanced" in line


def test_feature_summary_uses_named_material():
    text = extract_features(chess.Board(ROOK_CHECK)).summary()
    assert "White is up a bishop and a knight" in text
    assert " cp " not in text


def test_castling_rights_reported_even_when_not_possible_now():
    lines = castling_lines(chess.Board(ROOK_CHECK))
    assert lines[0] == "White can no longer castle."
    assert lines[1].startswith("Black can still castle kingside")
    b = chess.Board(ROOK_CHECK)
    b.push_san("Rhe1+")
    assert "cannot castle on this move because its king is in check" in castling_lines(b)[1]


def test_castling_legal_now():
    b = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    assert "Castling kingside and queenside is legal on this move" in castling_lines(b)[0]


def test_loose_pieces_include_unattacked_ones():
    b = chess.Board(ROOK_CHECK)
    names = [chess.square_name(s) for s in loose_pieces(b, chess.BLACK)]
    assert names == ["c8", "h8"]
    facts = extract_tactical_features(b, EngineAnalysis())
    assert "Black bishop on c8 (not attacked yet)" in facts
    assert "Black can still castle kingside" in facts


def test_legal_move_count():
    b = chess.Board(ROOK_CHECK)
    b.push_san("Rhe1+")
    assert legal_move_line(b) == ("Black (to move) has 6 legal moves "
                                  "(it must answer a check).")
    assert legal_move_line(b, with_list=True).endswith("Be6, Re7.")


def test_line_tracks_a_pawn_that_changes_file():
    # The error two models made: after bxa5 the b-pawn is an a-pawn, and
    # a later 'axb6' is that same pawn capturing back onto the b-file.
    b = chess.Board("4k3/8/1p6/p7/1P6/8/8/4K3 w - - 0 1")
    r = play_line(b, ["bxa5", "Kd7", "axb6"])
    assert not r.error
    assert "it is now White's a-pawn on a5, and White has no pawn left on the b-file" \
        in r.steps[0].notes[0]
    assert "(the b-pawn when the line began)" in r.steps[2].notes[0]
    assert r.board.piece_at(chess.B6) == chess.Piece(chess.PAWN, chess.WHITE)


def test_line_rejects_an_illegal_move_with_the_legal_list():
    b = chess.Board("4k3/8/8/p7/1P6/8/8/4K3 w - - 0 1")
    r = play_line(b, ["bxa5", "Kd7", "a6", "Kc7", "axb6"])
    assert len(r.steps) == 4
    assert "move 5 'axb6' is not legal for White" in r.error
    assert "a7" in r.error


def test_check_evasion_is_not_called_ignoring_a_threat():
    b = chess.Board(ROOK_CHECK)
    r = play_line(b, ["Rhe1+", "Kf7", "Ne5+", "Kf8"])
    text = r.text()
    assert "IGNORES" not in text
    assert "answers the check" in text


def test_position_facts_has_every_section():
    text = position_facts(chess.Board(ROOK_CHECK))
    for head in ("SIDE TO MOVE", "PIECES", "MATERIAL", "LEGAL MOVES",
                 "TACTICAL FACTS", "POSITIONAL FEATURES", "Castling:",
                 "Undefended pieces"):
        assert head in text


def test_check_against_a_king_that_can_castle():
    from chesscoach.describe import describe_move_effects
    b = chess.Board(ROOK_CHECK)
    eff = describe_move_effects(b, b.parse_san("Rhe1+"))
    assert any("stops Black from castling on the next move" in e for e in eff)
    # Every reply is a king move: the rights are gone for good.
    b2 = chess.Board("4k2r/8/8/8/8/8/8/R3K3 w Qk - 0 1")
    eff = describe_move_effects(b2, b2.parse_san("Ra8+"))
    assert any("castling for good" in e for e in eff)


def test_resolve_move_names_candidates_instead_of_calling_them_illegal():
    from chesscoach.facts import resolve_move
    b = chess.Board(ROOK_CHECK)
    mv, why = resolve_move(b, "Re1+")       # both rooks can go to e1
    assert mv is None and "Rhe1+" in why and "Rae1+" in why and "not legal" not in why
    assert resolve_move(b, "Rhe1")[0] == chess.Move.from_uci("h1e1")
    assert resolve_move(b, "h1e1")[0] == chess.Move.from_uci("h1e1")
    assert resolve_move(b, "0-0")[0] is None                   # White cannot castle
    t = chess.Board("r1bq1rk1/pp2bppp/2n1pn2/3pP3/3p4/2N1BN2/PPP1BPPP/R2Q1RK1 w - - 0 9")
    mv, why = resolve_move(t, "d4")         # "take on d4": three captures
    assert mv is None and all(x in why for x in ("Nxd4", "Bxd4", "Qxd4"))
    assert resolve_move(t, "f6")[0] == chess.Move.from_uci("e5f6")   # the only one
    assert "could mean" in play_line(b, ["Re1+"]).error
