"""
A reviewed game as an annotated PGN, in the conventions every chess
program reads (ChessBase, Lichess, Lucas Chess, SCID):

  - move marks as NAGs: $6 ?! inaccuracy, $2 ? mistake, $4 ?? blunder,
    $1 ! for a strong find (the only good move, punishing a mistake, mate)
  - [%eval] on every move: Stockfish's score after it, as Lichess and
    ChessBase write it, so their evaluation graphs draw from it
  - the coach's comment on each critical moment, as plain text
  - on every error, the engine's better line as a variation, with its score
  - ECO / Opening headers, Annotator, and the code-written game summary
    (accuracy, errors, what they allowed) as the comment before move 1
"""

from __future__ import annotations

import re
from typing import Optional

import chess
import chess.engine
import chess.pgn

from . import __version__
from .motifs import MOTIFS

NAG_FOR = {"Inaccuracy": chess.pgn.NAG_DUBIOUS_MOVE, "Mistake": chess.pgn.NAG_MISTAKE,
           "Blunder": chess.pgn.NAG_BLUNDER}
MARK_NAG = {"!!": chess.pgn.NAG_BRILLIANT_MOVE, "!": chess.pgn.NAG_GOOD_MOVE,
            "!?": chess.pgn.NAG_SPECULATIVE_MOVE}
PRAISE = {"found the only good move", "punished the opponent's mistake", "delivered checkmate"}
ERRORS = set(NAG_FOR)


def _g(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def plain_comment(text: str) -> str:
    """The coach's '**Heading**: text' lines as one plain paragraph: the
    Assessment line is dropped (the NAG says it), 'What happened' is the
    body, other headings stay as 'Better was: ...'."""
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^\*\*(.+?)\*\*:?\s*(.*)$", line)
        if m and re.search(r"assessment", m.group(1), re.I):
            continue
        body = re.sub(r"\*\*(.+?)\*\*", r"\1", m.group(2) if m else line).strip()
        if m and not re.search(r"what happened", m.group(1), re.I):
            body = f"{m.group(1).strip()}: {body}"
        if body:
            out.append(body)
    # braces would end a PGN comment early
    return " ".join(out).replace("{", "(").replace("}", ")")


def _score(cp: Optional[int], mate: Optional[int]) -> Optional[chess.engine.PovScore]:
    if mate is not None:
        return chess.engine.PovScore(chess.engine.Mate(mate), chess.WHITE)
    if cp is not None:
        return chess.engine.PovScore(chess.engine.Cp(cp), chess.WHITE)
    return None


def _score_text(cp: Optional[int], mate: Optional[int]) -> str:
    if mate is not None:
        return f"#{mate}" if mate > 0 else f"#-{-mate}"
    return "" if cp is None else f"{cp / 100:+.2f}"


COACH = "Coach:"           # starts the coach's part of a comment, so a reader can tell
SUMMARY = "Coach summary:" # it from the annotator's (and the app doesn't show it twice)


def _after_first(mate: Optional[int], cp: Optional[int], mover_white: bool) -> tuple:
    """The score after the first move of the engine's line, from the score
    before it. Centipawns: the same (it is the line's value). Mate: one
    move fewer when the side moving is the one mating; unchanged when it
    is the one being mated."""
    if mate is None:
        return None, cp
    mating_white = mate > 0
    if mating_white == mover_white:
        left = abs(mate) - 1
        return (None, None) if left <= 0 else ((left if mating_white else -left), None)
    return mate, None


def _strip_coach(game: chess.pgn.Game) -> None:
    """Take out what an earlier export by chesscoach added: the parts of
    comments from "Coach:" on, the summary, and the engine's variations."""
    def own_part(c: str) -> str:
        for mark in (SUMMARY, COACH):
            i = c.find(mark)
            if i >= 0:
                c = c[:i]
        return c.strip()
    game.comment = own_part(game.comment)
    for node in game.mainline():
        node.comment = own_part(node.comment)
        parent = node.parent
        for v in list(parent.variations[1:]):
            if v.comment.strip().startswith(f"{COACH} Stockfish"):
                parent.remove_variation(v)


def annotated_game(review, source: Optional[chess.pgn.Game] = None) -> chess.pgn.Game:
    """`review`: a GameReview or its dict. `source`: the game as it was
    opened; its own comments, NAGs and variations are kept and the coach's
    are added after them (marked "Coach:")."""
    import io
    headers = dict(_g(review, "headers") or {})
    start = chess.Board(_g(review, "start_fen") or chess.STARTING_FEN)
    moves = _g(review, "moves") or []
    if source is not None and [m.uci() for m in source.mainline_moves()] == [_g(m, "uci") for m in moves]:
        game = chess.pgn.read_game(io.StringIO(str(source)))      # a copy, with everything in it
        own = True
        _strip_coach(game)                     # a re-review replaces the coach's earlier notes
    else:
        game = chess.pgn.Game()
        for k, v in headers.items():
            game.headers[k] = v
        if start.fen() != chess.STARTING_FEN:
            game.setup(start)
        own = False
    models = _g(review, "models") or []
    coach = f"chesscoach {__version__}" + (f" ({models[0]})" if models else "")
    prior = game.headers.get("Annotator", "")
    game.headers["Annotator"] = coach if not prior or prior.startswith("chesscoach") else f"{prior}; {coach}"
    named = [m for m in moves if _g(m, "opening")]
    if named:
        o = _g(named[-1], "opening")
        if game.headers.get("ECO", "?") in ("", "?"):
            game.headers["ECO"] = o.get("eco", "")
        if game.headers.get("Opening", "?") in ("", "?"):
            game.headers["Opening"] = o.get("name", "")

    intro = []
    acc = _g(review, "accuracy") or {}
    if acc.get("white") is not None or acc.get("black") is not None:
        intro.append("Accuracy: " + ", ".join(
            f"{c.capitalize()} {acc[c]:.0f}%" for c in ("white", "black") if acc.get(c) is not None)
            + " (Lichess's method).")
    intro += [c["text"] if isinstance(c, dict) else c.text
              for c in (_g(review, "summary") or []) if (_g(c, "kind") != "accuracy")]
    if intro:
        text = f"{SUMMARY} " + " ".join(intro).replace("{", "(").replace("}", ")")
        game.comment = f"{game.comment} {text}".strip() if own and game.comment else text

    nodes = list(game.mainline()) if own else []
    node = game
    board = start.copy()
    for k, mv in enumerate(moves):
        move = chess.Move.from_uci(_g(mv, "uci"))
        parent, before = node, board.copy()
        node = nodes[k] if own else parent.add_variation(move)
        board.push(move)
        label, reason = _g(mv, "label", ""), _g(mv, "reason", "")
        mark = _g(mv, "mark", "")
        has_move_nag = any(1 <= n <= 6 for n in node.nags)         # the annotator's mark wins
        if not has_move_nag:
            if mark in MARK_NAG:                   # !! ! !? from classify.py
                node.nags.add(MARK_NAG[mark])
            elif label in NAG_FOR:
                node.nags.add(NAG_FOR[label])
            elif reason in PRAISE:
                node.nags.add(chess.pgn.NAG_GOOD_MOVE)
        parts = []
        # The opening once, where the game settled into it (the header has it too).
        if named and mv is named[-1]:
            o = _g(mv, "opening")
            parts.append(f"{o.get('eco', '')} {o['name']}".strip() + ".")
        text = next(iter((_g(mv, "annotations") or {}).values()), "")
        lt = _g(mv, "left_theory") or {}
        if lt and not text:                     # the coach's comment already says so
            parts.append("Leaves the named opening lines." if lt.get("source") == "names"
                         else "Leaves known opening theory.")
        if text:
            parts.append(plain_comment(text))
        tac = _g(mv, "tactics") or {}
        if tac.get("allowed"):
            parts.append("Allowed " + ", ".join(MOTIFS.get(t, t) for t in tac["allowed"]) + ".")
        prior = re.sub(r"\s*\[%eval [^\]]*\]\s*", " ", node.comment).strip() if own else ""
        if parts:
            node.comment = (f"{prior} " if prior else "") + f"{COACH} " + " ".join(parts)
        elif own:
            node.comment = prior
        # [%eval] goes into the comment, so it is set after the text
        if not board.is_game_over():
            sc = _score(_g(mv, "score_cp"), _g(mv, "score_mate"))
            if sc is not None:
                node.set_eval(sc)
        # The engine's better line, as a variation from the position before
        # (unless the annotator already gave that move as a variation).
        line = _g(mv, "best_line") or []
        if label in ERRORS and line and line[0] != _g(mv, "san"):
            try:
                first_move = before.parse_san(line[0])
            except ValueError:
                first_move = None
            if first_move is None or parent.has_variation(first_move):
                continue
            var, b = parent, before.copy()
            first = None
            for san in line:
                try:
                    m2 = b.parse_san(san)
                except ValueError:
                    break
                var = var.add_variation(m2)
                first = first or var
                b.push(m2)
            if first is not None:
                missed = [MOTIFS.get(t, t) for t in tac.get("missed", [])]
                first.comment = (f"{COACH} Stockfish "
                                 + _score_text(_g(mv, "best_cp"), _g(mv, "best_mate"))
                                 + (f"; {', '.join(missed)}" if missed else "")).strip()
                # [%eval] is the position after the move it follows
                mate, cp = _after_first(_g(mv, "best_mate"), _g(mv, "best_cp"),
                                        before.turn == chess.WHITE)
                sc = _score(cp, mate)
                if sc is not None:
                    first.set_eval(sc)
    game.headers["Result"] = headers.get("Result", game.headers.get("Result", "*"))
    return game


def annotated_pgn(review, source: Optional[chess.pgn.Game] = None) -> str:
    return str(annotated_game(review, source)) + "\n"
