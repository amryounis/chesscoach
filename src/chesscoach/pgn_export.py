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


def annotated_game(review) -> chess.pgn.Game:
    """`review`: a GameReview or its dict."""
    headers = dict(_g(review, "headers") or {})
    game = chess.pgn.Game()
    for k, v in headers.items():
        game.headers[k] = v
    start = chess.Board(_g(review, "start_fen") or chess.STARTING_FEN)
    if start.fen() != chess.STARTING_FEN:
        game.setup(start)
    models = _g(review, "models") or []
    game.headers["Annotator"] = f"chesscoach {__version__}" + (f" ({models[0]})" if models else "")
    moves = _g(review, "moves") or []
    named = [m for m in moves if _g(m, "opening")]
    if named:
        o = _g(named[-1], "opening")
        game.headers.setdefault("ECO", o.get("eco", ""))
        game.headers.setdefault("Opening", o.get("name", ""))

    intro = []
    acc = _g(review, "accuracy") or {}
    if acc.get("white") is not None or acc.get("black") is not None:
        intro.append("Accuracy: " + ", ".join(
            f"{c.capitalize()} {acc[c]:.0f}%" for c in ("white", "black") if acc.get(c) is not None)
            + " (Lichess's method).")
    intro += [c["text"] if isinstance(c, dict) else c.text
              for c in (_g(review, "summary") or []) if (_g(c, "kind") != "accuracy")]
    if intro:
        game.comment = " ".join(intro).replace("{", "(").replace("}", ")")

    node = game
    board = start.copy()
    for mv in moves:
        move = chess.Move.from_uci(_g(mv, "uci"))
        parent, before = node, board.copy()
        node = parent.add_variation(move)
        board.push(move)
        label, reason = _g(mv, "label", ""), _g(mv, "reason", "")
        if label in NAG_FOR:
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
        if parts:
            node.comment = " ".join(parts)
        # [%eval] goes into the comment, so it is set after the text
        if not board.is_game_over():
            sc = _score(_g(mv, "score_cp"), _g(mv, "score_mate"))
            if sc is not None:
                node.set_eval(sc)
        # The engine's better line, as a variation from the position before.
        line = _g(mv, "best_line") or []
        if label in ERRORS and line and line[0] != _g(mv, "san"):
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
                first.comment = ("Stockfish " + _score_text(_g(mv, "best_cp"), _g(mv, "best_mate"))
                                 + (f"; {', '.join(missed)}" if missed else "")).strip()
                sc = _score(_g(mv, "best_cp"), _g(mv, "best_mate"))
                if sc is not None:
                    first.set_eval(sc)
    game.headers["Result"] = headers.get("Result", "*")
    return game


def annotated_pgn(review) -> str:
    return str(annotated_game(review)) + "\n"
