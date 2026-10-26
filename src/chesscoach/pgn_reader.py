"""
Phase 7a: annotated PGN games as data for the reader pane (and, in phase 8,
as answer keys).

`read_games()` lists the games in a file; `game_tree()` turns one game into
a flat list of nodes the page can draw: every move of the main line and of
every variation, with its comment (split by language when a comment holds
the same text in two languages, as ChessBase bilingual annotations do),
its symbols (!, ?!, ±), the annotator's arrows and coloured squares, and
the annotator's engine evaluation per main-line ply (ChessBase `%evp`).
"""

from __future__ import annotations

import io
import re
from typing import Optional

import chess
import chess.pgn

# Standard NAGs (numeric annotation glyphs) as shown in books.
NAGS = {
    1: "!", 2: "?", 3: "!!", 4: "??", 5: "!?", 6: "?!", 7: "□", 8: "□",
    10: "=", 13: "∞", 14: "⩲", 15: "⩱", 16: "±", 17: "∓", 18: "+−", 19: "−+",
    22: "⨀", 23: "⨀", 32: "⟳", 33: "⟳", 36: "↑", 37: "↑", 40: "→", 41: "→",
    44: "=∞", 45: "=∞", 132: "⇆", 133: "⇆", 138: "⊕", 139: "⊕",
    140: "∆", 142: "⌓", 144: "=", 145: "RR", 146: "N",
}
# What each symbol means, for the tooltip.
NAG_WORDS = {
    1: "good move", 2: "mistake", 3: "brilliant move", 4: "blunder",
    5: "interesting move", 6: "dubious move", 7: "only move", 8: "only move",
    10: "equal position", 13: "unclear position", 14: "White is slightly better",
    15: "Black is slightly better", 16: "White is clearly better",
    17: "Black is clearly better", 18: "White is winning", 19: "Black is winning",
    22: "zugzwang", 23: "zugzwang", 32: "development advantage",
    33: "development advantage", 36: "initiative", 37: "initiative",
    40: "attack", 41: "attack", 44: "compensation for the material",
    45: "compensation for the material", 132: "counterplay", 133: "counterplay",
    138: "time trouble", 139: "time trouble", 140: "with the idea",
    142: "better is", 144: "equal", 145: "editorial comment", 146: "novelty",
}
# Move judgements (shown after the move) vs position judgements (after it, spaced).
MOVE_NAGS = {1, 2, 3, 4, 5, 6, 7, 8}

ARROW_COLORS = {"G": "green", "R": "red", "Y": "yellow", "B": "blue"}

_EVP = re.compile(r"\[%evp\s+([^\]]*)\]")
_CMD = re.compile(r"\[%[a-z]+[^\]]*\]")

# --- Two languages in one comment -----------------------------------------

_DE = set("""der die das den dem des ein eine einen einem einer und ist nicht ich mit
zu auf für wenn nach hatte hat habe sich auch noch nur aber oder als wie wird werden
war wäre hier jetzt dann schon sehr mein meine meinen meiner meinem dass weil
gegen bereits zunächst daher sollte wollte konnte mich mir ihm ihn ihr uns diese
dieser dieses diesen zug züge stellung partie vorteil weiß schwarz läufer springer
turm dame könig bauer bauern feld felder damenflügel königsflügel endspiel
eröffnung gewinnt verliert droht kann muss besser stark schwach sieht aus
natürlich überlegen gespielt nachdenken minuten nun um im am vom zum zur beim bei
kein keine keinen etwas immer wieder gibt sogar auf nach hatte meint sah aus
ich er sie es wir mein dein sein seine seinen ihre unser euer man denn doch also
erhöht druck möglichkeit variante weitere gewinnen gewonnen sollte behält dabei
dazu daran darauf wonach danach womit wobei hätte möchte müsste dürfte""".split())
_EN = set("""the a an and is not i with to on for if after had has have also only
but or as how will be was would here now then already very my mine that because
against first thus should wanted could me him her us this these those move moves
position game advantage white black bishop knight rook queen king pawn pawns
square squares queenside kingside endgame opening wins loses threatens can must
better strong weak looks clearly played thinking minutes of in at from by no
some always again there it its which what when where who his he she they their
we our you your more putting pressure doesn't didn't couldn't don't isn't wasn't
it's there see way continue draw chances option too powerful looked like prepared
even after before just than then since while into over under about would think
thought line lines calculated idea ideas plan play playing went go going get got
keep keeps kept give gives make makes made take takes took instead other another
both each such same any all most more less much many one two three only still
good bad excellent interesting important incredible nice great terrible obvious
mistake blunder idea attack defence defense threat sacrifice exchange trade""".split())
# Words that are common in both languages say nothing.
_BOTH = {"in", "an", "was", "will", "so", "die", "also", "man", "war", "hat", "am",
         "best", "bald", "rest", "hand", "plan", "position", "partie"}
_DE -= _BOTH
_EN -= _BOTH
_GERMAN_CHARS = re.compile(r"[äöüßÄÖÜ]")


def _lang_of(word: str) -> int:
    """+1 German, -1 English, 0 neither (moves, names, numbers)."""
    w = word.lower().strip(".,;:!?()\"'„“”‚‘’-")
    if not w:
        return 0
    if _GERMAN_CHARS.search(w) or w in _DE and w not in _EN:
        return 1
    if w in _EN and w not in _DE:
        return -1
    return 0


def _short_pair(words: list) -> Optional[dict]:
    """Short bilingual fragments the word lists cannot place: 'After Nach',
    'The engines prefer Die Engines bevorzugen'. The German half starts with
    a capital letter in mid-sentence, and the halves are about as long."""
    if len(words) > 8 or len(words) < 2:
        return None
    for k in range(1, len(words)):
        w, prev, right = words[k], words[k - 1], words[k:]
        if not w[:1].isupper() or re.match(r"^[KQRBNSLTD]?[a-h]?[1-8]?x?[a-h][1-8]", w):
            continue
        if any(_lang_of(x) < 0 for x in right):
            continue                       # "Caruana and Shankland played": English
        at_stop = re.search(r"[.!?:]$", prev)
        # Mid-sentence capital: halves of similar length; after a full stop
        # ("Incredible sequence! Unglaubliche Sequenz!"): equal halves only.
        if (not at_stop and 0.5 <= k / len(right) <= 2) or (at_stop and k == len(right)):
            return {"en": " ".join(words[:k]), "de": " ".join(right)}
    return None


def split_languages(text: str) -> dict:
    """{"en": ..., "de": ...} when a comment is English followed by its
    German translation (or only one of them); {"en": text} when it cannot
    tell. The switch point is where 'English before, German after' fits
    best, word by word, so it works even when the English part ends
    without a full stop ("...begin with In meiner vorherigen Partie")."""
    words = text.split()
    if not words:
        return {}
    marks = [_lang_of(w) for w in words]
    n_de = sum(1 for m in marks if m > 0)
    n_en = sum(1 for m in marks if m < 0)
    if n_de == 0 and not _GERMAN_CHARS.search(text):
        short = _short_pair(words)
        return short if short else {"en": text.strip()}
    if n_de < n_en // 8 and not _GERMAN_CHARS.search(text):
        return {"en": text.strip()}
    if n_en == 0:
        return {"de": text.strip()}
    scores = []
    en_before = de_before = 0
    for k in range(len(words) + 1):
        scores.append(en_before - de_before + (n_de - de_before) - (n_en - en_before))
        if k < len(words):
            en_before += marks[k] < 0
            de_before += marks[k] > 0
    best = max(scores)
    ties = [k for k, sc in enumerate(scores) if sc == best]
    # Words neither list knows ("great.", "Najdorf") make ties: cut at the
    # end of a sentence when one is among them, else as late as possible
    # (the German part starts with words the list does know).
    ends = [k for k in ties if k > 0 and re.search(r"[.!?:]$", words[k - 1])]
    best_k = ends[-1] if ends else ties[-1]
    en, de = " ".join(words[:best_k]).strip(), " ".join(words[best_k:]).strip()
    if not en:
        return {"de": de}
    if not de:
        return {"en": en}
    return {"en": en, "de": de}


# --- Games and nodes -------------------------------------------------------

def read_games(text: str) -> list:
    """Every game in a PGN text, as python-chess games (variations kept)."""
    games, f = [], io.StringIO(text)
    while True:
        g = chess.pgn.read_game(f)
        if g is None:
            break
        if g.errors:
            g.headers["_errors"] = "; ".join(str(e) for e in g.errors[:3])
        games.append(g)
    return games


def game_list(games: list) -> list:
    """One line per game for a picker."""
    out = []
    for i, g in enumerate(games):
        h = g.headers
        out.append({"index": i, "white": h.get("White", "?"), "black": h.get("Black", "?"),
                    "result": h.get("Result", "*"), "event": h.get("Event", ""),
                    "date": h.get("Date", ""), "eco": h.get("ECO", ""),
                    "annotator": h.get("Annotator", ""),
                    "plies": sum(1 for _ in g.mainline_moves()),
                    "annotated": has_annotations(g)})
    return out


def has_annotations(game: chess.pgn.Game) -> bool:
    for node in _walk(game):
        if node is not game and (node.comment.strip() or node.nags or node.variations[1:]):
            return True
        if node is game and (_CMD.sub("", node.comment).strip() or node.variations[1:]):
            return True
    return False


def _walk(node):
    yield node
    for v in node.variations:
        yield from _walk(v)


def _comment(raw: str) -> dict:
    """Comment text without engine commands, split by language."""
    text = _CMD.sub("", raw or "")
    text = re.sub(r"\s+", " ", text).strip()
    return split_languages(text) if text else {}


def _shapes(node) -> tuple:
    arrows, circles = [], []
    for a in node.arrows():
        arrows.append({"from": chess.square_name(a.tail), "to": chess.square_name(a.head),
                       "color": a.color})
    for m in re.finditer(r"\[%csl\s+([^\]]*)\]", node.comment or ""):
        for tok in m.group(1).split(","):
            tok = tok.strip()
            if len(tok) == 3 and tok[0] in ARROW_COLORS and tok[1:] in chess.SQUARE_NAMES:
                circles.append({"square": tok[1:], "color": ARROW_COLORS[tok[0]]})
    return arrows, circles


def evp(game: chess.pgn.Game) -> list:
    """The annotator's engine evaluation per main-line ply (White's view,
    centipawns), from ChessBase's `[%evp start,count,v0,v1,...]`; [] if none."""
    m = _EVP.search(game.comment or "")
    if not m:
        return []
    try:
        vals = [int(x) for x in m.group(1).split(",") if x.strip()]
    except ValueError:
        return []
    if len(vals) < 3:
        return []
    start = vals[0]
    out = [None] * start
    return out + vals[2:]


def game_tree(game: chess.pgn.Game) -> dict:
    """The game as a flat list of nodes, in reading order.

    Each node: id, parent, ply (0 = after White's first move), san, uci,
    fen (after the move), move_label ("12...Nxd5"), main (on the main line),
    depth (0 main line, 1 variation, 2 variation in a variation), nags
    [{glyph, words, move}], comment {en, de}, before {en, de} (a comment
    before a variation's first move), arrows, circles, children ids.
    Node 0 is the start position (its comment is the game's opening text).
    """
    nodes = []

    def add(node, parent_id, depth, main):
        nid = len(nodes)
        board = node.board()
        rec = {"id": nid, "parent": parent_id, "depth": depth, "main": main,
               "fen": board.fen(), "children": [], "comment": _comment(node.comment),
               "before": {}, "nags": [], "arrows": [], "circles": []}
        if node.move is not None:
            parent_board = node.parent.board()
            san = parent_board.san(node.move)
            n = parent_board.fullmove_number
            rec.update(ply=parent_board.ply() - game.board().ply(), san=san,
                       uci=node.move.uci(),
                       move_label=f"{n}. {san}" if parent_board.turn == chess.WHITE
                       else f"{n}...{san}",
                       white=parent_board.turn == chess.WHITE)
            rec["before"] = _comment(node.starting_comment)
            for g in sorted(node.nags):
                rec["nags"].append({"nag": g, "glyph": NAGS.get(g, ""),
                                    "words": NAG_WORDS.get(g, ""), "move": g in MOVE_NAGS})
        else:
            rec.update(ply=-1, san="", uci="", move_label="", white=None)
        rec["arrows"], rec["circles"] = _shapes(node)
        nodes.append(rec)
        for i, child in enumerate(node.variations):
            cid = add(child, nid, depth if i == 0 else depth + 1, main and i == 0)
            rec["children"].append(cid)
        return nid

    add(game, None, 0, True)
    mainline = [n["id"] for n in nodes if n["main"] and n["ply"] >= 0]
    return {"nodes": nodes, "mainline": mainline, "evp": evp(game),
            "annotated": has_annotations(game),
            "languages": sorted({k for n in nodes for k in list(n["comment"]) + list(n["before"])})}
