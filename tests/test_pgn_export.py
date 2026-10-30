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
    assert "The queen comes out early. Better was: Nf3 develops (fast)." in main[2].comment
    assert "Allowed a fork." in main[2].comment
    # the better line, from the position before the error
    var = main[1].variations[1]
    assert var.san() == "Nf3" and [n.san() for n in var.mainline()] == ["Nc6", "Bb5"]
    assert "Stockfish +0.35" in var.comment and var.eval().white().score() == 35
    # opening named once, on the move where the game settled into it
    assert "King's Pawn Game: open" in main[1].comment and "B00" not in main[0].comment


def test_plain_comment():
    assert plain_comment("**Assessment**: Mistake\n**What happened**: A **bad** move.\n"
                         "**What now**: Defend.") == "A bad move. What now: Defend."
