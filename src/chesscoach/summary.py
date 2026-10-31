"""
Phase 5: the game summary, written by code from stored facts.

Every claim carries the moves it rests on and the stored facts behind it
(labels, winning chances, themes), so it can be traced and re-checked:
`verify()` rebuilds each claim's numbers from the review and reports any
that do not match. No language model is involved: the phase 4 runs showed
qwen3:14b drops specifics when it summarises several moves ("weakened its
position"), and a summary is nothing but specifics.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

import chess

from .motifs import MOTIFS
from .themes import ERRORS, TAGS

_WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight",
          "nine", "ten"]
PHASES = ("opening", "middlegame", "endgame")
SWING = 15          # points of winning chance: a move that lost this much is a turning point


@dataclass
class Claim:
    text: str
    kind: str                         # counts | turning | theme | phase | good
    color: str                        # "white" / "black"
    plies: list = field(default_factory=list)      # the moves it rests on
    facts: list = field(default_factory=list)      # the stored facts behind it
    tag: str = ""                                  # theme claims

    def to_dict(self) -> dict:
        return asdict(self)


def _n(k: int) -> str:
    return _WORDS[k] if k < len(_WORDS) else str(k)


def _plural(k: int, word: str) -> str:
    if word.endswith("y") and k != 1:
        word = word[:-1] + "ie"
    return f"{_n(k)} {word}{'' if k == 1 else 's'}"


def _join(items: list) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _get(m, key):
    return m.get(key) if isinstance(m, dict) else getattr(m, key, None)


def phase_of(fen: str) -> str:
    """Opening up to move 12; endgame when little material is left."""
    b = chess.Board(fen)
    vals = {chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
    pieces = sum(vals.get(p.piece_type, 0) for p in b.piece_map().values())
    if pieces <= 26:
        return "endgame"
    if b.fullmove_number <= 12:
        return "opening"
    return "middlegame"


def _pct(x: Optional[float]) -> str:
    return "?" if x is None else f"{x:.0f}%"


def _move_fact(m) -> str:
    return (f"{_get(m, 'move_label')}: {_get(m, 'label')}, winning chance "
            f"{_pct(_get(m, 'win_before'))} → {_pct(_get(m, 'win_after'))}")


def _voice(color: str, single: bool) -> tuple:
    """(subject, possessive): ('You', 'your') or ('White', "White's")."""
    if single:
        return "You", "your"
    name = color.capitalize()
    return name, f"{name}'s"


def summarize(review) -> list:
    """[Claim] for each annotated side. `review` is a GameReview or its dict."""
    side = _get(review, "side")
    moves = _get(review, "moves")
    acc = (review.get("accuracy") if isinstance(review, dict)
           else getattr(review, "accuracy", None)) or {}
    colors = ["white", "black"] if side == "both" else [side]
    out = []
    for color in colors:
        out += _side_claims(moves, color, single=len(colors) == 1, accuracy=acc.get(color))
    return out


def _side_claims(moves: list, color: str, single: bool,
                 accuracy: Optional[float] = None) -> list:
    who, whose = _voice(color, single)
    mine = [m for m in moves if _get(m, "color") == color and not _get(m, "book")]
    errors = [m for m in mine if _get(m, "label") in ERRORS]
    out = []

    # 1. How many errors, by kind.
    counts = {lab: [m for m in errors if _get(m, "label") == lab] for lab in ERRORS}
    parts = [_plural(len(counts[lab]), lab.lower()) for lab in ERRORS]
    booked = any(_get(m, "book") for m in moves if _get(m, "color") == color)
    text = (f"{who} made {_join(parts)} in {_plural(len(mine), 'move')}"
            + (" after the opening book." if booked else "."))
    out.append(Claim(text, "counts", color, [_get(m, "ply") for m in errors],
                     [_move_fact(m) for m in errors]))

    # 1b. How close to the engine: agreement and the average cost per move,
    #     and mates missed or walked into.
    rated = [m for m in mine if _get(m, "best_san")]
    if rated:
        agree = [m for m in rated if _get(m, "san") == _get(m, "best_san")]
        losses = [max(0.0, _get(m, "win_before") - _get(m, "win_after")) for m in rated
                  if _get(m, "win_before") is not None and _get(m, "win_after") is not None]
        avg = sum(losses) / len(losses) if losses else 0.0
        lead = (f"{whose.capitalize()} accuracy was {accuracy:.0f}% (Lichess's method). "
                if accuracy is not None else "")
        out.append(Claim(
            f"{lead}{who} played the engine's first choice in {len(agree)} of "
            f"{len(rated)} moves ({100 * len(agree) / len(rated):.0f}%); on average "
            f"a move cost {avg:.1f} points of winning chance.",
            "accuracy", color, [_get(m, "ply") for m in agree],
            [f"{_get(m, 'move_label')}: the engine's first choice" for m in agree]
            + [f"average loss over {len(losses)} moves: {avg:.1f} points"]))
    missed = [m for m in mine if (_get(m, "mate_before") or 0) > 0
              and not (_get(m, "mate_after") or 0) > 0]
    if missed:
        out.append(Claim(
            f"{who} missed a forced mate with {_join([_get(m, 'move_label') for m in missed])}.",
            "mate", color, [_get(m, "ply") for m in missed],
            [f"{_get(m, 'move_label')}: mate in {_get(m, 'mate_before')} before the move, "
             f"none after it" for m in missed]))
    walked = [m for m in mine if (_get(m, "mate_after") or 0) < 0
              and not (_get(m, "mate_before") or 0) < 0]
    if walked:
        out.append(Claim(
            f"{_join([_get(m, 'move_label') for m in walked])} allowed a forced mate.",
            "mate", color, [_get(m, "ply") for m in walked],
            [f"{_get(m, 'move_label')}: the opponent mates in {-_get(m, 'mate_after')} "
             f"after the move" for m in walked]))

    # 2. The turning point: the move that cost the most.
    def drop(m):
        a, b = _get(m, "win_before"), _get(m, "win_after")
        return (a - b) if a is not None and b is not None else 0
    worst = max(mine, key=drop, default=None)
    if worst is not None and drop(worst) >= SWING:
        lab = _get(worst, "label").lower()
        art = "an" if lab[0] in "aeiou" else "a"
        out.append(Claim(
            f"The biggest swing was {_get(worst, 'move_label')} ({art} {lab}): "
            f"{whose} winning chance went from {_pct(_get(worst, 'win_before'))} to "
            f"{_pct(_get(worst, 'win_after'))}.",
            "turning", color, [_get(worst, "ply")], [_move_fact(worst)]))

    # 3. Themes: what the errors got wrong, most frequent first.
    by_tag = {}
    for m in errors:
        for t in _get(m, "themes") or []:
            by_tag.setdefault(t["tag"], []).append((m, t))
    order = sorted(by_tag, key=lambda tag: (-len(by_tag[tag]), list(TAGS).index(tag)))
    for tag in order:
        items = by_tag[tag]
        phrase = TAGS[tag] if single else (TAGS[tag].replace("your", whose)
                                             .replace(" you ", f" {who} "))
        labels = _join([_get(m, "move_label") for m, _ in items])
        if len(items) >= 2:
            of = (f"{whose} errors" if len(items) == len(errors) else
                  f"{whose} {_n(len(errors))} errors")
            text = f"{_n(len(items)).capitalize()} of {of} {phrase}: {labels}."
        else:
            text = f"{labels} {phrase}."
        out.append(Claim(text, "theme", color, [_get(m, "ply") for m, _ in items],
                         [f"{_get(m, 'move_label')}: {t['fact']}" for m, t in items],
                         tag=tag))

    # 3b. Tactics: what the errors allowed and what they missed (motifs
    #     found by code in the engine's lines; see motifs.py).
    for kind, verb in (("allowed", "allowed"), ("missed", "missed")):
        by_motif = {}
        for m in errors:
            for t in (_get(m, "tactics") or {}).get(kind, []):
                if t in MOTIFS:
                    by_motif.setdefault(t, []).append(m)
        if not by_motif:
            continue
        top = sorted(by_motif, key=lambda t: (-len(by_motif[t]), list(MOTIFS).index(t)))[:3]
        parts = [f"{MOTIFS[t]} ({_join([_get(m, 'move_label') for m in by_motif[t]])})"
                 for t in top]
        if kind == "allowed":
            subject = f"{whose.capitalize()} errors" if not single else "Your errors"
            text = f"{subject} allowed {_join(parts)}."
        else:
            text = f"{who} missed {_join(parts)}."          # "You missed a fork (3. Qh5)."
        plies = sorted({_get(m, "ply") for t in top for m in by_motif[t]})
        out.append(Claim(text, "tactics", color, plies,
                         [f"{_get(m, 'move_label')}: {verb} {MOTIFS[t]}" for t in top
                          for m in by_motif[t]], tag=kind))

    # 3c. How hard the errors were to avoid (difficulty.py): the ones in
    #     plain sight are the ones to train first.
    for level, said in (("plain", "in plain sight (a two-move look already shows the better move)"),
                        ("deep", "hard to see (the better move only proves itself several moves deep)")):
        these = [m for m in errors if (_get(m, "difficulty") or {}).get("level") == level]
        if not these:
            continue
        labels = _join([_get(m, "move_label") for m in these])
        if len(these) == 1:
            text = f"{labels} was {said}."
        else:
            of = (f"{whose} errors" if len(these) == len(errors) else
                  f"{whose} {_n(len(errors))} errors")
            text = f"{_n(len(these)).capitalize()} of {of} were {said}: {labels}."
        out.append(Claim(text, "difficulty", color, [_get(m, "ply") for m in these],
                         [f"{_get(m, 'move_label')}: {(_get(m, 'difficulty') or {}).get('text', '')}"
                          for m in these], tag=level))

    # 4. Where the errors came.
    if len(errors) >= 3:
        by_phase = {p: [m for m in errors if phase_of(_get(m, "fen_before")) == p]
                    for p in PHASES}
        top = max(PHASES, key=lambda p: len(by_phase[p]))
        k = len(by_phase[top])
        if k * 2 > len(errors):
            lead = (f"All {_n(k)} of {whose} errors" if k == len(errors) else
                    f"{_n(k).capitalize()} of {whose} {_n(len(errors))} errors")
            out.append(Claim(
                f"{lead} came in the {top}: "
                f"{_join([_get(m, 'move_label') for m in by_phase[top]])}.",
                "phase", color, [_get(m, "ply") for m in by_phase[top]],
                [f"{_get(m, 'move_label')}: {top}" for m in by_phase[top]]))

    # 5. What went right.
    for reason, words in (("found the only good move", "found the only good move"),
                          ("punished the opponent's mistake",
                           "punished the opponent's mistake")):
        good = [m for m in mine if _get(m, "reason") == reason]
        if good:
            out.append(Claim(
                f"{who} {words} with {_join([_get(m, 'move_label') for m in good])}.",
                "good", color, [_get(m, "ply") for m in good],
                [f"{_get(m, 'move_label')}: {reason}" for m in good]))
    return out


def verify(review) -> list:
    """Rebuild every claim from the stored moves; return what does not match
    (empty = every claim traces back to a stored fact)."""
    moves = {(_get(m, "ply")): m for m in _get(review, "moves")}
    problems = []
    for c in _get(review, "summary") or []:
        c = c if isinstance(c, dict) else c.to_dict()
        for p in c["plies"]:
            m = moves.get(p)
            if m is None:
                problems.append(f"{c['text']} — no move {p}")
                continue
            if m and _get(m, "color") != c["color"]:
                problems.append(f"{c['text']} — {_get(m, 'move_label')} is not {c['color']}'s")
            if _get(m, "move_label") not in c["text"] and c["kind"] not in ("counts", "accuracy"):
                problems.append(f"{c['text']} — does not name {_get(m, 'move_label')}")
            if c["kind"] == "theme" and not any(
                    t["tag"] == c["tag"] for t in _get(m, "themes") or []):
                problems.append(f"{c['text']} — {_get(m, 'move_label')} has no "
                                f"'{c['tag']}' fact")
            if c["kind"] in ("counts", "theme", "phase", "difficulty") \
                    and _get(m, "label") not in ERRORS:
                problems.append(f"{c['text']} — {_get(m, 'move_label')} is not an error")
            if c["kind"] == "turning":
                for x in (_get(m, "win_before"), _get(m, "win_after")):
                    if _pct(x) not in c["text"]:
                        problems.append(f"{c['text']} — winning chance {_pct(x)} not stated")
        if c["kind"] == "tactics":
            for p in c["plies"]:
                m = moves.get(p)
                if m and not (_get(m, "tactics") or {}).get(c["tag"]):
                    problems.append(f"{c['text']} — {_get(m, 'move_label')} has no "
                                    f"'{c['tag']}' tactics stored")
        if c["kind"] == "difficulty":
            for p in c["plies"]:
                m = moves.get(p)
                if m and (_get(m, "difficulty") or {}).get("level") != c["tag"]:
                    problems.append(f"{c['text']} — {_get(m, 'move_label')} is not stored "
                                    f"as '{c['tag']}'")
        if c["kind"] == "accuracy":
            acc = ((review.get("accuracy") if isinstance(review, dict)
                    else getattr(review, "accuracy", None)) or {}).get(c["color"])
            if acc is not None and f"accuracy was {acc:.0f}%" not in c["text"]:
                problems.append(f"{c['text']} — stored accuracy is {acc:.0f}%")
            agree = [m for m in moves.values() if _get(m, "color") == c["color"]
                     and not _get(m, "book") and _get(m, "best_san")
                     and _get(m, "san") == _get(m, "best_san")]
            if sorted(_get(m, "ply") for m in agree) != sorted(c["plies"]):
                problems.append(f"{c['text']} — the agreeing moves stored do not match")
        if c["kind"] == "mate":
            for p in c["plies"]:
                m = moves.get(p)
                if m and not ((_get(m, "mate_before") or 0) > 0 or (_get(m, "mate_after") or 0) < 0):
                    problems.append(f"{c['text']} — {_get(m, 'move_label')} has no mate stored")
        if c["kind"] == "counts":
            errs = [m for m in moves.values() if _get(m, "color") == c["color"]
                    and not _get(m, "book") and _get(m, "label") in ERRORS]
            if sorted(_get(m, "ply") for m in errs) != sorted(c["plies"]):
                problems.append(f"{c['text']} — the errors stored do not match")
            for lab in ERRORS:
                k = sum(_get(m, "label") == lab for m in errs)
                if _plural(k, lab.lower()) not in c["text"]:
                    problems.append(f"{c['text']} — should say {_plural(k, lab.lower())}")
    return problems
