"""
Phase 5: patterns across your games.

Each finished review is stored as one small JSON record: the game's
headers, which colour you played, and for each of your moves its label,
winning chances and themes (the stored facts, see themes.py). Patterns are
counted from these records only, by code, and every pattern names the
games and moves it rests on.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from . import config
from .summary import Claim, _get, _join, _n, phase_of
from .themes import ERRORS, OUTCOMES, TAGS


def history_dir() -> Path:
    return config.user_config_path().parent / "history"


def game_id(review) -> str:
    moves = " ".join(_get(m, "uci") for m in _get(review, "moves"))
    return hashlib.sha1((_get(review, "start_fen") + " " + moves).encode()).hexdigest()[:12]


def player_color(review, player_name: str = "") -> Optional[str]:
    """Which colour 'you' played: the reviewed side, or with both sides
    reviewed, the side whose name in the headers is yours."""
    side = _get(review, "side")
    if side in ("white", "black"):
        return side
    name = (player_name or "").strip().lower()
    h = _get(review, "headers") or {}
    if name:
        for color in ("white", "black"):
            if h.get(color.capitalize(), "").strip().lower() == name:
                return color
    return None


def record(review, player_name: str = "") -> Optional[dict]:
    color = player_color(review, player_name)
    if color is None:
        return None
    h = _get(review, "headers") or {}
    mine = [m for m in _get(review, "moves")
            if _get(m, "color") == color and not _get(m, "book")]
    return {
        "id": game_id(review), "color": color,
        "white": h.get("White", "?"), "black": h.get("Black", "?"),
        "date": h.get("Date", "?"), "result": h.get("Result", "?"),
        "moves": [{"ply": _get(m, "ply"), "move_label": _get(m, "move_label"),
                   "label": _get(m, "label"), "fen_before": _get(m, "fen_before"),
                   "win_before": _get(m, "win_before"), "win_after": _get(m, "win_after"),
                   "themes": _get(m, "themes") or []} for m in mine],
    }


def save(review, player_name: str = "", folder: Optional[Path] = None) -> Optional[Path]:
    """Store the review's record (a re-review of the same game replaces it)."""
    rec = record(review, player_name)
    if rec is None:
        return None
    folder = folder or history_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{rec['id']}.json"
    path.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return path


def load(folder: Optional[Path] = None) -> list:
    folder = folder or history_dir()
    out = []
    for p in sorted(folder.glob("*.json")) if folder.exists() else []:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    out.sort(key=lambda r: (r.get("date", ""), r["id"]))
    return out


def _count(k: int, word: str) -> str:
    if k == 1:
        return f"1 {word}"
    return f"{k} {word[:-1] + 'ies' if word.endswith('y') else word + 's'}"


def game_name(rec: dict) -> str:
    opp = rec["black"] if rec["color"] == "white" else rec["white"]
    return f"vs {opp} ({rec['date']})"


def patterns(records: list, last: int = 10) -> list:
    """[Claim] about your last `last` reviewed games. Claim.plies holds
    {"game", "ply", "move"} for every move a pattern rests on."""
    recs = records[-last:]
    if not recs:
        return []
    n = len(recs)
    out = []
    errors = [(r, m) for r in recs for m in r["moves"] if m["label"] in ERRORS]
    ref = lambda r, m: {"game": r["id"], "ply": m["ply"], "move": m["move_label"]}
    counts = [sum(m["label"] == lab for _, m in errors) for lab in ERRORS]
    scope = ("In your one reviewed game" if n == 1 else
             f"In your last {_n(n)} reviewed games")
    out.append(Claim(
        f"{scope}: {_count(len(errors), 'error')} ("
        + ", ".join(_count(k, lab.lower()) for k, lab in zip(counts, ERRORS)) + ").",
        "pattern-counts", "", [ref(r, m) for r, m in errors],
        [f"{game_name(r)} {m['move_label']}: {m['label']}" for r, m in errors]))

    by_tag = {}
    for r, m in errors:
        for t in m["themes"]:
            by_tag.setdefault(t["tag"], []).append((r, m, t))
    for tag in sorted(by_tag, key=lambda t: (-len({r["id"] for r, _, _ in by_tag[t]}),
                                             list(TAGS).index(t))):
        items = by_tag[tag]
        games = []
        for r, _, _ in items:
            if r["id"] not in [g["id"] for g in games]:
                games.append(r)
        if len(games) < 2 or tag in OUTCOMES:
            continue                       # once is a moment, not a pattern
        out.append(Claim(
            f"In {_n(len(games))} of your last {_n(n)} games, a move "
            f"{TAGS[tag]} ({len(items)} moves in all): "
            + _join([game_name(g) + " " + ", ".join(m["move_label"] for r, m, _ in items
                                                     if r["id"] == g["id"])
                     for g in games]) + ".",
            "pattern-theme", "", [ref(r, m) for r, m, _ in items],
            [f"{game_name(r)} {m['move_label']}: {t['fact']}" for r, m, t in items],
            tag=tag))

    if len(errors) >= 5:
        by_phase = {}
        for r, m in errors:
            by_phase.setdefault(phase_of(m["fen_before"]), []).append((r, m))
        top = max(by_phase, key=lambda p: len(by_phase[p]))
        if len(by_phase[top]) * 2 > len(errors):
            out.append(Claim(
                f"{_n(len(by_phase[top])).capitalize()} of these {len(errors)} errors "
                f"came in the {top}.",
                "pattern-phase", "", [ref(r, m) for r, m in by_phase[top]],
                [f"{game_name(r)} {m['move_label']}: {top}" for r, m in by_phase[top]]))
    return out


def verify_patterns(records: list, claims: list) -> list:
    """Every move a pattern names must be stored, an error, and (for a
    theme) carry that theme."""
    moves = {(r["id"], m["ply"]): m for r in records for m in r["moves"]}
    problems = []
    for c in claims:
        c = c if isinstance(c, dict) else c.to_dict()
        for p in c["plies"]:
            m = moves.get((p["game"], p["ply"]))
            if m is None:
                problems.append(f"{c['text']} — no stored move {p['move']}")
            elif m["label"] not in ERRORS:
                problems.append(f"{c['text']} — {p['move']} is not an error")
            elif c["tag"] and not any(t["tag"] == c["tag"] for t in m["themes"]):
                problems.append(f"{c['text']} — {p['move']} has no '{c['tag']}' fact")
    return problems
