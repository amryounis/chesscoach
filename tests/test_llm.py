"""An online model (OpenAI-compatible: Gemini, OpenAI, Groq...), against a local
stand-in for the service: the requests, the answers, the errors, one request
per review, and no names sent."""
import http.server
import io
import json
import re
import threading

import chess
import chess.engine
import chess.pgn
import pytest

from chesscoach import coach, config, llm, narrator
from chesscoach.review import review_game

TRAP = ("[White \"Alice Secretname\"]\n[Black \"Bob Hiddenname\"]\n[Event \"Club Night Cairo\"]\n\n"
        "1. e4 e5 2. Nf3 Nc6 3. Bc4 Nd4 4. Nxe5 Qg5 5. Nxf7 Qxg2 6. Rf1 Qxe4+ 7. Be2 Nf3# 0-1")


class FakeService:
    """A local OpenAI-compatible service. `answer(body) -> (status, json)`."""

    def __init__(self):
        self.requests = []                 # (path, headers, body)
        self.answer = lambda body: (200, reply("**Assessment**: Good\n**What happened**: Fine."))
        svc = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, data):
                raw = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                svc.requests.append((self.path, dict(self.headers), None))
                self._send(200, {"data": [{"id": "models/gemini-flash-test"},
                                          {"id": "gemini-flash-lite-test"}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                svc.requests.append((self.path, dict(self.headers), body))
                status, data = svc.answer(body)
                self._send(status, data)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def posts(self):
        return [b for p, _, b in self.requests if b is not None]


def reply(text, tool_calls=None):
    msg = {"role": "assistant", "content": text}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}}


@pytest.fixture
def service(stockfish, monkeypatch, tmp_path):
    config.settings = config.load_settings(stockfish_path=stockfish, maia_cmd="no-such-maia",
                                           depth=8)
    monkeypatch.setattr(config, "user_config_path", lambda: tmp_path / "config.toml")
    s = FakeService()
    c = config.settings
    c.coach_llm, c.online_service, c.online_url = "online", "gemini", s.url
    c.online_model, c.online_key = "gemini-flash-test", "k-123"
    c.player_name, c.lichess_user = "Homerson", "youamer"
    monkeypatch.setattr(narrator.ollama, "chat", lambda *a, **k: pytest.fail("Ollama was asked"))
    llm._private.clear()
    yield s
    s.server.shutdown()


def test_the_settings_choose_the_model_and_say_what_is_missing(service):
    assert llm.active_model() == "online:gemini-flash-test"
    assert llm.label(llm.active_model()) == "Google Gemini: gemini-flash-test"
    config.settings.online_key = ""
    with pytest.raises(config.ConfigError, match="key is missing.*aistudio.google.com"):
        llm.check(llm.active_model())
    config.settings.coach_llm = "ollama"
    assert llm.active_model() == "qwen3:14b" and not llm.is_online(llm.active_model())


def test_one_call_in_ollamas_shape(service):
    resp = llm.chat("online:gemini-flash-test", [{"role": "system", "content": "Be brief."},
                                                 {"role": "user", "content": "Hi, I am Homerson."}])
    assert resp["message"]["content"].startswith("**Assessment**") and resp["eval_count"] == 20
    path, headers, body = service.requests[-1]
    assert path == "/v1/chat/completions"
    assert headers["Authorization"] == "Bearer k-123"
    assert body["model"] == "gemini-flash-test" and body["max_tokens"] == llm.MIN_TOKENS
    assert body["messages"][1]["content"] == "Hi, I am the player."      # the user's own name


def test_tool_calls_go_both_ways(service):
    service.answer = lambda b: (200, reply("", [{"id": "c1", "type": "function", "function": {
        "name": "best_moves", "arguments": json.dumps({"n": 2})}}]))
    resp = llm.chat("online:gemini-flash-test", [{"role": "user", "content": "?"}],
                    tools=[{"type": "function", "function": {"name": "best_moves"}}])
    assert coach._tool_calls(resp) == [("best_moves", {"n": 2})]
    # the coach's next turn, in Ollama's shape, reaches the service in OpenAI's
    llm.chat("online:gemini-flash-test", [
        {"role": "user", "content": "?"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "best_moves", "arguments": {"n": 2}}}]},
        {"role": "tool", "content": "1. e4", "tool_name": "best_moves"}])
    msgs = service.posts()[-1]["messages"]
    cid = msgs[1]["tool_calls"][0]["id"]
    assert msgs[1]["tool_calls"][0]["function"]["arguments"] == '{"n": 2}'
    assert msgs[2] == {"role": "tool", "tool_call_id": cid, "content": "1. e4"}


@pytest.mark.parametrize("status,kind,words", [
    (429, "quota", "midnight, US Pacific time"), (401, "key", "refused the key"),
    (404, "model", "doesn't know that model"), (500, "bad", "error (500): boom")])
def test_errors_in_plain_words(service, status, kind, words):
    service.answer = lambda b: (status, [{"error": {"message": "boom"}}])
    with pytest.raises(llm.LLMError) as e:
        llm.chat("online:gemini-flash-test", [{"role": "user", "content": "?"}])
    assert e.value.kind == kind and words in str(e.value)
    text, err = narrator.call_llm("online:gemini-flash-test", [{"role": "user", "content": "?"}])
    assert text is None and words in err and "ollama" not in err


def test_unreachable_service(service):
    config.settings.online_url = "http://127.0.0.1:9/v1"
    with pytest.raises(llm.LLMError, match="can't be reached") as e:
        llm.chat("online:gemini-flash-test", [{"role": "user", "content": "?"}])
    assert e.value.kind == "offline"


def test_list_models(service):
    assert llm.list_models("custom", service.url, "k") == ["gemini-flash-lite-test",
                                                            "gemini-flash-test"]
    assert service.requests[-1][0] == "/v1/models"
    with pytest.raises(llm.LLMError, match="key first"):
        llm.list_models("gemini", "", "")


def test_a_key_from_the_environment_stays_out_of_the_file(service, monkeypatch, tmp_path):
    monkeypatch.setenv("CHESSCOACH_ONLINE_KEY", "secret-from-env")
    s = config.load_settings(config_file=None)
    assert s.online_key == "secret-from-env"
    path = config.save_settings(s, tmp_path / "c.toml")
    text = path.read_text()
    assert "secret-from-env" not in text and 'online_key = ""' in text
    s.online_key = "typed-in"
    assert 'online_key = "typed-in"' in config.save_settings(s, tmp_path / "c.toml").read_text()


def _sections(body):
    return [int(k) for k in re.findall(r"^### MOVE (\d+)\s*$", body["messages"][-1]["content"], re.M)]


def test_a_review_asks_once_and_never_sends_names(service):
    """All comments in one request; the checks still apply to each; one more
    request rewrites what they caught."""
    def answer(body):
        if len(body["messages"]) == 2:                       # the comments
            ks = _sections(body)
            parts = []
            for k in ks:
                extra = " The white queen on a8 attacks everything." if k == 1 else ""
                parts.append(f"### MOVE {k}\n**Assessment**: x\n**What happened**: A reason "
                             f"for move {k}.{extra}")
            return 200, reply("\n\n".join(parts))
        # the rewrites: only what was caught
        ks = re.findall(r"^### MOVE (\d+)", body["messages"][-1]["content"], re.M)
        return 200, reply("\n\n".join(f"### MOVE {k}\n**Assessment**: x\n**What happened**: "
                                      f"A reason for move {k}, rewritten." for k in ks))
    service.answer = answer
    game = chess.pgn.read_game(io.StringIO(TRAP))
    stages = []
    r = review_game(game, "both", progress=lambda st, d, t, m, i=None: stages.append(m))
    posts = service.posts()
    assert len(posts) == 2                                     # comments + one rewrite
    first = posts[0]
    n = len(_sections(first))
    moments = [m for m in r.moves if m.reason]
    assert n == len(moments) >= 2
    assert first["max_tokens"] == min(llm.MAX_TOKENS, config.settings.num_predict * n + 200)
    m1 = moments[0]
    text = m1.annotations["online:gemini-flash-test"]
    assert "rewritten" in text and "a8" not in text            # the false claim is gone
    assert all("A reason for move" in m.annotations["online:gemini-flash-test"] for m in moments)
    assert any("in one request to Google Gemini: gemini-flash-test" in s for s in stages)
    sent = json.dumps(posts)
    for name in ("Alice", "Secretname", "Bob", "Hiddenname", "Club Night", "Homerson", "youamer"):
        assert name not in sent, name


def test_a_left_out_move_is_an_error_not_a_comment(service):
    service.answer = lambda b: (200, reply("### MOVE 1\n**Assessment**: x\n**What happened**: "
                                           "Only the first."))
    r = review_game(chess.pgn.read_game(io.StringIO(TRAP)), "both")
    moments = [m for m in r.moves if m.reason]
    assert "Only the first." in moments[0].annotations["online:gemini-flash-test"]
    assert all("left this move out" in m.errors["online:gemini-flash-test"] for m in moments[1:])
    assert not any(m.annotations for m in moments[1:])


def test_quota_used_up_mid_review_keeps_the_engine_work(service):
    service.answer = lambda b: (429, {"error": {"message": "Resource exhausted"}})
    r = review_game(chess.pgn.read_game(io.StringIO(TRAP)), "both")
    moments = [m for m in r.moves if m.reason]
    assert moments and all("quota is used up" in m.errors["online:gemini-flash-test"]
                           for m in moments)
    assert r.accuracy["white"] is not None and all(m.mark for m in r.moves)
    assert len(service.posts()) == 1                           # asked once, not per comment


def test_one_request_per_comment_when_switched_off(service):
    config.settings.online_one_request = False
    r = review_game(chess.pgn.read_game(io.StringIO(TRAP)), "both")
    moments = [m for m in r.moves if m.reason]
    # one request per comment, plus a rewrite for each the checks flag (as with Ollama)
    assert len(moments) <= len(service.posts()) <= 2 * len(moments)
    assert all(len(_sections(b)) == 0 for b in service.posts())
    assert all("Fine." in m.annotations["online:gemini-flash-test"] for m in moments)


def test_the_coach_answers_with_an_online_model(service):
    service.answer = lambda b: (200, reply("White is to move and has a plan."))
    with chess.engine.SimpleEngine.popen_uci(config.settings.stockfish_path) as e:
        a = coach.explain(chess.Board(), "online:gemini-flash-test", e, "")
    assert not a.error and a.text and service.posts()
    service.answer = lambda b: (429, {"error": {"message": "x"}})
    with chess.engine.SimpleEngine.popen_uci(config.settings.stockfish_path) as e:
        a = coach.explain(chess.Board(), "online:gemini-flash-test", e, "")
    assert "quota is used up" in a.error and "ollama" not in a.error


def test_the_config_command_never_prints_a_key(monkeypatch, capsys):
    from chesscoach import cli
    monkeypatch.setenv("CHESSCOACH_ONLINE_KEY", "sekrit-123")
    monkeypatch.setenv("CHESSCOACH_LICHESS_TOKEN", "lip-999")
    cli.main(["config"])
    out = capsys.readouterr().out
    assert "sekrit" not in out and "lip-999" not in out and "(set, 10 characters)" in out


def test_an_answer_cut_off_by_thinking_is_said_plainly(service):
    service.answer = lambda b: (200, {"choices": [{"message": {"role": "assistant", "content": None},
                                                   "finish_reason": "length"}]})
    with pytest.raises(llm.LLMError, match="ran out of room.*Flash-Lite"):
        llm.chat("online:gemini-flash-test", [{"role": "user", "content": "?"}])



@pytest.mark.parametrize("raw,want", [
    # what Gemini Flash-Lite wrote in a real review (0.27.0)
    ('The first sentence is already written: "White captures Black\'s bishop on d1 with the king '
     'from c1." Recapturing with the king costs material.',
     "White captures Black's bishop on d1 with the king from c1. Recapturing with the king costs "
     "material."),
    ('The first sentence is already written: "White moves the bishop from c1 to e3." Do NOT repeat '
     'it. White develops the bishop.', "White moves the bishop from c1 to e3. White develops the bishop."),
    ('The first sentence is already written: "X moves." Do NOT repeat it. Write only 1-2 sentences '
     'explaining WHY the move earned this label, using WHAT THE MOVE DID. The pawn is weak.',
     "X moves. The pawn is weak."),
    ('The first sentence of the answer is already written: "Nf3 is best." Do not repeat it and do '
     'not contradict it; explain WHY, from the results.\nIt develops.', "Nf3 is best.\nIt develops."),
    ('A comment with "quotes" stays as it is.', 'A comment with "quotes" stays as it is.'),
])
def test_an_instruction_copied_from_the_template_is_taken_out(raw, want):
    assert narrator.strip_echo(raw) == want


def test_a_review_whose_model_copies_the_template_reads_cleanly(service):
    def answer(body):
        ks = _sections(body) or [int(k) for k in re.findall(r"^### MOVE (\d+)",
                                                             body["messages"][-1]["content"], re.M)]
        prompts = body["messages"][-1]["content"]
        fixed = re.findall(r'already written: "([^"]*)"', prompts)
        return 200, reply("\n\n".join(
            f'### MOVE {k}\n**Assessment**: x\n**What happened**: The first sentence is already '
            f'written: "{fixed[k - 1]}" Do NOT repeat it. A reason for move {k}.' for k in ks))
    service.answer = answer
    r = review_game(chess.pgn.read_game(io.StringIO(TRAP)), "both")
    moments = [m for m in r.moves if m.reason]
    for m in moments:
        text = m.annotations["online:gemini-flash-test"]
        assert "already written" not in text and "repeat" not in text, text
        happened = text.split("**What happened**: ")[1].split("\n")[0]
        assert happened.count(m.san) <= 2 and "A reason for move" in happened


def test_what_now_is_written_by_code():
    # qwen (0.27.3): "c4, aiming to recapture on a4" — a purpose that wasn't true
    b = chess.Board("r4r2/1ppqppbk/1n4pp/p3P3/P2PNP2/1B2B2P/1PPQ2P1/3K3R b - - 0 17")
    assert narrator.what_now(b, ["Nxa4", "c4", "f5"]) == (
        "Black will capture White's pawn on a4 with the knight from b6, and White should "
        "respond by moving the pawn from c2 to c4.")
    assert narrator.what_now(chess.Board(), ["e4"]) == "White will move the pawn from e2 to e4."
    mate = chess.Board("rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3")
    assert narrator.what_now(mate, []) == "The game is over."
    prep = {"fixed": "White moves the pawn from h2 to h3.", "label": "Mistake",
            "what_now": "Black will capture White's pawn on a4 with the knight from b6."}
    text = ("**Assessment**: Mistake\n**What happened**: White moves the pawn from h2 to h3. "
            "It ignores the threat.\n**What now**: White plays c4, aiming to recapture on a4.")
    out = narrator.tidy_annotation(narrator.trim(text, prep, None), prep)
    assert "aiming to recapture" not in out
    assert out.endswith("**What now**: Black will capture White's pawn on a4 with the knight from b6.")


def test_a_local_model_keeps_one_sentence_of_its_own():
    prep = {"fixed": "Black moves the rook from a8 to d8.", "label": "Inaccuracy"}
    text = ("**Assessment**: Inaccuracy\n**What happened**: Black moves the rook from a8 to d8. "
            "The rook no longer defends a5. While the move adds a shield pawn, it fails.\n"
            "It allows White to equalize.\n**Better was**: Moving the pawn from f7 to f5.")
    one = narrator.trim(text, prep, 1)
    assert "**What happened**: Black moves the rook from a8 to d8. The rook no longer defends a5." in one
    assert "shield pawn" not in one and "equalize" not in one and "f7 to f5" in one
    assert "shield pawn" in narrator.trim(text, prep, None)          # online: all of it
    config.settings.local_sentences = 1
    assert narrator._keep("qwen3:14b") == 1 and narrator._keep("online:gemini-x") is None
    config.settings.local_sentences = 0
    assert narrator._keep("qwen3:14b") is None
