"""
How hard an error was to avoid, and whether a line was forced.

Stockfish says what is best, not what is hard to find. Two measures, after
Chess Engine Lab's "Using Stockfish to find tactics" and Maia:

  depth   Compare the engine's move with the one played at a very short
          search (two half-moves after the move) and at full depth. If even
          the short look shows most of the difference, the error was in
          plain sight (a piece left hanging, a mate in one); if the short
          look sees no difference at all, the point only shows several
          moves deep (a pawn fork whose payoff comes through a pin).
  human   What Maia, a model of players at your rating, plays there: the
          engine's move, the move you played, or something else.

Forced lines: in the engine's line, every reply is the only legal move, or
the attacker checks on every move. The model can't work that out from a
line of moves, and it is exactly what makes a mating attack easy to follow.

Everything here is computed by code; the sentences are facts the coach is
given and the player sees.
"""

from __future__ import annotations

from typing import Optional

import chess
import chess.engine

from .assessment import win_pct
from .describe import side_name
from .motifs import _balance

SHALLOW_DEPTH = 2          # the short look: depth 2 after the move
PLAIN = 0.6                # the short look sees at least this share of the difference
DEEP = 0.25                # ...or at most this share
LEVELS = {"plain": "In plain sight", "middle": "Partly hidden", "deep": "Hard to see"}


def _shallow_win(engine, board: chess.Board, me: bool) -> float:
    if board.is_checkmate():
        return 0.0 if board.turn == me else 100.0
    if board.is_game_over(claim_draw=True):
        return 50.0
    try:
        engine.configure({"Clear Hash": None})
    except Exception:
        pass
    info = engine.analyse(chess.Board(board.fen()), chess.engine.Limit(depth=SHALLOW_DEPTH))
    s = info["score"].white()
    w = win_pct(None if s.is_mate() else s.score(), s.mate() if s.is_mate() else None,
                me == chess.WHITE)
    return 50.0 if w is None else w


def difficulty(engine, board: chess.Board, played: chess.Move, best: chess.Move,
               win_best: Optional[float], win_played: Optional[float],
               maia_san: str = "", elo: int = 0) -> Optional[dict]:
    """How hard it was to find `best` instead of `played`, from the position
    before the move. win_best / win_played: the mover's winning chance at
    full depth (the review's numbers). None when there is nothing to judge.
    -> {level, label, deep_gap, shallow_gap, maia, text, fact}"""
    if best == played or win_best is None or win_played is None:
        return None
    deep_gap = win_best - win_played
    if deep_gap < 5:
        return None
    me = board.turn
    after_best, after_played = board.copy(stack=False), board.copy(stack=False)
    after_best.push(best)
    after_played.push(played)
    shallow_gap = _shallow_win(engine, after_best, me) - _shallow_win(engine, after_played, me)
    share = shallow_gap / deep_gap
    level = "plain" if share >= PLAIN else "deep" if share <= DEEP else "middle"
    b, p = board.san(best), board.san(played)
    if level == "plain":
        text = f"In plain sight: even a two-move look shows {b} is better than {p}."
    elif level == "deep":
        text = (f"Hard to see: {b} only proves better several moves deep; a two-move "
                f"look rates it no better than {p}.")
    else:
        text = f"Partly hidden: a two-move look shows only part of why {b} is better than {p}."
    who = f"players around {elo}" if elo else "players at this level"
    if maia_san:
        if maia_san == b:
            text += f" {who.capitalize()} usually find {b} here."
        elif maia_san == p:
            text += f" {p} is what {who} usually play here."
        else:
            text += f" {who.capitalize()} usually play {maia_san} here."
    # The coach's fact names no moves: its prompt allows no notation.
    fact = {"plain": "in plain sight: even a two-move search shows the engine's move is "
                     "better than the move played",
            "middle": "partly hidden: a two-move search shows only part of why the "
                      "engine's move is better",
            "deep": "hard to see: the engine's move only proves better several moves "
                    "deep; a two-move search rates it no better than the move played"}[level]
    if maia_san:
        fact += ("; players around this rating usually find the engine's move" if maia_san == b
                 else "; the move played is what players around this rating usually play"
                 if maia_san == p else "")
    fact = "How hard the better move was to find (measured): " + fact + "."
    return {"level": level, "label": LEVELS[level], "deep_gap": round(deep_gap, 1),
            "shallow_gap": round(shallow_gap, 1), "maia": maia_san, "text": text,
            "fact": fact}


def forced_line(board: chess.Board, line: list, max_plies: int = 8,
                whose: str = "the engine's line") -> Optional[dict]:
    """When the line (SAN, from `board`) is forced and pays off (every reply
    the only legal move, or a check on every attacking move; and it mates or
    wins material): {text (with the moves, for the player), fact (without
    them, for the coach)}. None otherwise. `whose` names the line in the fact."""
    b = board.copy(stack=False)
    attacker = b.turn
    start = _balance(b, attacker)
    att = checks = defs = only = 0
    played = []
    for san in line[:max_plies]:
        try:
            mv = b.parse_san(san)
        except ValueError:
            break
        if b.turn == attacker:
            att += 1
            checks += b.gives_check(mv)
        else:
            defs += 1
            only += b.legal_moves.count() == 1
        played.append(mv)
        b.push(mv)
    if not played:
        return None
    pays = b.is_checkmate() or _balance(b, attacker) - start >= 2
    if not pays:
        return None
    who, other = side_name(attacker), side_name(not attacker)
    text = board.variation_san(played)
    end = ", and it ends in checkmate" if b.is_checkmate() else ""
    if defs >= 2 and only == defs:
        return {"text": f"The line {text} is forced: each of {other}'s replies is the only "
                        f"legal move{end}.",
                "fact": f"In {whose}, each of {other}'s replies is the only legal move{end}."}
    if att >= 2 and checks == att:
        return {"text": f"In the line {text}, {who} gives check on every move, so {other}'s "
                        f"replies are forced{end}.",
                "fact": f"In {whose}, {who} gives check on every move, so {other}'s replies "
                        f"are forced{end}."}
    return None
