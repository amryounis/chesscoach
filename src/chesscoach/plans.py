"""
Plans: why a quiet engine move is good (after DecodeChess's "Plans").

    Idea      what the move prepares: with a free move afterwards, the side
              would play X (the engine's threat search, as in concepts.threat)
    Problem   X played at once does not work: the winning chance falls, and
              where code can find it, the opponent's reply that spoils it
              (a reply that hits the square the quiet move left)
    Solution  the quiet move first
    Outcome   the engine's line after it

Everything is an engine search or a board fact; the coach is given the
result as a verified fact and only puts it in words.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

import chess

from .assessment import win_pct
from .concepts import _forcing, search, threat
from .describe import PIECE_NAMES, describe_move, side_name

MIN_GAIN = 8          # the idea must be worth this much after the quiet move
MIN_PROBLEM = 8       # and this much less when played at once


@dataclass
class Plan:
    move: str                       # the quiet move, SAN ("Qa1")
    idea: str                       # the idea, SAN ("Bxd5")
    idea_words: str                 # "White captures Black's bishop on d5 with …"
    idea_line: list                 # SAN, from the position after the quiet move
    gain: float                     # what the idea is worth once prepared
    now_win: float                  # winning chance if the idea is played at once
    plan_win: float                 # winning chance after the quiet move
    problem_line: list = field(default_factory=list)   # SAN from the position now
    problem_reply: str = ""         # the opponent's reply that spoils it (SAN)
    problem_text: str = ""          # why, in words, when code can say
    outcome: list = field(default_factory=list)        # SAN after the quiet move
    move_words: str = ""            # the quiet move in words
    reply_words: str = ""           # the spoiling reply in words
    vacated: str = ""               # the square the quiet move left ("b2")
    idea_uci: str = ""              # for the arrow

    def to_dict(self) -> dict:
        return asdict(self)

    def facts(self, who: str) -> list:
        """The plan as verified sentences for the coach, in words (the coach
        writes no move notation)."""
        out = [f"PLAN: the engine's move ({self.move_words}) is quiet; it prepares this: "
               f"{self.idea_words}, worth about {self.gain:.0f} points of winning chance "
               f"once the quiet move has been played",
               f"PLAN: played at once, that capture or attack does not work: {who}'s "
               f"winning chance would be {self.now_win:.0f}% instead of {self.plan_win:.0f}%"
               + (f". The reason: in that line {self.reply_words}, hitting the piece on "
                  f"{self.vacated}, which the quiet move takes away first"
                  if self.reply_words else "")]
        return out


def _sans(board: chess.Board, moves: list, n: int = 8) -> list:
    out, b = [], board.copy(stack=False)
    for mv in moves[:n]:
        if mv not in b.legal_moves:
            break
        out.append(b.san(mv))
        b.push(mv)
    return out


def _win(s, color: bool) -> Optional[float]:
    return win_pct(s.cp, s.mate, color == chess.WHITE)


def _spoiler(board: chess.Board, quiet: chess.Move, idea_pv: list, engine,
             depth: int) -> tuple:
    """Follow the idea's own moves from the position now (the planning side
    plays the moves of the idea line, the opponent answers with the engine's
    best) and find the reply that hits the square the quiet move left, or
    captures there: 21.Bxd5 exd5 22.Rxd5 Qb6 hits the queen still on b2.
    (line SAN, reply SAN, text) or ("", "", "")."""
    me = board.turn
    vacated = quiet.from_square
    piece = board.piece_at(vacated)
    b = board.copy(stack=False)
    line = []
    own = [m for i, m in enumerate(idea_pv) if i % 2 == 0][:3]   # planning side's moves
    for k, mv in enumerate(own):
        if b.turn != me or mv not in b.legal_moves:
            break
        line.append(b.san(mv))
        b.push(mv)
        if b.is_game_over():
            break
        reply = search(engine, b, depth=depth).pv
        if not reply:
            break
        r = reply[0]
        after = b.copy(stack=False)
        after.push(r)
        hits = (r.to_square == vacated and b.is_capture(r)) or (
            vacated in after.attacks(r.to_square) and after.piece_at(vacated) == piece)
        san = b.san(r)
        line.append(san)
        if hits and piece:
            _spoiler.words = describe_move(b, r)
            what = f"the {PIECE_NAMES[piece.piece_type]} on {chess.square_name(vacated)}"
            text = (f"After {' '.join(line[:-1])}, {side_name(not me)} answers {san}, which "
                    f"hits {what}; {board.san(quiet)} moves it out of the way first")
            return line, san, text
        b = after
    return line, "", ""


def find_plan(engine, board: chess.Board, quiet: chess.Move,
              depth: Optional[int] = None) -> Optional[Plan]:
    """The plan behind `quiet` (usually the engine's best move) in `board`,
    or None when the move is forcing, prepares nothing measurable, or its
    idea works just as well at once."""
    from . import config
    # Full depth: at 14 the engine still thinks 21.Bxd5 works at once in
    # Kramnik-Leko; at 18 it sees why it does not.
    depth = depth or max(config.settings.depth, config.settings.concept_depth)
    if quiet not in board.legal_moves or _forcing(board, quiet):
        return None
    me = board.turn
    after = board.copy(stack=False)
    after.push(quiet)
    plan_s = search(engine, after, depth=depth)
    t = threat(engine, after, now=plan_s)
    if t is None or t.mate or t.gain < MIN_GAIN or t.side != me:
        return None
    idea = t.move
    if idea not in board.legal_moves:
        return None                                   # needs the quiet move to exist at all
    now = board.copy(stack=False)
    now.push(idea)
    now_s = search(engine, now, depth=depth)
    plan_win, now_win = _win(plan_s, me), _win(now_s, me)
    if plan_win is None or now_win is None or plan_win - now_win < MIN_PROBLEM:
        return None
    nb = after.copy(stack=False)
    nb.push(chess.Move.null())
    _spoiler.words = ""
    line, reply, text = _spoiler(board, quiet, t.pv, engine, depth)
    return Plan(move_words=describe_move(board, quiet), reply_words=_spoiler.words if reply else "",
                vacated=chess.square_name(quiet.from_square), idea_uci=idea.uci(),move=board.san(quiet), idea=board.san(idea), idea_words=t.words,
                idea_line=_sans(nb, t.pv), gain=t.gain, now_win=now_win, plan_win=plan_win,
                problem_line=line if reply else [board.san(idea)] + _sans(now, now_s.pv, 5),
                problem_reply=reply, problem_text=text,
                outcome=[board.san(quiet)] + _sans(after, plan_s.pv, 7))


def plan_boards(board: chess.Board, plan: dict) -> list:
    """Every position the plan's lines pass through, for the grounding check
    (the coach may say 'the queen on b6 hits the queen on b2')."""
    out = []
    for start, line in ((board, plan.get("problem_line", [])), (board, plan.get("outcome", []))):
        b = start.copy(stack=False)
        for san in line:
            try:
                b.push_san(san)
            except ValueError:
                break
            out.append(b.copy(stack=False))
    try:
        after = board.copy(stack=False)
        after.push_san(plan["move"])
        after.push(chess.Move.null())
        for san in plan.get("idea_line", []):
            after.push_san(san)
            out.append(after.copy(stack=False))
    except (ValueError, KeyError):
        pass
    return out
