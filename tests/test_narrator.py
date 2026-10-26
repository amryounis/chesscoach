import chess

from chesscoach import narrator


def test_check_output_flags_rule_breaks():
    b = chess.Board()
    issues = narrator.check_output(
        "**Assessment**: Good\nThis is the only way. The knight on e4 and Nf3 help.",
        ["only way"], [b], allowed_san="e4", label="Blunder")
    text = " | ".join(issues)
    assert "wrong label" in text
    assert "banned word used: 'only way'" in text
    assert "knight on e4" in text
    assert "'Nf3'" in text


def test_allowed_san_is_not_flagged():
    assert narrator.check_output("After Nxd4 the pawn hangs.", [], [chess.Board()],
                                 allowed_san="Nxd4") == []


def test_fixed_sentence_inserted_once():
    text = "**Assessment**: Excellent\n**What happened**: Because."
    out = narrator.insert_fixed_sentence(text, "Black moves the pawn from c7 to c6.")
    assert "**What happened**: Black moves the pawn from c7 to c6. Because." in out
    assert narrator.insert_fixed_sentence(out, "Black moves the pawn from c7 to c6.") == out


def test_narrate_retries_once_and_keeps_the_better_text(monkeypatch):
    replies = iter(["**Assessment**: Good\nThe only way.",
                    "**Assessment**: Good\nA sound move."])
    calls = []

    def fake_chat(model, messages, options=None, **kw):
        calls.append(len(messages))
        return {"message": {"content": next(replies)}}

    monkeypatch.setattr(narrator.ollama, "chat", fake_chat)
    prep = {"prompt": "p", "banned": ["only way"], "boards": [chess.Board()],
            "san": "e4", "label": "Good", "fixed": "White moves the pawn from e2 to e4."}
    r = narrator.narrate("any-model", prep)
    assert r["first_issues"] == 1 and r["issues"] == []
    assert len(calls) == 2 and calls[1] > calls[0]       # retry carries the history
    assert "A sound move." in r["text"]


def test_false_claim_gets_a_retry_then_its_sentence_is_removed(monkeypatch):
    # The baseline's 12...Kxf7 error, twice in a row: removed, with the
    # correction passed to the retry and recorded.
    from chesscoach import config
    config.settings.grounding = True
    b = chess.Board("rn1qk2r/p3pP2/2p2b1p/8/2pP2b1/2N1PN2/PP3PP1/R2QK2R b KQkq - 0 12")
    mv = b.parse_san("Kxf7")
    a = b.copy()
    a.push(mv)
    bad = ("**Assessment**: Inaccuracy\n**What happened**: The move removes its "
           "protection from the pawn on e7. It gives up the bishop pair.")
    prompts = []

    def fake_chat(model, messages, options=None, **kw):
        prompts.append(messages[-1]["content"])
        return {"message": {"content": bad}}

    monkeypatch.setattr(narrator.ollama, "chat", fake_chat)
    fixed = "Black captures White's pawn on f7 with the king from e8."
    prep = {"prompt": "p", "banned": [], "boards": [b, a], "san": "Kxf7",
            "label": "Inaccuracy", "fixed": fixed, "move": mv,
            "better_fixed": "The engine preferred this: Black moves the king from e8 to f8."}
    out = narrator.narrate("m", prep)
    assert len(prompts) == 2
    assert "the king on f7 still defends the pawn on e7" in prompts[1]
    assert "pawn on e7" not in out["text"]
    assert out["text"] == ("**Assessment**: Inaccuracy\n\n**What happened**: "
                           + fixed + " It gives up the bishop pair.\n\n**Better was**: "
                           "The engine preferred this: Black moves the king from e8 to f8.")
    assert len(out["removed"]) == 1
    assert any(i.startswith("sentence removed") for i in out["issues"])


def test_tidy_restores_the_shape_after_a_removal():
    # The 12...Kxf7 comment from the 5 October review: the rewrite lost the
    # 'What happened' heading and the check removed 'Better was'.
    broken = ("**Assessment**: Inaccuracy  \n"
              "Black's move allows White a positional advantage.\n\n\n"
              "**What now**: White will move the knight to e5, giving check.")
    prep = {"label": "Inaccuracy",
            "fixed": "Black captures White's pawn on f7 with the king from e8.",
            "better_fixed": "The engine preferred this: Black moves the king from e8 to f8."}
    assert narrator.tidy_annotation(broken, prep) == (
        "**Assessment**: Inaccuracy\n\n"
        "**What happened**: Black captures White's pawn on f7 with the king from e8. "
        "Black's move allows White a positional advantage.\n\n"
        "**Better was**: The engine preferred this: Black moves the king from e8 to f8.\n\n"
        "**What now**: White will move the knight to e5, giving check.")


def test_tidy_uses_the_code_label_and_keeps_good_moves_short():
    text = "**Assessment**: Good\n**What happened**: Fine.\n**What now**: White pushes."
    prep = {"label": "Excellent", "fixed": "White moves the pawn from e2 to e4.",
            "better_fixed": ""}
    out = narrator.tidy_annotation(text, prep)
    assert out.startswith("**Assessment**: Excellent")
    assert "Better was" not in out
    assert "**What happened**: White moves the pawn from e2 to e4. Fine." in out
