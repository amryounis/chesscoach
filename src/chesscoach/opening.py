"""Opening-book detection (Polyglot .bin books)."""

from __future__ import annotations

import os

import chess
import chess.polyglot


def book_status(book_path: str, boards: list, moves: list) -> list:
    """
    For each ply: True if it's known opening theory. A move counts as book
    if the move itself is in the book OR the position it reaches is (the
    book is pruned to the main moves, and positions catch transpositions).
    Once a move leaves the book, everything after it is out of book.
    """
    status = [False] * len(moves)
    if not book_path or not os.path.exists(book_path):
        return status
    with chess.polyglot.open_reader(book_path) as r:
        for i, m in enumerate(moves):
            here = list(r.find_all(boards[i], minimum_weight=0))
            nxt = list(r.find_all(boards[i + 1], minimum_weight=0))
            if any(e.move == m for e in here) or nxt:
                status[i] = True
            else:
                break
    return status


# --- Opening names and ideas (phase 7f) -----------------------------------
#
# Names come from the Lichess opening tables (public domain), looked up by
# position, so transpositions are named correctly. The one-line ideas are
# general statements written once per opening (data/opening_ideas.toml);
# none of them claims anything about a particular position.

import functools
import gzip
import json
from importlib import resources
from typing import Optional


@functools.lru_cache(maxsize=1)
def _table() -> dict:
    with resources.files("chesscoach").joinpath("data/openings.json.gz").open("rb") as f:
        return json.loads(gzip.decompress(f.read()))


@functools.lru_cache(maxsize=1)
def _ideas() -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:                      # Python < 3.11
        import tomli as tomllib
    text = resources.files("chesscoach").joinpath("data/opening_ideas.toml").read_text("utf-8")
    return tomllib.loads(text)


def name_at(board: chess.Board) -> Optional[tuple]:
    """(ECO, name) when this exact position is a named opening position."""
    hit = _table().get(board.epd())
    return (hit[0], hit[1]) if hit else None


def idea_for(name: str) -> tuple:
    """(key, idea) for the most specific opening idea that `name` falls
    under ("Sicilian Defense: Dragon Variation, Yugoslav Attack" → the
    Dragon idea); ("", "") if none."""
    best = ""
    for key in _ideas():
        if (name == key or name.startswith(key + ":") or name.startswith(key + ",")) \
                and len(key) > len(best):
            best = key
    return (best, _ideas()[best]) if best else ("", "")


def _named_after(board: chess.Board, move: chess.Move) -> Optional[tuple]:
    b = board.copy(stack=False)
    b.push(move)
    return name_at(b)


def theory_alternatives(board: chess.Board, played: chess.Move, book_path: str = "",
                        n: int = 2) -> list:
    """The moves that would have stayed in theory where `played` left it:
    [(san, name or "")], most played first. From the Polyglot book when one
    is set (by weight), else every move reaching a named position."""
    out = []
    if book_path and os.path.exists(book_path):
        with chess.polyglot.open_reader(book_path) as r:
            entries = sorted(r.find_all(board), key=lambda e: -e.weight)
        for e in entries:
            if e.move != played and e.move in board.legal_moves:
                named = _named_after(board, e.move)
                out.append((board.san(e.move), named[1] if named else ""))
    else:
        for mv in board.legal_moves:
            if mv == played:
                continue
            named = _named_after(board, mv)
            if named:
                out.append((board.san(mv), named[1]))
        out.sort(key=lambda x: (len(x[1]), x[0]))
    return out[:n]


def theory_exit(boards: list, moves: list, book: Optional[list] = None) -> dict:
    """Where the game leaves opening theory, claimed only on evidence.

    {"ply": i, "source": "book" | "names", "opening": name} or {} when the
    game never provably leaves theory.
      - "book": the Polyglot book knew at least the first move; the first
        ply it doesn't know leaves theory (all known -> no exit).
      - "names": no book evidence, so the only evidence is the openings
        table; the ply after the last one that reached a named position
        leaves the named lines.
      - nothing named and no book hit: nothing is claimed. An empty or
        missing book is NOT evidence that move 1 left theory.
    The moment picker, the move notes and the reports all use this one
    rule, so they can never disagree about where theory ended."""
    named = [name_at(boards[i + 1]) for i in range(len(moves))]
    if book and any(book):
        ply = next((i for i, b in enumerate(book) if not b), None)
        source = "book"
    else:
        last = max((i for i, hit in enumerate(named) if hit), default=None)
        ply = None if last is None or last + 1 >= len(moves) else last + 1
        source = "names"
    if ply is None:
        return {}
    name_then = next((named[j][1] for j in range(ply - 1, -1, -1) if named[j]), "")
    if source == "names" and not name_then:
        return {}
    return {"ply": ply, "source": source, "opening": name_then}


def opening_notes(boards: list, moves: list, book: Optional[list] = None,
                  book_path: str = "") -> list:
    """Per ply: {"opening": {eco, name, idea}} when the move reaches a newly
    named opening position (the idea only the first time its key appears),
    and {"left_theory": {"alternatives": [{san, name}], "opening": name,
    "source": "book" | "names"}} on the move that leaves theory, as decided
    by theory_exit()."""
    out = [{} for _ in moves]
    current, seen_ideas = None, set()
    named = [name_at(boards[i + 1]) for i in range(len(moves))]
    for i, hit in enumerate(named):
        # A transposition back to a broader name ("Sicilian Defense" after
        # "Sicilian Defense: Modern Variations") is not news.
        if hit and (current is None or not current[1].startswith(hit[1])):
            key, idea = idea_for(hit[1])
            rec = {"eco": hit[0], "name": hit[1]}
            if key and key not in seen_ideas:
                rec["idea"] = idea
                seen_ideas.add(key)
            out[i]["opening"] = rec
            current = hit
    exit_ = theory_exit(boards, moves, book)
    if exit_:
        leave = exit_["ply"]
        alts = theory_alternatives(boards[leave], moves[leave],
                                   book_path if exit_["source"] == "book" else "")
        out[leave]["left_theory"] = {"alternatives": [{"san": s, "name": nm} for s, nm in alts],
                                     "opening": exit_["opening"], "source": exit_["source"]}
    return out
