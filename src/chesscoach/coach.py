"""
Phase 4: the coach on any position, not only on critical moments.

  explain()   "Explain this position": one prompt built from verified
              facts (static facts, engine evaluation and best moves in
              words, real threats, key facts), one model call, grounding.
              No tool calls, so it is fast and predictable.

  ask(q)      "Ask the coach": a tool-calling agent. The model never sees
              the board or a FEN; everything it may say about the position
              comes back from tools that return verified facts in plain
              English. Its answer then goes through the same grounding
              check as the review comments, with one rewrite and sentence
              removal for what still fails.

Tools work on the position on the board, optionally after a list of moves
from it (`moves`, in SAN, as the player would type them). An illegal move
comes back as an error naming the legal moves, never as a guess.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import chess
import chess.engine

from . import config
from .assessment import eval_phrase, win_pct
from .concepts import _pv_captures, equal_alternative, threat
from .describe import PIECE_NAMES, describe_move, describe_move_effects, side_name
from .engine import EngineAnalysis, run_stockfish
from .facts import (castling_lines, join_words, legal_move_line, loose_piece_lines,
                    material_lines, play_line, position_facts, resolve_move)
from .grounding import correction_text, ground, remove_failed

MAX_TOOL_CALLS = 6


# --- Engine helpers -------------------------------------------------------

def _analyse(engine, board: chess.Board, multipv: int = 3) -> EngineAnalysis:
    try:
        engine.configure({"Clear Hash": None})
    except Exception:
        pass
    return run_stockfish(chess.Board(board.fen()), engine=engine, multipv=multipv)


def _verdict(ea: EngineAnalysis, board: chess.Board) -> str:
    if board.is_checkmate():
        return f"{side_name(board.turn)} is checkmated"
    if board.is_stalemate():
        return "it is stalemate"
    return eval_phrase(ea.score_cp, ea.score_mate)[0]


def _chance(cp, mate, color) -> str:
    w = win_pct(cp, mate, color == chess.WHITE)
    return "unknown" if w is None else f"{w:.0f}%"


def _line_boards(board: chess.Board, line: list, plies: int = 4) -> list:
    out, b = [], board.copy(stack=False)
    for san in line[:plies]:
        try:
            b.push_san(san)
        except ValueError:
            break
        out.append(b.copy(stack=False))
    return out


# --- The fact tools -------------------------------------------------------

class Tools:
    """The agent's only window on the board. Every result is plain English
    and verified; every position a tool looks at is remembered, so the
    grounding check can verify the answer against the same positions."""

    SPECS = [
        ("position_facts", "Verified facts about the position: material named piece by "
         "piece, castling rights, attacked and undefended pieces, pins, mate threats, "
         "pawn structure, king safety.", False),
        ("evaluate", "Who is better and by how much, in words, with each side's winning "
         "chance, and the engine's best move in words.", False),
        ("best_moves", "The engine's three best moves for the side to move, each in words "
         "with its winning chance, and which of them are practically equal.", False),
        ("threats", "What each side really threatens: the opponent's best forcing move if "
         "they could move now, checked with the engine. Empty means no real threat.", False),
        ("legal_moves", "How many legal moves the side to move has, and the list.", False),
        ("play_line", "Play a sequence of moves from the position and describe each one: "
         "what it captures, what it attacks, which pieces it stops defending. Stops at the "
         "first illegal move and lists the legal ones.", True),
        ("compare_moves", "Compare two candidate moves for the side to move: winning chance "
         "after each, and how the position differs a few moves later with best play.", None),
    ]

    def __init__(self, board: chess.Board, engine):
        self.board = board.copy(stack=False)
        self.engine = engine
        self.boards = [self.board.copy(stack=False)]   # every position looked at
        self.calls = []                                # (name, args, result)

    # Ollama tool schemas.
    def schemas(self) -> list:
        out = []
        moves = {"type": "array", "items": {"type": "string"},
                 "description": "Optional: moves in SAN played first from the current "
                                "position, e.g. [\"Rae1+\", \"Kd8\"]. Omit for the "
                                "position as it is."}
        for name, desc, kind in self.SPECS:
            props, req = {}, []
            if kind is True:
                props["moves"] = dict(moves, description="Moves in SAN from the current "
                                      "position, in order.")
                req = ["moves"]
            elif kind is None:
                props = {"move_a": {"type": "string", "description": "First move, SAN"},
                         "move_b": {"type": "string", "description": "Second move, SAN"},
                         "moves": moves}
                req = ["move_a", "move_b"]
            else:
                props["moves"] = moves
            out.append({"type": "function", "function": {
                "name": name, "description": desc,
                "parameters": {"type": "object", "properties": props, "required": req}}})
        return out

    def call(self, name: str, args: dict) -> str:
        args = dict(args or {})
        moves = args.pop("moves", None) or []
        if isinstance(moves, str):
            moves = moves.replace(",", " ").split()
        try:
            fn = getattr(self, "t_" + name)
        except AttributeError:
            result = f"Unknown tool '{name}'."
        else:
            try:
                result = fn(moves, **args)
            except Exception as e:                       # never crash the agent loop
                result = f"Tool error: {e}"
        self.calls.append((name, {"moves": moves, **args}, result))
        return result

    def _at(self, moves: list):
        """The position after `moves`, or an error string."""
        if not moves:
            return self.board.copy(stack=False), ""
        r = play_line(self.board, moves, effects=False)
        self.boards += [r.board.copy(stack=False)]
        if r.error:
            return None, "Cannot reach that position: " + r.error
        return r.board.copy(stack=False), ""

    def _remember_line(self, board: chess.Board, line: list):
        self.boards += _line_boards(board, line)

    # The tools themselves.
    def t_position_facts(self, moves):
        b, err = self._at(moves)
        return err or position_facts(b)

    def t_evaluate(self, moves):
        b, err = self._at(moves)
        if err:
            return err
        ea = _analyse(self.engine, b, multipv=1)
        self._remember_line(b, ea.best_line)
        lines = [f"Verdict: {_verdict(ea, b)}.",
                 f"Winning chance: White {_chance(ea.score_cp, ea.score_mate, chess.WHITE)}, "
                 f"Black {_chance(ea.score_cp, ea.score_mate, chess.BLACK)}."]
        if ea.best_line:
            lines.append("Best move: " + describe_move(b, b.parse_san(ea.best_line[0])) + ".")
        return "\n".join(lines)

    def t_best_moves(self, moves):
        b, err = self._at(moves)
        if err:
            return err
        ea = _analyse(self.engine, b, multipv=3)
        out, top = [], None
        rows = [((ea.score_cp, ea.score_mate), ea.best_line)] + [
            (sc, line) for sc, (_, line) in zip(ea.alt_scores, ea.alt_lines)]
        for i, ((cp, mate), line) in enumerate(rows, 1):
            if not line:
                continue
            self._remember_line(b, line)
            w = win_pct(cp, mate, b.turn == chess.WHITE)
            top = w if top is None else top
            eq = (" — practically equal to the best" if i > 1 and w is not None
                  and top - w < 3 else "")
            mv = b.parse_san(line[0])
            out.append(f"{i}. {describe_move(b, mv)} (winning chance for "
                       f"{side_name(b.turn)} {w:.0f}%){eq}")
            out += [f"   - {e}" for e in describe_move_effects(b, mv)]
        return "\n".join(out) or "No legal moves."

    def t_threats(self, moves):
        b, err = self._at(moves)
        if err:
            return err
        out = []
        t = threat(self.engine, b)
        if t:
            out.append(t.text() + ".")
            nb = b.copy(stack=False)
            nb.push(chess.Move.null())
            self.boards.append(nb)
            self._remember_line(nb, [nb.san(t.move)])
        else:
            out.append(f"{side_name(not b.turn)} has no real threat right now "
                       f"(nothing that wins material or mates if {side_name(b.turn)} "
                       f"passed).")
        if b.is_check():
            out.append(f"{side_name(b.turn)} is in check and must answer it.")
        out += loose_piece_lines(b)
        return "\n".join(out)

    def t_legal_moves(self, moves):
        b, err = self._at(moves)
        return err or legal_move_line(b, with_list=True)

    def t_play_line(self, moves):
        if not moves:
            return "Give the moves to play, in SAN."
        r = play_line(self.board, moves)
        b = self.board.copy(stack=False)
        for s in r.steps:
            b.push_san(s.san)
            self.boards.append(b.copy(stack=False))
        text = r.text()
        if not r.error:
            ea = _analyse(self.engine, r.board, multipv=1)
            self._remember_line(r.board, ea.best_line)
            text += (f"\nAfter these moves: {_verdict(ea, r.board)}; "
                     f"{side_name(r.board.turn)} to move.\n"
                     + "\n".join(material_lines(r.board)[-1:]))
        return text

    def t_compare_moves(self, moves, move_a: str = "", move_b: str = ""):
        from .features import compare_lines
        b, err = self._at(moves)
        if err:
            return err
        ma, why_a = resolve_move(b, move_a)
        mb, why_b = resolve_move(b, move_b)
        if ma is None or mb is None:
            return " ".join(w for w in (why_a, why_b) if w)
        out = []
        evals = {}
        for label, mv in (("A", ma), ("B", mb)):
            a = b.copy(stack=False)
            a.push(mv)
            ea = _analyse(self.engine, a, multipv=1)
            evals[label] = (a, ea)
            self.boards.append(a.copy(stack=False))
            self._remember_line(a, ea.best_line)
            out.append(f"Move {label}: {describe_move(b, mv)}. Afterwards "
                       f"{_verdict(ea, a)}; winning chance for {side_name(b.turn)} "
                       f"{_chance(ea.score_cp, ea.score_mate, b.turn)}.")
        (_, ea_a), (_, ea_b) = evals["A"], evals["B"]
        wa = win_pct(ea_a.score_cp, ea_a.score_mate, b.turn == chess.WHITE)
        wb = win_pct(ea_b.score_cp, ea_b.score_mate, b.turn == chess.WHITE)
        if wa is not None and wb is not None and abs(wa - wb) < 3:
            out.append("The two moves are practically equal; neither is clearly better.")
        # The line comparison from the review, with B as 'the engine's choice'.
        before = EngineAnalysis(best_move=b.san(mb), best_line=[b.san(mb)] + ea_b.best_line)
        diffs = compare_lines(b, ma, before, ea_a)
        if diffs:
            text = "\n".join(diffs)
            text = (text.replace("ENGINE'S CHOICE", "MOVE B").replace("engine's choice", "move B")
                        .replace("PLAYER'S MOVE", "MOVE A").replace("player's move", "move A")
                        .replace("what the player gave up", "what move A gives up")
                        .replace("what the player gained", "what move A gains")
                        .replace("the player's move did not cause it",
                                 "move A did not cause it"))
            out.append("How the positions differ a few moves later:\n" + text)
        return "\n".join(out)


# --- Shared: call, check, rewrite, remove ---------------------------------

@dataclass
class Answer:
    text: str = ""
    removed: list = field(default_factory=list)
    caught: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)   # [(name, args)]
    secs: float = 0.0
    error: str = ""
    facts: list = field(default_factory=list)        # what the coach was given
    calls: list = field(default_factory=list)        # model calls: (secs, in, out)
    fixed: str = ""                                  # the code-written sentence(s)
    boards: list = field(default_factory=list)       # positions the answer may describe
    route: str = ""                                  # router: the kind of question
    candidates: list = field(default_factory=list)   # [(before, move, [after...])]


CALL_LOG: list = []      # (seconds, prompt tokens, output tokens) per model call


def _chat(model: str, messages: list, tools: Optional[list] = None):
    import ollama
    options = {"temperature": config.settings.temperature, "top_p": 0.9,
               "num_predict": config.settings.num_predict,
               "num_ctx": config.settings.num_ctx}
    kw = dict(model=model, messages=messages, options=options,
              keep_alive=config.settings.keep_alive)
    if tools:
        kw["tools"] = tools
    t0 = time.time()
    try:
        resp = ollama.chat(think=False, **kw)
    except TypeError:
        resp = ollama.chat(**kw)
    get = (resp.get if isinstance(resp, dict) else lambda k, d=None: getattr(resp, k, d))
    CALL_LOG.append((round(time.time() - t0, 1), get("prompt_eval_count", None),
                     get("eval_count", None)))
    return resp


def _content(resp) -> str:
    msg = resp["message"] if isinstance(resp, dict) else resp.message
    text = msg["content"] if isinstance(msg, dict) else (msg.content or "")
    return re.sub(r"<think>.*?</think>\s*", "", text or "", flags=re.S).strip()


def _tool_calls(resp) -> list:
    msg = resp["message"] if isinstance(resp, dict) else resp.message
    calls = msg.get("tool_calls") if isinstance(msg, dict) else getattr(msg, "tool_calls", None)
    out = []
    for c in calls or []:
        fn = c["function"] if isinstance(c, dict) else c.function
        name = fn["name"] if isinstance(fn, dict) else fn.name
        args = fn["arguments"] if isinstance(fn, dict) else fn.arguments
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        out.append((name, dict(args or {})))
    return out


_CONNECTOR = re.compile(r"^(?:However|But|Also|Additionally|Moreover|Still|Instead|"
                        r"Therefore|So|Thus|That said|In addition),?\s+", re.I)


def _tidy_after_removal(text: str) -> str:
    """A sentence that opened with 'However,' after the one it answered was
    removed reads as nonsense: drop the connector, capitalise."""
    out = []
    for line in text.split("\n"):
        m = re.match(r"^(\s*(?:\*\*[^*]+\*\*:?\s*)?)(.*)$", line)
        head, body = m.group(1), m.group(2)
        body2 = _CONNECTOR.sub("", body)
        if body2 != body and body2:
            body2 = body2[0].upper() + body2[1:]
        out.append(head + body2)
    return "\n".join(out)


def _grounded(model: str, messages: list, text: str, boards: list, keep: str,
              answer: Answer, question: str = "") -> str:
    """Check the text; one rewrite with the corrections; remove what still fails."""
    if not config.settings.grounding:
        return text
    cands = answer.candidates or None
    fails = ground(text, boards, keep=keep, candidates=cands)
    answer.caught = [f"{f.kind}: \"{f.claim}\" — {f.correction}" for f in fails]
    if fails and question:
        # In a conversation the model reads 'rewrite' as dialogue ("You are
        # correct. These points have been corrected."): ask for the answer
        # again instead, with the corrections stated as facts.
        fix = ("Before you answer, note these corrections. They are verified facts "
               "and override what you wrote:\n" + correction_text(fails)
               + f"\n\nNow answer the question again, in 3 to 6 plain sentences, "
                 f"using the tool results and these corrections: {question}\n"
                 "Reply with the answer only.")
    elif fails:
        fix = ("Some statements in your answer are false on the board:\n"
               + correction_text(fails)
               + "\nRewrite the whole answer, fixing only those statements. If you cannot "
                 "state a point correctly from the facts given, leave it out. Do not add "
                 "anything new. Reply with the rewritten answer only.")
    if fails:
        resp = _chat(model, messages + [{"role": "assistant", "content": text},
                                        {"role": "user", "content": fix}])
        text2 = _content(resp)
        fails2 = ground(text2, boards, keep=keep, candidates=cands)
        # A fresh answer to a question wins ties: it was written with the
        # corrections in front of it. An edited annotation must do better.
        better = (len(fails2) <= len(fails)) if question else (len(fails2) < len(fails))
        if text2 and better:
            text, fails = text2, fails2
    if fails:
        text = _tidy_after_removal(remove_failed(text, fails))
        seen = {}
        for f in fails:
            seen.setdefault(f.sentence, f.correction)
        answer.removed = [f"{s} [{c}]" for s, c in seen.items()]
    return text


# --- Explain this position -------------------------------------------------

EXPLAIN_SYSTEM = """You are a chess coach explaining a position to a club player (about 1500). You are a narrator: every fact you may use is given to you, verified. Do not calculate, do not invent pieces, squares, threats or moves, and do not write move notation. Describe moves in words. Use the exact verdict wording given for who is better. Follow the output format exactly."""


def explain_prompt(board: chess.Board, engine, history: str = "") -> tuple:
    """(prompt, boards to check against, code-written first sentence, key facts)."""
    ea = _analyse(engine, board, multipv=3)
    verdict = _verdict(ea, board)
    boards = [board.copy(stack=False)]
    me = board.turn
    key = []

    # Real threats: what the side not to move would do with a free move.
    t = threat(engine, board)
    if t:
        key.append(t.text())
        nb = board.copy(stack=False)
        nb.push(chess.Move.null())
        boards.append(nb)
    # Loose pieces the engine's line captures, either side.
    for line in loose_piece_lines(board):
        if line.startswith("Undefended"):
            key.append(line)
    # Castling still possible matters while kings are in the centre.
    for line in castling_lines(board):
        if "can still castle" in line:
            key.append(line)
    if board.is_check():
        key.append(f"{side_name(me)} is in check and must answer it.")

    # The engine's best moves, in words, with equal ones marked.
    best = []
    rows = [((ea.score_cp, ea.score_mate), ea.best_line)] + [
        (sc, line) for sc, (_, line) in zip(ea.alt_scores, ea.alt_lines)]
    top = None
    for (cp, mate), line in rows:
        if not line:
            continue
        boards += _line_boards(board, line)
        w = win_pct(cp, mate, me == chess.WHITE)
        top = w if top is None else top
        mv = board.parse_san(line[0])
        eq = " (practically as good as the first)" if best and top - w < 3 else ""
        best.append(f"- {describe_move(board, mv)}: winning chance for {side_name(me)} "
                    f"{w:.0f}%{eq}")
        for e in describe_move_effects(board, mv)[:3]:
            best.append(f"    - {e}")
        if len(best) > 12:
            break
    first = ea.best_line[0] if ea.best_line else ""
    alt = None
    if len(rows) > 1 and rows[1][1]:
        try:
            alt = equal_alternative(board, board.parse_san(first), ea) if first else None
        except ValueError:
            pass
        if alt:
            key.append(f"Two moves are practically equal for {side_name(me)}: the first "
                       f"choice and {alt[0]}")

    fixed = (f"{side_name(me)} is to move, and {verdict}. "
             + material_lines(board)[-1].replace("Material difference: ", ""))
    fixed = fixed.rstrip(".") + "."
    if alt:
        # The model left this out of every explanation: code writes it.
        first_words = describe_move(board, board.parse_san(first))
        fixed += (f" The engine's two best moves are practically equal: {first_words}, "
                  f"or {alt[0][0].lower() + alt[0][1:] if not alt[0].startswith(('White', 'Black')) else alt[0]}.")
    game = f"\nGAME SO FAR:\n{history}\n" if history else ""
    prompt = f"""Explain this position to the player.
{game}
SIDE TO MOVE: {side_name(me)}
VERDICT (use this exact wording for who is better): {verdict}

KEY FACTS (verified; the most important — build your explanation on these):
{chr(10).join('- ' + k for k in key) if key else '- No real threats; no loose pieces.'}

THE ENGINE'S BEST MOVES FOR {side_name(me).upper()} (in words, verified):
{chr(10).join(best) or '- none'}

ALL FACTS (verified and complete; anything not listed here is not so):
{position_facts(board, engine=ea)}

OUTPUT FORMAT (exactly these headings, in this order):
**The position**: The first sentence is already written: "{fixed}" Do NOT repeat it. Write 1-2 sentences on what matters most, from the KEY FACTS.
**Threats**: 1-2 sentences on what each side threatens, only from the KEY FACTS. If there are none, say so.
**Plan**: 1-2 sentences on what {side_name(me)} should do, describing the engine's best move in words and its idea, only from the facts above."""
    return prompt, boards, fixed, key


def explain(board: chess.Board, model: str, engine, history: str = "") -> Answer:
    t0 = time.time()
    ans = Answer()
    CALL_LOG.clear()
    if board.is_game_over():
        # Nothing to explain: code says what happened.
        if board.is_checkmate():
            ans.text = (f"**The position**: {side_name(board.turn)} is checkmated: "
                        f"{side_name(not board.turn)} has won the game.")
        else:
            ans.text = "**The position**: The game is drawn: no further moves are played."
        ans.secs = time.time() - t0
        return ans
    try:
        prompt, boards, fixed, key = explain_prompt(board, engine, history)
    except Exception as e:
        ans.error = f"analysis failed: {e}"
        return ans
    ans.facts, ans.fixed = key, fixed
    messages = [{"role": "system", "content": EXPLAIN_SYSTEM},
                {"role": "user", "content": prompt}]
    try:
        text = _content(_chat(model, messages))
        text = _grounded(model, messages, text, boards, fixed, ans)
    except Exception as e:
        ans.error = f"{e} (is '{model}' pulled? run: ollama list)"
        return ans
    m = re.search(r"(\*\*The position\*\*:?\s*)", text)
    if fixed.strip(" .") not in text:
        text = (text[:m.end()] + fixed + " " + text[m.end():]) if m \
            else f"**The position**: {fixed}\n" + text
    ans.text = text
    ans.secs = time.time() - t0
    ans.calls = list(CALL_LOG)
    return ans


# --- Ask the coach ---------------------------------------------------------

ASK_SYSTEM = """You are a chess coach answering a club player's question (about 1500) about the position on their board.

You cannot see the board, and you must not work anything out yourself. Every fact about pieces, squares, attacks, defences, threats, moves or evaluations must come from a tool result in this conversation. Call the tools you need first (at most {n} calls), then answer.

The answer: 3 to 6 plain sentences. Describe moves in words; you may repeat a move the player named. Never state something a tool did not return. If the tools cannot answer the question, say so plainly."""


def ask(board: chess.Board, question: str, model: str, engine,
        history: str = "", max_calls: int = MAX_TOOL_CALLS,
        mode: Optional[str] = None, me: Optional[bool] = None) -> Answer:
    """Ask the coach. mode "router" (default) or "agent"; see ask_routed.
    `me`: the asker's colour, when known."""
    if (mode or config.settings.ask_mode) != "agent":
        return ask_routed(board, question, model, engine, history, me)
    return ask_agent(board, question, model, engine, history, max_calls)


def ask_agent(board: chess.Board, question: str, model: str, engine,
              history: str = "", max_calls: int = MAX_TOOL_CALLS) -> Answer:
    """The model chooses its own tool calls."""
    t0 = time.time()
    ans = Answer()
    CALL_LOG.clear()
    tools = Tools(board, engine)
    messages = [{"role": "system", "content": ASK_SYSTEM.format(n=max_calls)},
                {"role": "user", "content":
                    (f"Game so far: {history}\n" if history else "")
                    + f"{side_name(board.turn)} is to move.\nQuestion: {question}"}]
    try:
        text = ""
        for _ in range(max_calls + 1):
            allowed = len(tools.calls) < max_calls
            resp = _chat(model, messages, tools=tools.schemas() if allowed else None)
            calls = _tool_calls(resp) if allowed else []
            if not calls:
                text = _content(resp)
                break
            messages.append({"role": "assistant", "content": _content(resp) or "",
                             "tool_calls": [{"function": {"name": n, "arguments": a}}
                                            for n, a in calls]})
            for name, args in calls[:max(0, max_calls - len(tools.calls))]:
                result = tools.call(name, args)
                messages.append({"role": "tool", "content": result, "tool_name": name,
                                 "name": name})
        if not text:
            messages.append({"role": "user", "content": "Answer the question now from "
                             "the tool results above."})
            text = _content(_chat(model, messages))
        ans.tool_calls = [(n, a) for n, a, _ in tools.calls]
        ans.boards = list(tools.boards)
        text = _grounded(model, messages, text, tools.boards, "", ans, question=question)
        if not text.strip():
            text = ("I could not give an answer to that which checks out against the "
                    "board. Try asking it another way, or about one move at a time.")
    except Exception as e:
        ans.error = f"{e} (is '{model}' pulled and does it support tools? run: ollama list)"
        return ans
    ans.text = text
    ans.secs = time.time() - t0
    ans.calls = list(CALL_LOG)
    return ans


# --- Ask the coach: the router ----------------------------------------------
#
# qwen3:14b did not pick tools reliably (phase 4 eval: 5/21). The router
# reads the question with code, gathers the facts that kind of question
# needs, writes the part of the answer code can decide (which move is
# better, whether a piece is attacked or defended), and the model only
# explains, from those results.

_PIECE_WORDS = {"king": chess.KING, "queen": chess.QUEEN, "rook": chess.ROOK,
                "bishop": chess.BISHOP, "knight": chess.KNIGHT, "pawn": chess.PAWN}
_TOKEN = re.compile(
    r"(?<![\w.])(\.\.\.)?((?:[KQRBN][a-h]?[1-8]?x?[a-h][1-8]|[a-h]x[a-h][1-8](?:=?[QRBN])?"
    r"|[a-h][1-8](?:=?[QRBN])?)[+#]?|O-O-O[+#]?|O-O[+#]?|0-0-0|0-0)(?![\w])")
_SQUARE_CONTEXT = re.compile(r"\b(on|to|at|onto|from|square|squares|and|or)\s*$", re.I)
_CAPTURE_VERB = re.compile(r"\b(take|takes|taking|capture|captures|capturing|grab|win)\b", re.I)
_SAFETY = re.compile(r"\b(safe|hanging|loose|defended|protected|undefended|unprotected|"
                     r"attacked|in danger|trapped)\b", re.I)
_YES_NO = re.compile(r"^\s*(does|do|is|are|can|will|would|could)\b", re.I)


@dataclass
class Route:
    kind: str = "general"        # line | compare | move | safety | general
    line: list = field(default_factory=list)      # SAN moves played first
    moves: list = field(default_factory=list)     # candidate moves (on base)
    squares: list = field(default_factory=list)   # squares asked about
    notes: list = field(default_factory=list)     # what could not be read
    rest: str = ""                                # the question after the line
    base: Optional[chess.Board] = None            # position the question is about


def _expand(board: chess.Board, tok: str) -> tuple:
    """([moves], note) for a move the player typed: an ambiguous move gives
    every candidate; an illegal one gives a note."""
    mv, why = resolve_move(board, tok)
    if mv is not None:
        return [mv], ""
    t = tok.rstrip("+#")
    to = re.search(r"([a-h][1-8])", t)
    letter = t[0] if t[:1] in list("KQRBN") else ""
    cands = [m for m in board.legal_moves if to
             and chess.square_name(m.to_square) == to.group(1)
             and (board.san(m)[:1] == letter if letter else
                  board.piece_type_at(m.from_square) == chess.PAWN)]
    if cands and "could mean" in why:
        return cands, ""
    return [], why


_SAFETY_WORDS = re.compile(
    r"\b(safe|attacked|hanging|en prise|defended|protected|undefended|loose|trapped|"
    r"danger|threatened|in trouble|lost|can be taken|be captured)\b")
_WHOSE_PIECE = re.compile(
    r"\b(my|white's|black's|the|his|her|their|opponent's)\s+(king|queen|rook|bishop|knight|pawn)s?\b")


def route_question(board: chess.Board, question: str,
                   me: Optional[bool] = None) -> Route:
    """`me`: the asker's colour when the app knows it (the reviewed side),
    so "my bishop" can be found on the board."""
    r = Route(base=board.copy(stack=False))
    q = question.strip()
    rest = q
    # 1. "After Rae1+ Kd8 Ne5, does ...": a line played first.
    m = re.match(r"\s*(?:and\s+|so\s+)?(?:if\s+)?after\s+(.*?)(?:,|\s(?=does|do|is|are|can|what|who|how|why)\b)",
                 q, re.I)
    if m:
        toks = [t.group(2) for t in _TOKEN.finditer(m.group(1))]
        if toks:
            res = play_line(board, toks, effects=False)
            r.line = [s.san for s in res.steps]
            if res.error:
                r.notes.append(res.error)
            r.base = res.board.copy(stack=False)
            rest = q[m.end():]
    r.rest = rest
    base = r.base
    # 2. Moves and squares named in the rest of the question.
    seen = set()
    for t in _TOKEN.finditer(rest):
        dots, tok = t.group(1), t.group(2)
        before = rest[:t.start()]
        after = rest[t.end():t.end() + 12].lower()
        bare = re.fullmatch(r"[a-h][1-8]", tok) is not None
        is_square = bare and not dots and (
            _SQUARE_CONTEXT.search(before) is not None
            or re.match(r"\s*(" + "|".join(_PIECE_WORDS) + r")", after) is not None)
        if bare and not dots and not is_square:
            is_square = resolve_move(base, tok)[0] is None and not _expand(base, tok)[0]
        if is_square:
            sq = chess.parse_square(tok)
            if sq not in r.squares:
                r.squares.append(sq)
            continue
        if dots and base.turn == chess.WHITE:
            r.notes.append(f"'...{tok}' is a Black move, but it is White to move.")
            continue
        cands, note = _expand(base, tok)
        if note:
            r.notes.append(note)
        for mv in cands:
            if mv not in seen:
                seen.add(mv)
                r.moves.append(mv)
    low = rest.lower()
    # 3. "Take the pawn on d4 or the knight on f6?": captures onto those squares.
    if _CAPTURE_VERB.search(low) and not r.moves:
        for sq in r.squares:
            for mv in base.legal_moves:
                if mv.to_square == sq and base.is_capture(mv) and mv not in seen:
                    seen.add(mv)
                    r.moves.append(mv)
    # 4. "Which rook should give check on e1?": that piece's moves to the square.
    w = re.search(r"\bwhich\s+(king|queen|rook|bishop|knight|pawn)", low)
    if w and not r.moves:
        pt = _PIECE_WORDS[w.group(1)]
        for mv in base.legal_moves:
            if base.piece_type_at(mv.from_square) != pt:
                continue
            if r.squares and mv.to_square not in r.squares:
                continue
            if "check" in low and not base.gives_check(mv):
                continue
            r.moves.append(mv)
        dest = {mv.to_square for mv in r.moves}
        r.squares = [sq for sq in r.squares if sq not in dest]
    # 4b. "Is my bishop safe?": no square named, so find the piece. One
    # such piece: its square; several: all of them, and the answer covers
    # each. Whose: "my" is the asker's side when known; "White's"/"Black's"
    # as said; otherwise both sides'.
    if not r.moves and not r.squares and _SAFETY_WORDS.search(low):
        p = _WHOSE_PIECE.search(low)
        if p:
            whose, pt = p.group(1), _PIECE_WORDS[p.group(2)]
            if whose == "white's":
                colors = [chess.WHITE]
            elif whose == "black's":
                colors = [chess.BLACK]
            elif whose == "my" and me is not None:
                colors = [me]
            elif whose in ("his", "her", "their", "opponent's") and me is not None:
                colors = [not me]
            else:
                colors = [chess.WHITE, chess.BLACK]
                if whose == "my":
                    r.notes.append("It is not known which side the asker plays; "
                                   "the facts cover both sides' pieces of that kind.")
            r.squares = sorted(sq for c in colors for sq in base.pieces(pt, c))
            if not r.squares:
                r.notes.append(f"There is no such {p.group(2)} on the board.")
    # 5. The kind of question.
    if r.line:
        r.kind = "line"
    elif len(r.moves) >= 2:
        r.kind = "compare"
    elif r.moves:
        r.kind = "move"
    elif r.squares:
        r.kind = "safety"
    return r


def square_facts(board: chess.Board, sq: int) -> str:
    """Who stands on a square, who attacks it and who defends it, in words."""
    name = chess.square_name(sq)
    p = board.piece_at(sq)

    def names(color):
        return join_words([f"the {PIECE_NAMES[board.piece_type_at(s)]} on "
                           f"{chess.square_name(s)}"
                           for s in sorted(board.attackers(color, sq))])
    if not p:
        w, b = names(chess.WHITE), names(chess.BLACK)
        return (f"{name} is empty. White pieces that reach it: {w or 'none'}. "
                f"Black pieces that reach it: {b or 'none'}.")
    me = side_name(p.color)
    label = f"The {me} {PIECE_NAMES[p.piece_type]} on {name}"
    att, dfn = names(not p.color), names(p.color)
    s = (f"{label} is attacked by {att}." if att else
         f"{label} is not attacked by any {side_name(not p.color)} piece right now.")
    if p.piece_type != chess.KING:
        s += (f" It is defended by {dfn}." if dfn else " It has no defender.")
    return s


def attack_facts(board: chess.Board, sq: int, targets: list) -> tuple:
    """(text, attacks_all_targets, enemy pieces attacked) for the piece on sq."""
    p = board.piece_at(sq)
    hit = [s for s in sorted(board.attacks(sq))
           if board.piece_at(s) and board.color_at(s) != p.color]
    label = f"The {side_name(p.color)} {PIECE_NAMES[p.piece_type]} on {chess.square_name(sq)}"
    words = join_words([f"the {side_name(board.color_at(s))} "
                        f"{PIECE_NAMES[board.piece_type_at(s)]} on {chess.square_name(s)}"
                        for s in hit])
    out = [f"{label} attacks {words}." if hit else
           f"{label} attacks no {side_name(not p.color)} piece."]
    missing = [t for t in targets if t not in hit and t != sq]
    for t in missing:
        what = (f"the {PIECE_NAMES[board.piece_type_at(t)]} on {chess.square_name(t)}"
                if board.piece_at(t) else chess.square_name(t))
        out.append(f"{label} does NOT attack {what}.")
    return " ".join(out), not missing, hit


def _actor(board: chess.Board, rest: str, line: list, start: chess.Board) -> Optional[int]:
    """The piece a line question is about: the piece named in the question
    that moved last in the line, else the last piece moved."""
    low = rest.lower()
    # Piece words in the order they appear, except targets ("rook on b7").
    named = [_PIECE_WORDS[m.group(1)] for m in
             re.finditer(r"\b(king|queen|rook|bishop|knight|pawn)\b(?!\s+on\s+[a-h][1-8])", low)]
    b = start.copy(stack=False)
    moved = []
    for san in line:
        mv = b.parse_san(san)
        b.push(mv)
        moved.append(mv.to_square)
    for sq in reversed(moved):
        if board.piece_at(sq) and (not named or board.piece_type_at(sq) == named[0]):
            return sq
    return moved[-1] if moved and board.piece_at(moved[-1]) else None


ROUTER_SYSTEM = """You are a chess coach answering a club player's question (about 1500). You are a narrator: every fact you may use is given to you, verified by a chess engine and the rules. You cannot see the board. Do not calculate, do not invent pieces, squares, threats or moves. Describe moves in words; you may repeat a move as the player wrote it. Never contradict the verified results."""


def _san(board: chess.Board, mv: chess.Move) -> str:
    """A move as a player writes it: '...g5' for Black."""
    return ("..." if board.turn == chess.BLACK else "") + board.san(mv)


def _win(board: chess.Board, ea: EngineAnalysis, color: bool):
    return win_pct(ea.score_cp, ea.score_mate, color == chess.WHITE)


def ask_routed(board: chess.Board, question: str, model: str, engine,
               history: str = "", me: Optional[bool] = None) -> Answer:
    t0 = time.time()
    ans = Answer()
    CALL_LOG.clear()
    tools = Tools(board, engine)
    try:
        r = route_question(board, question, me)
    except Exception as e:                           # a question code cannot read
        r = Route(base=board.copy(stack=False), notes=[f"could not read the question: {e}"])
    ans.route = r.kind
    base, me = r.base, r.base.turn
    sections, lead = [], []

    def add(title, text):
        if text and text.strip():
            sections.append(f"== {title} ==\n{text.strip()}")

    try:
        ea_now = _analyse(engine, base, multipv=3)
        tools._remember_line(base, ea_now.best_line)
        add("THE POSITION AT THE END OF THE LINE" if r.line else "THE POSITION NOW",
            f"{side_name(me)} is to move. Verdict: {_verdict(ea_now, base)}.\n"
            + material_lines(base)[-1])
        if r.notes:
            add("COULD NOT READ", "\n".join(r.notes))

        if r.kind == "line":
            add("THE LINE, MOVE BY MOVE", tools.call("play_line", {"moves": r.line}))
            actor = _actor(base, r.rest, r.line, board)
            if actor is not None and not base.is_game_over():
                text, all_hit, hit = attack_facts(base, actor, r.squares)
                add("AT THE END OF THE LINE", text)
                low = r.rest.lower()
                piece = f"the {PIECE_NAMES[base.piece_type_at(actor)]} on {chess.square_name(actor)}"
                if _YES_NO.match(r.rest) and ("fork" in low or "attack" in low) and r.squares:
                    if not all_hit:
                        miss = [s for s in r.squares if s not in hit and s != actor]
                        lead.append(f"No: after {' '.join(r.line)}, {piece} does not attack "
                                    + join_words([("the " + PIECE_NAMES[base.piece_type_at(s)]
                                                   + " on " + chess.square_name(s))
                                                  if base.piece_at(s) else chess.square_name(s)
                                                  for s in miss]) + ".")
                    elif "fork" in low and len(hit) >= 2:
                        lead.append(f"Yes: after {' '.join(r.line)}, {piece} attacks "
                                    + join_words([f"the {PIECE_NAMES[base.piece_type_at(s)]} on "
                                                  f"{chess.square_name(s)}" for s in hit]) + ".")
            for sq in r.squares:
                add(f"SQUARE {chess.square_name(sq).upper()} AT THE END OF THE LINE",
                    square_facts(base, sq))
            if not base.is_game_over():
                add("THREATS AT THE END OF THE LINE", tools.call("threats", {"moves": r.line}))

        elif r.kind in ("compare", "move"):
            cands = list(r.moves)
            best = base.parse_san(ea_now.best_line[0]) if ea_now.best_line else None
            if r.kind == "move" and best is not None and best not in cands:
                cands.append(best)                   # "Why not X?": X against the best
            rows = []
            for mv in cands[:5]:
                a = base.copy(stack=False)
                a.push(mv)
                ea = _analyse(engine, a, multipv=1)
                tools.boards.append(a.copy(stack=False))
                tools._remember_line(a, ea.best_line)
                rows.append((_win(a, ea, me), mv, a, ea))
                ans.candidates.append((base.copy(stack=False), mv,
                                       [a.copy(stack=False)] + _line_boards(a, ea.best_line)))
            rows.sort(key=lambda x: -(x[0] if x[0] is not None else -1))
            top = rows[0]
            text = []
            for w, mv, a, ea in rows:
                tag = " (the engine's first choice)" if mv == best else ""
                text.append(f"- {_san(base, mv)}: {describe_move(base, mv)}{tag}. Afterwards "
                            f"{_verdict(ea, a)}; winning chance for {side_name(me)} "
                            f"{w:.0f}%." if w is not None else
                            f"- {_san(base, mv)}: {describe_move(base, mv)}.")
                text += [f"    - {e}" for e in describe_move_effects(base, mv)[:4]]
                if ea.best_line:
                    text.append(f"    - {side_name(not me)}'s best reply: "
                                + describe_move(a, a.parse_san(ea.best_line[0])) + ".")
            # The facts that explain the difference, pulled to the top: the
            # model left "Nxd4 leaves e5 undefended" out when it sat third
            # in a list of effects.
            key = []
            for w, mv, a, ea in rows:
                for e in describe_move_effects(base, mv):
                    if ("NO defender" in e or "no longer defends" in e
                            or e.startswith("It leaves")):
                        key.append(f"{_san(base, mv)}: {e}")
            if key:
                add("KEY FACTS (build the explanation on these)", "\n".join(key[:6]))
            add("THE MOVES ASKED ABOUT (verified)", "\n".join(text))
            # The verdict between them is code's to write.
            if len(rows) >= 2 and rows[0][0] is not None and rows[1][0] is not None:
                if rows[0][0] - rows[1][0] < 3:
                    eq = [x for x in rows if rows[0][0] - x[0] < 3]
                    lead.append(join_words([_san(base, x[1]) for x in eq])
                                + (" are practically equal: the winning chance for "
                                   f"{side_name(me)} is about the same after each "
                                   f"({', '.join(f'{x[0]:.0f}%' for x in eq)})."))
                else:
                    w1, m1 = rows[0][0], rows[0][1]
                    head = (f"The engine prefers {_san(base, m1)}" if m1 not in r.moves
                            else f"{_san(base, m1)} is clearly better")
                    lead.append(f"{head}: the winning chance for {side_name(me)} is "
                                f"{w1:.0f}% after it, against "
                                + join_words([f"{x[0]:.0f}% after {_san(base, x[1])}"
                                              for x in rows[1:]]) + ".")
            elif len(rows) == 1 and best is not None and rows[0][1] == best:
                lead.append(f"{_san(base, best)} is the engine's first choice here.")
            # How the worse moves differ from the best a few moves later.
            for w, mv, a, ea in rows[1:3]:
                if rows[0][0] is not None and w is not None and rows[0][0] - w < 3:
                    continue
                cmp = tools.call("compare_moves", {"move_a": base.san(mv),
                                                   "move_b": base.san(top[1])})
                cut = cmp.find("How the positions differ")
                # Differences the move did not cause are true but beside the point.
                cmp = "\n".join(l for l in cmp.split("\n")
                                if "did not cause it" not in l)
                ls = cmp.split("\n")
                cmp = "\n".join(l for i, l in enumerate(ls) if not (
                    l.startswith("- In favour") and not
                    (i + 1 < len(ls) and ls[i + 1].startswith("  - "))))
                cut = cmp.find("How the positions differ")
                if cut >= 0 and cmp[cut:].count("\n  - "):
                    add(f"{_san(base, mv)} (MOVE A) AGAINST {_san(base, top[1])} (MOVE B)",
                        cmp[cut:])
            add("THREATS IN THE POSITION NOW", tools.call("threats", {}))
            for sq in r.squares:
                add(f"SQUARE {chess.square_name(sq).upper()}", square_facts(base, sq))

        elif r.kind == "safety":
            for sq in r.squares:
                f = square_facts(base, sq)
                add(f"SQUARE {chess.square_name(sq).upper()}", f)
                p = base.piece_at(sq)
                if p and p.piece_type != chess.KING:
                    lead.append(f)
                    if p.color != me and _pv_captures(base, ea_now.best_line, sq):
                        lead.append("The engine's best line captures it.")
            add("THREATS IN THE POSITION NOW", tools.call("threats", {}))
            add("THE ENGINE'S BEST MOVES", tools.call("best_moves", {}))

        else:
            add("THE ENGINE'S BEST MOVES", tools.call("best_moves", {}))
            add("THREATS IN THE POSITION NOW", tools.call("threats", {}))
            add("ALL FACTS", position_facts(base, engine=ea_now))
    except Exception as e:
        ans.error = f"analysis failed: {e}"
        return ans

    fixed = " ".join(lead).strip()
    ans.fixed, ans.facts = fixed, sections
    ans.tool_calls = [(n, a) for n, a, _ in tools.calls]
    ans.boards = list(tools.boards)
    first = (f'The first sentence of the answer is already written: "{fixed}" '
             "Do not repeat it and do not contradict it; explain WHY, from the results.\n"
             if fixed else "")
    game = f"Game so far: {history}\n" if history else ""
    prompt = (f"{game}QUESTION: {question}\n\nVERIFIED RESULTS (complete; anything not "
              f"listed here is not so):\n\n" + "\n\n".join(sections)
              + f"\n\n{first}Answer the question in 3 to 5 plain sentences, using only the "
                "verified results. Name what each move attacks, defends or gives up when "
                "the results say so. Reply with the answer only.")
    messages = [{"role": "system", "content": ROUTER_SYSTEM},
                {"role": "user", "content": prompt}]
    try:
        text = _content(_chat(model, messages))
        if fixed:
            # The model sometimes restates the first sentence anyway.
            text = text.replace(fixed, "").strip()
        text = _grounded(model, messages, text, tools.boards, fixed, ans, question=question)
        if fixed:                            # the re-answer can restate it too
            text = text.replace(fixed, "").strip()
    except Exception as e:
        ans.error = f"{e} (is '{model}' pulled? run: ollama list)"
        return ans
    text = (fixed + " " + text).strip() if fixed else text
    if not text.strip():
        text = ("I could not give an answer to that which checks out against the "
                "board. Try asking it another way, or about one move at a time.")
    ans.text = text
    ans.secs = time.time() - t0
    ans.calls = list(CALL_LOG)
    return ans


def open_engine():
    config.settings.require_stockfish()
    return chess.engine.SimpleEngine.popen_uci(config.settings.stockfish_path)


def board_from(fen: str = "", pgn_moves: str = "") -> tuple:
    """(board, 'game so far' text) from a FEN or from moves from the start."""
    if pgn_moves:
        b = chess.Board()
        for tok in pgn_moves.replace(".", ". ").split():
            if tok.rstrip(".").isdigit() or tok in ("1-0", "0-1", "1/2-1/2", "*"):
                continue
            b.push_san(tok)
        history = chess.Board().variation_san(b.move_stack)
        return b, history
    return chess.Board(fen) if fen else chess.Board(), ""

