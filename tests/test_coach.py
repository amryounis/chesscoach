"""Phase 4: Explain this position and Ask the coach, with a stubbed model."""
import chess
import pytest

from chesscoach import coach, config

pytestmark = pytest.mark.engine

ROOK = "2b1k2r/1r4pp/1pp5/p7/2P2B2/3B1N2/1P1K1PPP/R6R w k - 1 18"


@pytest.fixture
def sf(stockfish):
    config.settings = config.load_settings(stockfish_path=stockfish)
    e = chess.engine.SimpleEngine.popen_uci(stockfish)
    yield e
    e.quit()


class FakeOllama:
    """Replays scripted replies; records what the model was sent."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def chat(self, model, messages, tools=None, **kw):
        self.sent.append({"messages": [dict(m) for m in messages], "tools": tools})
        r = self.replies.pop(0)
        if isinstance(r, list):                      # a tool call round
            return {"message": {"content": "", "tool_calls": [
                {"function": {"name": n, "arguments": a}} for n, a in r]}}
        return {"message": {"content": r}}


@pytest.fixture
def fake(monkeypatch):
    def install(replies):
        f = FakeOllama(replies)
        import ollama
        monkeypatch.setattr(ollama, "chat", f.chat)
        return f
    return install


def test_tools_return_verified_facts(sf):
    t = coach.Tools(chess.Board(ROOK), sf)
    assert "White is winning" in t.call("evaluate", {})
    assert "6 legal moves" in t.call("legal_moves", {"moves": ["Rhe1+"]})
    out = t.call("play_line", {"moves": ["Rhe1+", "O-O"]})
    assert "'O-O' is not legal for Black" in out
    out = t.call("compare_moves", {"move_a": "Rhe1+", "move_b": "Rae1+"})
    assert "practically equal" in out
    assert "not a legal move" in t.call("compare_moves", {"move_a": "Rxh7", "move_b": "Rae1+"})
    assert t.call("nonsense", {}) == "Unknown tool 'nonsense'."
    assert len(t.boards) > 5                      # every position looked at is kept


def test_ask_uses_tools_then_answers_and_is_checked(sf, fake):
    f = fake([
        [("position_facts", {}), ("threats", {})],
        "The rook on e1 attacks the bishop on c8. The bishop on c8 has no defender.",
        "The rook on e1 attacks the bishop on c8. The bishop on c8 has no defender.",
    ])
    a = coach.ask(chess.Board(ROOK), "Is Black's bishop on c8 safe?", "m", sf,
                  mode="agent")
    assert not a.error
    assert [n for n, _ in a.tool_calls] == ["position_facts", "threats"]
    # The model saw tool results, never a FEN.
    sent = " ".join(m.get("content", "") for m in f.sent[1]["messages"])
    assert "Undefended Black pieces" in sent and ROOK.split()[0] not in sent
    # The false attack claim was sent back once, then removed.
    assert "does not attack the bishop on c8" in f.sent[2]["messages"][-1]["content"]
    assert a.text == "The bishop on c8 has no defender."
    assert len(a.removed) == 1


def test_ask_stops_calling_tools_at_the_limit(sf, fake):
    # The model keeps asking for tools; after two it gets none and must answer.
    f = fake([[("evaluate", {})], [("evaluate", {}), ("best_moves", {})],
              "White is winning."])
    a = coach.ask(chess.Board(ROOK), "Who is better?", "m", sf, max_calls=2,
                  mode="agent")
    assert [n for n, _ in a.tool_calls] == ["evaluate", "evaluate"]
    assert f.sent[-1]["tools"] is None            # no tools offered past the limit
    assert a.text == "White is winning."


def test_explain_writes_the_verdict_and_checks_the_rest(sf, fake):
    fake(["**The position**: Black's king is still in the centre and can castle.\n"
          "**Threats**: Material is equal.\n"
          "**Plan**: White gives check with a rook on e1.",
          "**The position**: Black's king is still in the centre and can castle.\n"
          "**Threats**: Material is equal.\n"
          "**Plan**: White gives check with a rook on e1."])
    a = coach.explain(chess.Board(ROOK), "m", sf)
    assert a.text.startswith("**The position**: White is to move, and White is winning. "
                             "White is up a bishop and a knight.")
    assert "Material is equal" not in a.text
    assert "can castle" in a.text
    assert any("Black can still castle" in k for k in a.facts)


def test_explain_prompt_has_key_facts_and_best_moves(sf):
    p, boards, fixed, key = coach.explain_prompt(chess.Board(ROOK), sf)
    assert "KEY FACTS" in p and "THE ENGINE'S BEST MOVES FOR WHITE" in p
    assert "practically as good as the first" in p
    assert ROOK.split()[0] not in p                # no FEN for the model to misread
    assert len(boards) > 3


def test_board_from_moves():
    b, history = coach.board_from(pgn_moves="1. e4 e5 2. Nf3")
    assert b.fen().startswith("rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2")
    assert history == "1. e4 e5 2. Nf3"


def test_ask_re_answers_with_corrections_and_tidies(sf, fake):
    # The model claims a fork; the check sends the correction and asks for
    # the answer again (not a 'rewrite', which it read as dialogue).
    f = fake([
        [("play_line", {"moves": ["Rae1+", "Kd8", "Ne5", "Rf8", "Nxc6+"]})],
        "Yes, White's knight on c6 forks Black's rook on b7 and the pawn on a5.",
        "Yes, White's knight on c6 forks Black's rook on b7 and the pawn on a5. "
        "However, the knight gives check.",
    ])
    a = coach.ask(chess.Board(ROOK), "After Rae1+ Kd8 Ne5 Rf8 Nxc6+, does the knight "
                  "fork the rook on b7?", "m", sf, mode="agent")
    last = f.sent[-1]["messages"][-1]["content"]
    assert "Now answer the question again" in last and "no fork" in last
    assert a.text == "The knight gives check."          # 'However,' dropped


def test_ask_tools_explain_ambiguous_moves(sf):
    t = coach.Tools(chess.Board(ROOK), sf)
    out = t.call("compare_moves", {"move_a": "Re1+", "move_b": "Rae1+"})
    assert "could mean more than one move" in out and "Rhe1+" in out


# --- The router: code reads the question and gathers the facts -------------

TEST9 = "r1bq1rk1/pp2bppp/2n1pn2/3pP3/3p4/2N1BN2/PPP1BPPP/R2Q1RK1 w - - 0 9"
HOMER8 = "rnbqk2r/pp2pp2/2p2bpp/3p3P/2PP4/2N2N2/PP2PPP1/R2QKB1R b KQkq - 0 8"


def _sans(board, r):
    return sorted(board.san(m) for m in r.moves)


def test_route_reads_each_kind_of_question():
    b = chess.Board(ROOK)
    r = coach.route_question(b, "Is Black's bishop on c8 safe?")
    assert r.kind == "safety" and r.squares == [chess.C8]
    r = coach.route_question(b, "Which rook should White give check with on e1?")
    assert r.kind == "compare" and _sans(b, r) == ["Rae1+", "Rhe1+"] and not r.squares
    r = coach.route_question(b, "After Rae1+ Kd8 Ne5 Rf8 Nxc6+, does White's knight "
                                "fork Black's rook on b7?")
    assert r.kind == "line" and r.line == ["Rae1+", "Kd8", "Ne5", "Rf8", "Nxc6+"]
    assert r.squares == [chess.B7]
    r = coach.route_question(b, "Why not Re1+?")          # ambiguous: both rooks
    assert r.kind == "compare" and _sans(b, r) == ["Rae1+", "Rhe1+"]
    t = chess.Board(TEST9)
    r = coach.route_question(t, "Should White take the pawn on d4 or the knight on f6?")
    assert r.kind == "compare" and _sans(t, r) == ["Bxd4", "Nxd4", "Qxd4", "exf6"]
    r = coach.route_question(t, "Why not Nxd4?")
    assert r.kind == "move" and _sans(t, r) == ["Nxd4"]
    h = chess.Board(HOMER8)
    r = coach.route_question(h, "Why is ...g5 better than ...Bg4 here?")
    assert _sans(h, r) == ["Bg4", "g5"]
    assert coach.route_question(t, "What should White do?").kind == "general"
    r = coach.route_question(t, "Why not ...Nxe5?")       # a Black move, White to move
    assert r.notes and "White to move" in r.notes[0]


def test_square_and_attack_facts():
    b = chess.Board(ROOK)
    assert coach.square_facts(b, chess.C8) == (
        "The Black bishop on c8 is not attacked by any White piece right now. "
        "It has no defender.")
    r = coach.play_line(b, ["Rae1+", "Kd8", "Ne5", "Rf8", "Nxc6+"], effects=False)
    text, all_hit, hit = coach.attack_facts(r.board, chess.C6, [chess.B7])
    assert not all_hit and "does NOT attack the rook on b7" in text
    assert "the Black king on d8" in text


def test_router_writes_the_verdict_and_the_model_explains(sf, fake):
    f = fake(["Both rooks give the same check, and Black's best reply is the same."])
    a = coach.ask(chess.Board(ROOK), "Which rook should White give check with on e1?",
                  "m", sf)
    assert a.route == "compare" and not a.error
    assert a.text.startswith("Rhe1+ and Rae1+ are practically equal") or \
        a.text.startswith("Rae1+ and Rhe1+ are practically equal")
    assert a.text.endswith("Black's best reply is the same.")
    assert len(f.sent) == 1 and f.sent[0]["tools"] is None      # one call, no tools
    prompt = f.sent[0]["messages"][-1]["content"]
    assert "THE MOVES ASKED ABOUT" in prompt and ROOK.split()[0] not in prompt


def test_router_answers_a_line_question_and_removes_a_false_fork(sf, fake):
    f = fake(["The knight on c6 forks the rook on b7 and the king.",
              "No: after Rae1+ Kd8 Ne5 Rf8 Nxc6+, the knight on c6 does not attack the "
              "rook on b7. The knight on c6 forks the rook on b7 and the king."])
    a = coach.ask(chess.Board(ROOK), "After Rae1+ Kd8 Ne5 Rf8 Nxc6+, does White's knight "
                  "fork Black's rook on b7?", "m", sf)
    assert a.route == "line"
    assert a.text.startswith("No: after Rae1+ Kd8 Ne5 Rf8 Nxc6+, the knight on c6 "
                             "does not attack the rook on b7.")
    assert "forks" not in a.text and a.removed            # checked against the line
    assert a.text.count("No: after") == 1                 # restated first sentence dropped
    assert "Now answer the question again" in f.sent[1]["messages"][-1]["content"]


def test_router_compares_captures_on_named_squares(sf, fake):
    fake(["Taking the knight wins a piece."])
    a = coach.ask(chess.Board(TEST9), "Should White take the pawn on d4 or the knight "
                  "on f6?", "m", sf)
    assert a.fixed.startswith("exf6 is clearly better")
    assert "Nxd4" in a.fixed and "Bxd4" in a.fixed
    assert any("KEY FACTS" in s and "Nxd4: The knight no longer defends the White pawn "
               "on e5" in s for s in a.facts)
