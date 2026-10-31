"""
Practice: replay your own mistakes.

A review says what went wrong; practice asks you again. Every Inaccuracy,
Mistake or Blunder you made in a reviewed game becomes a drill (blunders
first, since they cost the most): the position before
the move, "what would you play?", then the engine's move, its line and the
coach's comment from the review.

Answers are judged by Stockfish, not by matching one move: a different move
that keeps your winning chances is accepted (and said to be so). The
judgment uses the same win% thresholds as the review's labels, so "that
would also be a mistake" means exactly what it means in a review.

Attempts are stored next to the review history (practice.json), so drills
you keep missing come back first and ones you have solved move to the end.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Optional

import chess
import chess.engine

from . import config
from .assessment import label_for_drop, win_pct
from .engine import run_stockfish

DRILL_LABELS = ("Inaccuracy", "Mistake", "Blunder")
_SEVERITY = {"Blunder": 0, "Mistake": 1, "Inaccuracy": 2}
SOLVED = ("best", "good")
LINE_PLIES = 6                     # how much of the engine's line to show


def article(label: str) -> str:
    """'an inaccuracy', 'a mistake', 'a blunder'."""
    word = label.lower()
    return ("an " if word[:1] in "aeiou" else "a ") + word


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def explanation_of(mv) -> str:
    """The coach's comment on a move from the review (first model's), "" if none."""
    # Since 0.13 errors are kept apart from the comments; reviews made
    # before that stored "Failed to connect to Ollama..." as the comment.
    for text in (_get(mv, "annotations") or {}).values():
        if text and not looks_like_error(str(text)):
            return text
    return ""


def looks_like_error(text: str) -> bool:
    t = text.lower()
    return (t.startswith("error") or "ollama" in t or "(is '" in t
            or "connection" in t and "refused" in t or "not found" in t and "model" in t)


def make_drill(mv, game: str, game_name: str = "") -> Optional[dict]:
    """A drill from one reviewed move (a MoveReview, its dict, or a history
    record move); None when it isn't a mistake or lacks the engine's move."""
    if _get(mv, "label") not in DRILL_LABELS or not _get(mv, "best_uci") \
            or not _get(mv, "fen_before"):
        return None
    return {"id": f"{game}-{_get(mv, 'ply')}", "game": game, "game_name": game_name,
            "ply": _get(mv, "ply"), "move_label": _get(mv, "move_label"),
            "fen": _get(mv, "fen_before"), "color": _get(mv, "color"),
            "label": _get(mv, "label"),
            "played_san": _get(mv, "san", ""), "played_uci": _get(mv, "uci", ""),
            "best_san": _get(mv, "best_san", ""), "best_uci": _get(mv, "best_uci"),
            "win_before": _get(mv, "win_before"), "win_after": _get(mv, "win_after"),
            "explanation": _get(mv, "explanation") or explanation_of(mv),
            "themes": [t.get("tag", "") for t in (_get(mv, "themes") or [])
                       if isinstance(t, dict)]}


def drills_from_review(review, color: Optional[str] = None) -> list:
    """Drills from a finished review: the mistakes of `color` ("white" /
    "black"), or of the reviewed side; with both sides reviewed and no
    colour known, both sides' mistakes."""
    from .history import game_id, game_name, player_color
    color = color or player_color(review, config.settings.player_name)
    h = _get(review, "headers") or {}
    gid = game_id(review)
    name = game_name({"color": color or "white", "white": h.get("White", "?"),
                      "black": h.get("Black", "?"), "date": h.get("Date", "?")})
    out = []
    for mv in _get(review, "moves"):
        if color and _get(mv, "color") != color:
            continue
        d = make_drill(mv, gid, name if color else "")
        if d:
            out.append(d)
    return out


def drills_from_history(records: list) -> list:
    """Drills from every reviewed game in the history (records written
    before practice existed don't have the engine's move and are skipped)."""
    from .history import game_name
    return [d for r in records for m in r["moves"]
            if (d := make_drill(m, r["id"], game_name(r)))]


# --- attempts ---------------------------------------------------------------

def progress_path() -> Path:
    """practice.json beside the history folder, not in it: the history
    folder holds one record per game. (0.12.0 put it inside; it is moved.)"""
    from .history import history_dir
    path = history_dir().parent / "practice.json"
    old = history_dir() / "practice.json"
    if old.exists() and not path.exists():
        try:
            old.replace(path)
        except OSError:
            return old
    return path


def load_progress(path: Optional[Path] = None) -> dict:
    path = path or progress_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    # keep only well-formed entries: one bad line must not lose the rest
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, dict)
            and isinstance(v.get("tries", 0), int) and isinstance(v.get("solved", 0), int)}


def record_attempt(drill_id: str, verdict: str, path: Optional[Path] = None) -> dict:
    """Store one attempt; returns that drill's progress."""
    path = path or progress_path()
    prog = load_progress(path)
    p = prog.setdefault(drill_id, {"tries": 0, "solved": 0, "last": "", "last_verdict": ""})
    p["tries"] += 1
    p["solved"] += verdict in SOLVED
    p["last"] = datetime.datetime.now().isoformat(timespec="seconds")
    p["last_verdict"] = verdict
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")       # written whole, then swapped in
        tmp.write_text(json.dumps(prog, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass
    return p


def queue_tag(progress: dict) -> str:
    """Why a drill sits where it does in the cross-game queue (order())."""
    if not progress or not progress.get("tries"):
        return "New"
    if progress.get("last_verdict") == "hinted":
        return "Solved with hints last time"
    if progress.get("last_verdict") not in SOLVED:
        return "Missed last time"
    n = progress.get("solved", 0)
    return "Solved once" if n == 1 else f"Solved {n} times"


def order(drills: list, progress: dict) -> list:
    """Not yet solved (never tried, or missed last time) first, then the
    ones solved least often, oldest attempt first; within that, blunders
    before mistakes before inaccuracies."""
    def key(d):
        p = progress.get(d["id"], {})
        missed_last = p.get("last_verdict") not in SOLVED if p else True
        return (not missed_last, p.get("solved", 0), p.get("last", ""),
                _SEVERITY.get(d["label"], 3))
    return sorted(drills, key=key)


# --- judging an answer --------------------------------------------------------

def engine_line(drill: dict, engine=None) -> list:
    """The engine's line (SAN) from the drill position, starting with the
    review's best move even if a fresh search would now prefer another."""
    board = chess.Board(drill["fen"])
    line = run_stockfish(board, multipv=1, engine=engine).best_line
    best = drill["best_san"]
    if best and (not line or line[0] != best):
        after = board.copy()
        after.push_san(best)
        line = [best] + (run_stockfish(after, multipv=1, engine=engine).best_line
                         if not after.is_game_over() else [])
    return line[:LINE_PLIES]


def judge(drill: dict, answer_uci: Optional[str], engine=None) -> dict:
    """
    Judge an answer (None = show me). Returns
    {verdict, answer_san, label, win_after, drop, best_san, best_uci,
     line, message, explanation}. verdict: "best" (the engine's move),
    "good" (another move that keeps the winning chances, Excellent/Good),
    "repeat" (the move from the game), "miss" (another move that loses
    winning chances), "revealed".
    """
    board = chess.Board(drill["fen"])
    me_white = board.turn == chess.WHITE
    best = drill["best_san"]
    out = {"best_san": best, "best_uci": drill["best_uci"], "answer_san": "",
           "label": "", "win_after": None, "drop": None,
           "explanation": drill.get("explanation", "")}
    own = engine is None
    if own:
        config.settings.require_stockfish()
        engine = chess.engine.SimpleEngine.popen_uci(config.settings.stockfish_path)
    try:
        if answer_uci is None:
            out.update(verdict="revealed",
                       message=f"The engine's move was {best}.")
        else:
            move = chess.Move.from_uci(answer_uci)
            if move not in board.legal_moves:
                raise ValueError(f"Illegal move: {answer_uci}")
            san = board.san(move)
            out["answer_san"] = san
            if answer_uci == drill["best_uci"]:
                out.update(verdict="best", label="Excellent",
                           message=f"Yes: {best} is the engine's move.")
            elif answer_uci == drill["played_uci"]:
                out.update(verdict="repeat", label=drill["label"],
                           message=(f"{san} is the move you played in the game, "
                                    f"{article(drill['label'])}. The engine's move was {best}."))
            else:
                after = board.copy()
                after.push(move)
                if after.is_checkmate():
                    out.update(verdict="best", label="Excellent", win_after=100.0, drop=0.0,
                               message=f"{san} is checkmate.")
                else:
                    ea = run_stockfish(after, multipv=1, engine=engine)
                    w1 = win_pct(ea.score_cp, ea.score_mate, me_white)
                    w0 = drill.get("win_before")
                    if w0 is None:            # the review couldn't score it; do it now
                        e0 = run_stockfish(board, multipv=1, engine=engine)
                        w0 = win_pct(e0.score_cp, e0.score_mate, me_white)
                    drop = max(0.0, (w0 or 0) - (w1 or 0))
                    label = label_for_drop(drop)
                    out.update(label=label, win_after=w1, drop=round(drop, 1))
                    if label in ("Excellent", "Good"):
                        out.update(verdict="good", message=(
                            f"{san} also works: your winning chances stay about where "
                            f"they were ({w1:.0f}%). The engine's first choice was {best}."))
                    else:
                        out.update(verdict="miss", message=(
                            f"{san} would be {article(label)}: your winning chances go from "
                            f"{w0:.0f}% to {w1:.0f}%. The engine's move was {best}."))
        out["line"] = engine_line(drill, engine=engine)
    finally:
        if own:
            engine.quit()
    return out
