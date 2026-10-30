"""Annotated PGN export: what other chess programs read back."""
import io

import chess
import chess.pgn

from chesscoach.pgn_export import annotated_pgn, plain_comment


def _review():
    b = chess.Board()
    moves = []
    for i, (san, label, extra) in enumerate([
            ("e4", "Book", {"opening": {"eco": "B00", "name": "King's Pawn Game"}}),
            ("e5", "Book", {"opening": {"eco": "C20", "name": "King's Pawn Game: open"}}),
            ("Qh5", "Inaccuracy", {"reason": "inaccuracy", "best_line": ["Nf3", "Nc6", "Bb5"],
                                   "best_cp": 35, "best_mate": None,
                                   "annotations": {"m": "**Assessment**: x\n**What happened**: "
                                                   "The queen comes out early.\n**Better was**: "
                                                   "**Nf3** develops {fast}."},
                                   "tactics": {"allowed": ["fork"], "missed": []}}),
            ("Nc6", "Good", {}),
            ("Bc4", "Good", {"mark": "!?"}),
            ("Nf6", "Blunder", {"reason": "blunder", "best_line": ["g6"], "best_cp": -20,
                                "annotations": {}}),
            ("Qxf7#", "Excellent", {"reason": "delivered checkmate"})]):
        mv = b.parse_san(san)
        moves.append({"ply": i, "san": san, "uci": mv.uci(), "label": label,
                      "score_cp": 30, "score_mate": None, "color": "white" if b.turn else "black",
                      **extra})
        b.push(mv)
    return {"headers": {"White": "Me", "Black": "You", "Result": "1-0"}, "side": "both",
            "models": ["qwen3:14b"], "start_fen": chess.STARTING_FEN, "moves": moves,
            "accuracy": {"white": 92.0, "black": 41.5},
            "summary": [{"text": "Black made one blunder.", "kind": "counts"}]}


def test_export_reads_back_with_marks_evals_and_variations():
    text = annotated_pgn(_review())
    g = chess.pgn.read_game(io.StringIO(text))
    assert not g.errors and g.headers["Result"] == "1-0"
    assert g.headers["Annotator"].startswith("chesscoach") and g.headers["ECO"] == "C20"
    assert "Accuracy: White 92%, Black 42%" in g.comment
    main = list(g.mainline())
    assert main[2].nags == {chess.pgn.NAG_DUBIOUS_MOVE} and main[5].nags == {chess.pgn.NAG_BLUNDER}
    assert main[6].nags == {chess.pgn.NAG_GOOD_MOVE}                     # the mate
    assert main[4].nags == {chess.pgn.NAG_SPECULATIVE_MOVE}              # !? from classify
    assert main[2].eval() is not None and main[6].eval() is None        # none after mate
    assert "Coach: The queen comes out early. Better was: Nf3 develops (fast)." in main[2].comment
    assert "Allowed a fork." in main[2].comment
    # the better line, from the position before the error
    var = main[1].variations[1]
    assert var.san() == "Nf3" and [n.san() for n in var.mainline()] == ["Nc6", "Bb5"]
    assert "Coach: Stockfish +0.35" in var.comment and var.eval().white().score() == 35
    # opening named once, on the move where the game settled into it
    assert "King's Pawn Game: open" in main[1].comment and "B00" not in main[0].comment


def test_plain_comment():
    assert plain_comment("**Assessment**: Mistake\n**What happened**: A **bad** move.\n"
                         "**What now**: Defend.") == "A bad move. What now: Defend."


def test_variation_eval_is_the_position_after_its_first_move():
    """Review feedback: the score before the move was put on the variation's
    first move. Centipawns are the line's value either way; a mate is one
    move nearer after the mating side's move."""
    from chesscoach.pgn_export import _after_first
    assert _after_first(None, 35, True) == (None, 35)
    assert _after_first(3, None, True) == (2, None)          # White mates in 3, White moves
    assert _after_first(-3, None, False) == (-2, None)       # Black mates in 3, Black moves
    assert _after_first(-3, None, True) == (-3, None)        # White moves, still mated in 3
    assert _after_first(1, None, True) == (None, None)       # the move is the mate


def test_the_source_games_notes_are_kept_and_coach_notes_marked():
    import chess.pgn
    src = chess.pgn.read_game(io.StringIO(
        '[White "Me"]\n[Black "You"]\n[Result "1-0"]\n\n'
        '1. e4 {My favourite} e5 2. Qh5 (2. Nf3 {main line}) 2... Nc6 3. Bc4 Nf6 4. Qxf7# 1-0'))
    r = _review()
    text = annotated_pgn(r, src)
    g = chess.pgn.read_game(io.StringIO(text))
    main = list(g.mainline())
    assert main[0].comment.startswith("My favourite")                   # the annotator's note stays
    assert "Coach: The queen comes out early." in main[2].comment       # the coach's, marked
    assert [v.san() for v in main[1].variations] == ["Qh5", "Nf3"]      # the source's 2.Nf3
    assert main[1].variations[1].comment == "main line"                 # not doubled by the coach's
    assert g.comment.startswith("Coach summary:")
    # exporting the exported game again does not stack the coach's notes
    again = chess.pgn.read_game(io.StringIO(annotated_pgn(r, g)))
    assert list(again.mainline())[2].comment.count("Coach:") == 1
    assert again.comment.count("Coach summary:") == 1
