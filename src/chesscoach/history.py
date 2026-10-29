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
import os
from pathlib import Path
from typing import Optional

import chess

from . import config
from .summary import Claim, _get, _join, _n, phase_of
from .motifs import MOTIFS
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
        "accuracy": ((review.get("accuracy") if isinstance(review, dict)
                      else getattr(review, "accuracy", None)) or {}).get(color),
        "moves": [_move_record(m) for m in mine],
    }


def _move_record(m) -> dict:
    rec = {"ply": _get(m, "ply"), "move_label": _get(m, "move_label"),
           "color": _get(m, "color"), "san": _get(m, "san"), "uci": _get(m, "uci"),
           "label": _get(m, "label"), "fen_before": _get(m, "fen_before"),
           "best_san": _get(m, "best_san"), "best_uci": _get(m, "best_uci"),
           "win_before": _get(m, "win_before"), "win_after": _get(m, "win_after"),
           "themes": _get(m, "themes") or [],
           "tactics": _get(m, "tactics") or {}}
    if _get(m, "label") in ERRORS:
        # what the coach said, so the move can be practised later (practice.py)
        from .practice import explanation_of
        rec["explanation"] = explanation_of(m)
    return rec


SCHEMA = 2      # 1: before 0.12 (no engine move); 2: adds san/uci/best/explanation


def save(review, player_name: str = "", folder: Optional[Path] = None) -> Optional[Path]:
    """Store the review's record (a re-review of the same game replaces it)."""
    rec = record(review, player_name)
    if rec is None:
        return None
    rec["v"] = SCHEMA
    folder = folder or history_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{rec['id']}.json"
    # Write a temporary file and swap it in, so a crash or a full disk can
    # never leave half a record behind.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return path


def clean(rec) -> Optional[dict]:
    """A stored record made safe to count, or None if it isn't a game
    record at all. Old or hand-edited records lose only the parts that are
    malformed (a move without a label, a theme this version doesn't know),
    so one bad file can't stop the patterns, or a review, from finishing."""
    if not isinstance(rec, dict) or not isinstance(rec.get("id"), str) \
            or not isinstance(rec.get("moves"), list):
        return None
    out = dict(rec)
    for k, default in (("color", "white"), ("white", "?"), ("black", "?"),
                       ("date", "?"), ("result", "?")):
        if not isinstance(out.get(k), str):
            out[k] = default
    if out["color"] not in ("white", "black"):
        return None
    moves = []
    for m in rec["moves"]:
        if not isinstance(m, dict) or not isinstance(m.get("label"), str) \
                or not isinstance(m.get("move_label"), str) or not isinstance(m.get("ply"), int):
            continue
        m = dict(m)
        try:
            chess.Board(m.get("fen_before") or "")
        except (ValueError, TypeError):
            continue
        m["themes"] = [t for t in (m.get("themes") or [])
                       if isinstance(t, dict) and t.get("tag") in TAGS
                       and isinstance(t.get("fact", ""), str)]
        tac = m.get("tactics") if isinstance(m.get("tactics"), dict) else {}
        m["tactics"] = {k: [t for t in v if t in MOTIFS] for k, v in tac.items()
                        if k in ("allowed", "missed") and isinstance(v, list)}
        moves.append(m)
    out["moves"] = moves
    return out


def load(folder: Optional[Path] = None) -> list:
    folder = folder or history_dir()
    out = []
    for p in sorted(folder.glob("*.json")) if folder.exists() else []:
        try:
            rec = clean(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        # Only game records: anything else in the folder (0.12.0 kept
        # practice.json here) is not a game and must not break patterns.
        if rec is not None:
            out.append(rec)
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

    # Tactics across games (Lucas Chess's "statistics on themes"): which
    # motifs your errors let in, and which you missed, in two games or more.
    for kind, lead in (("allowed", "your errors allowed"), ("missed", "you missed")):
        by_motif = {}
        for r, m in errors:
            for t in (m.get("tactics") or {}).get(kind, []):
                by_motif.setdefault(t, []).append((r, m))
        common = [t for t in by_motif if len({r["id"] for r, _ in by_motif[t]}) >= 2]
        if not common:
            continue
        common.sort(key=lambda t: (-len(by_motif[t]), list(MOTIFS).index(t)))
        parts = [f"{MOTIFS[t]} {_count(len(by_motif[t]), 'time')} in "
                 f"{_n(len({r['id'] for r, _ in by_motif[t]}))} games" for t in common[:3]]
        items = [(r, m, t) for t in common[:3] for r, m in by_motif[t]]
        out.append(Claim(
            f"Across your last {_n(n)} games, {lead} {_join(parts)}.",
            "pattern-tactics", "", [ref(r, m) for r, m, _ in items],
            [f"{game_name(r)} {m['move_label']}: {kind} {MOTIFS[t]}" for r, m, t in items],
            tag=kind))

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
            elif c["kind"] == "pattern-tactics":
                if not (m.get("tactics") or {}).get(c["tag"]):
                    problems.append(f"{c['text']} — {p['move']} has no '{c['tag']}' tactics")
            elif c["tag"] and not any(t["tag"] == c["tag"] for t in m["themes"]):
                problems.append(f"{c['text']} — {p['move']} has no '{c['tag']}' fact")
    return problems
