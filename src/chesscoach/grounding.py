"""
The grounding check: every concrete chess claim in the model's text is
verified on the board before the text is shown.

The prompt tells the model not to calculate; this module is what makes
that true. It finds claims of these kinds and checks each one:

  placement   "the knight on f6"              a knight is on f6
  attack      "the rook on e1 attacks c8"     the rook's moves reach c8
  defence     "the king on f7 defends e7"     the king reaches e7
  lost defence "the king no longer protects the pawn on e7"
  undefended  "the bishop on c8 is undefended"  nothing defends c8
  pin         "pins the knight on f3"         the knight is pinned
  fork        "forks the rook on b7"          one piece attacks b7 and more
  material    "material is equal", "White is up a piece"
  legal moves "only one legal reply", "six legal moves"

A claim passes if it is true on ANY of the boards in context: the
positions before and after the move and along the engine's lines. Text
refers to all of those, and a check that is too strict would delete true
sentences; the errors the experiments showed are false on every board.

Claims the parser doesn't recognise are let through. The check catches
what it can name, not everything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import chess

from .describe import PIECE_NAMES

NAME_TO_TYPE = {v: k for k, v in PIECE_NAMES.items()}
VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
          chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 100}
NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
           "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
           "twelve": 12}

_SIDE = r"(?:(?:the )?(?:white|black)(?:'s)? )?"
_PIECE = r"\b(pawn|knight|bishop|rook|queen|king)s?"
# A piece with its square: "White's knight on f3", "the pawn from e5".
REF = _SIDE + _PIECE + r" (?:on|from|at) \**([a-h][1-8])\b"
REF_RE = re.compile(REF, re.I)


@dataclass
class Failure:
    kind: str          # placement, attack, defence, ...
    claim: str         # the words that made the claim
    correction: str    # what is true instead, for the retry prompt
    sentence: str = ""


# --- Board helpers --------------------------------------------------------

def _piece(board: chess.Board, sq: int, name: str) -> Optional[chess.Piece]:
    p = board.piece_at(sq)
    return p if p and PIECE_NAMES[p.piece_type] == name.lower() else None


def _reaches(board: chess.Board, frm: int, to: int, xray: bool = True) -> bool:
    """Does the piece on `frm` attack `to`, directly or through one piece?"""
    if to in board.attacks(frm):
        return True
    if not xray:
        return False
    p = board.piece_at(frm)
    if not p or p.piece_type not in (chess.BISHOP, chess.ROOK, chess.QUEEN):
        return False
    between = chess.SquareSet(chess.between(frm, to))
    if not between or not chess.SquareSet.ray(frm, to):
        return False
    blockers = [s for s in between if board.piece_at(s)]
    if len(blockers) != 1:
        return False
    b = board.copy(stack=False)
    b.remove_piece_at(blockers[0])
    return to in b.attacks(frm)


def _relatively_pinned(board: chess.Board, sq: int) -> bool:
    """Absolute pin, or pinned to a piece worth more than itself: a
    bishop, rook or queen attacks it, and behind it on the same line
    stands a more valuable piece of its own side."""
    p = board.piece_at(sq)
    if not p:
        return False
    if board.is_pinned(p.color, sq):
        return True
    b = board.copy(stack=False)
    b.remove_piece_at(sq)
    for atk in board.attackers(not p.color, sq):
        if board.piece_at(atk).piece_type not in (chess.BISHOP, chess.ROOK, chess.QUEEN):
            continue
        for behind in b.attacks(atk):
            q = b.piece_at(behind)
            if (q and q.color == p.color
                    and sq in chess.SquareSet(chess.between(atk, behind))
                    and VALUES[q.piece_type] > VALUES[p.piece_type]):
                return True
    return False


def _forks(board: chess.Board) -> list:
    """(attacker square, [target squares]) for every piece attacking two
    or more enemy pieces worth at least a knight, or the king."""
    out = []
    for sq, p in board.piece_map().items():
        targets = [t for t in board.attacks(sq)
                   if (q := board.piece_at(t)) and q.color != p.color
                   and (q.piece_type == chess.KING
                        or VALUES[q.piece_type] >= 3)]
        if len(targets) >= 2:
            out.append((sq, targets))
    return out


def _material(board: chess.Board) -> int:
    return sum((1 if p.color else -1) * VALUES[p.piece_type]
               for p in board.piece_map().values() if p.piece_type != chess.KING)


def _defended(board: chess.Board, sq: int) -> bool:
    p = board.piece_at(sq)
    return bool(p and board.attackers(p.color, sq))


# --- Sentences ------------------------------------------------------------

# A heading is a short bold label ending in a colon: "**What happened**:".
_HEAD = re.compile(r"^(\s*(?:[-*]\s+)?\*\*[^*]{1,40}(?::\*\*|\*\*:)\s*)")


def split_sentences(text: str) -> list:
    """[(line_index, prefix, [sentences])]: headings kept apart so a
    removed sentence never takes its heading with it."""
    out = []
    for i, line in enumerate(text.split("\n")):
        m = _HEAD.match(line)
        prefix = m.group(1) if m else ""
        body = line[len(prefix):]
        parts = [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"'(*])", body) if s]
        out.append((i, prefix, parts))
    return out


# --- Claim checks ---------------------------------------------------------

def _check_placement(s: str, boards: list) -> list:
    out = []
    for m in REF_RE.finditer(s):
        name, sq = m.group(1).lower(), m.group(2)
        # "the pawn from e5" names where a piece came from: fine on any board.
        if not any(_piece(b, chess.parse_square(sq), name) for b in boards):
            actual = boards[-1].piece_at(chess.parse_square(sq))
            what = (f"a {PIECE_NAMES[actual.piece_type]}" if actual else "nothing")
            out.append(Failure("placement", m.group(0),
                               f"there is no {name} on {sq} in this position "
                               f"(there is {what} there)"))
    return out


_ATTACK_VERB = r"(?:attacks?|attacking|hits|hitting|threatens|threatening|targets|targeting|eyes|eyeing)"
_DEFEND_VERB = r"(?:defends?|defending|protects?|protecting|guards?|guarding)"
_NEG = re.compile(r"\b(?:no longer|not|n't|never|stops?|ceases?|instead of)\b", re.I)


def _subject_object(s: str, verb: str):
    """Yield (subject match, object match, text between subject and verb)."""
    pat = re.compile(REF + r"(?P<mid>[^.;:]{0,40}?)\b" + verb + r"\b[^.;:]{0,25}?" + REF, re.I)
    for m in pat.finditer(s):
        yield m, m.group(1).lower(), m.group(2), m.group(4).lower(), m.group(5), m.group("mid")


def _check_attacks(s: str, boards: list) -> list:
    out = []
    for m, sn, ssq, on, osq, mid in _subject_object(s, _ATTACK_VERB):
        if _NEG.search(mid) or REF_RE.search(mid):
            continue                      # negated, or the verb has another subject
        a, t = chess.parse_square(ssq), chess.parse_square(osq)
        ok = any(_piece(b, a, sn) and _piece(b, t, on) and _reaches(b, a, t)
                 for b in boards)
        if not ok:
            out.append(Failure("attack", m.group(0),
                               f"the {sn} on {ssq} does not attack the {on} on {osq}"))
    return out


def _check_defences(s: str, boards: list) -> list:
    out = []
    for m, sn, ssq, on, osq, mid in _subject_object(s, _DEFEND_VERB):
        if _NEG.search(mid) or REF_RE.search(mid):
            continue
        a, t = chess.parse_square(ssq), chess.parse_square(osq)
        ok = any(_piece(b, a, sn) and _piece(b, t, on)
                 and b.piece_at(a).color == b.piece_at(t).color
                 and _reaches(b, a, t, xray=False) for b in boards)
        if not ok:
            out.append(Failure("defence", m.group(0),
                               f"the {sn} on {ssq} does not defend the {on} on {osq}"))
    return out


_LOST = re.compile(
    r"(?:no longer (?:defends?|protects?|guards?)|removes? (?:its|the|their|his) "
    r"(?:protection|defen[cs]e|support) (?:from|of)|stops? (?:defending|protecting|guarding)"
    r"|abandons?)(?: the)?[^.;:]{0,15}?" + REF, re.I)


def _check_lost_defence(s: str, boards: list, move: Optional[chess.Move]) -> list:
    """'The king no longer protects the pawn on e7' after the king moved:
    false if the moved piece still defends that square."""
    if move is None or len(boards) < 2:
        return []
    after = boards[1]
    mover = after.piece_at(move.to_square)
    out = []
    for m in _LOST.finditer(s):
        name, sq = m.group(1).lower(), chess.parse_square(m.group(2))
        target = _piece(after, sq, name)
        if mover and target and target.color == mover.color \
                and sq in after.attacks(move.to_square):
            out.append(Failure(
                "defence", m.group(0),
                f"the {PIECE_NAMES[mover.piece_type]} on "
                f"{chess.square_name(move.to_square)} still defends the "
                f"{name} on {chess.square_name(sq)}"))
    return out


_UNDEF = re.compile(
    REF + r"(?:,? which)? (?:is|was|remains|stays|becomes|left|now)? ?(?:now |still |completely |totally )?"
    r"(?:undefended|unprotected|hanging|loose|without (?:a |any )?(?:defender|defence|defense|protection))"
    r"|(?:undefended|unprotected|hanging|loose) " + _PIECE + r" on \**([a-h][1-8])\b"
    r"|leaves?[^.;:]{0,10}?" + REF + r" (?:undefended|unprotected|hanging|without (?:a |any )?(?:defender|protection))",
    re.I)


def _check_undefended(s: str, boards: list, conseq: Optional[list] = None) -> list:
    out = []
    for m in _UNDEF.finditer(s):
        g = [x for x in m.groups() if x]
        name, sq = g[0].lower(), chess.parse_square(g[1])
        # "The move leaves X undefended" is about the position right after
        # the move, not somewhere further along a line.
        pool = [conseq[0]] if (conseq and m.group(0).lower().startswith("leav")) else boards
        # Whose piece: named in the text, else whoever has it there after
        # the move (the second board), else first.
        said = re.search(r"\b(white|black)", m.group(0), re.I)
        order = boards[1:2] + boards[:1] + boards[2:]
        color = ((said.group(1).lower() == "white") if said else
                 next((b.piece_at(sq).color for b in order if _piece(b, sq, name)), None))
        mine = [b for b in pool if _piece(b, sq, name) and b.piece_at(sq).color == color]
        ok = any(not _defended(b, sq) for b in mine)
        if not ok and mine:
            out.append(Failure("undefended", m.group(0),
                               f"the {name} on {chess.square_name(sq)} is defended"))
    return out


def _check_pins(s: str, boards: list) -> list:
    if not re.search(r"\bpin(?:s|ned|ning)?\b", s, re.I):
        return []
    refs = [(m.group(1).lower(), chess.parse_square(m.group(2))) for m in REF_RE.finditer(s)]
    any_pin = any(_relatively_pinned(b, sq) for b in boards for sq in b.piece_map())
    if refs:
        ok = any(_piece(b, sq, n) and _relatively_pinned(b, sq)
                 for b in boards for n, sq in refs)
        # The pinning piece may be the one named; accept if any named
        # piece pins something.
        ok = ok or any(_piece(b, sq, n) and any(
            _relatively_pinned(b, t) and sq in b.attackers(not b.piece_at(t).color, t)
            for t in b.piece_map()) for b in boards for n, sq in refs)
        if not ok:
            return [Failure("pin", s.strip(), "no piece named here is pinned, "
                            "and none of them pins anything")]
    elif not any_pin:
        return [Failure("pin", s.strip(), "there is no pin in this position")]
    return []


def _check_forks(s: str, boards: list) -> list:
    if not re.search(r"\bfork", s, re.I):
        return []
    forks = [(b, sq, set(t)) for b in boards for sq, t in _forks(b)]
    if not forks:
        return [Failure("fork", s.strip(), "there is no fork in this position "
                        "or along the engine's line")]
    named = [chess.parse_square(m.group(2)) for m in REF_RE.finditer(s)]
    if named and not any(set(named) & targets or sq in named
                         for _, sq, targets in forks):
        sqs = ", ".join(chess.square_name(x) for x in named)
        return [Failure("fork", s.strip(), f"no piece forks anything on {sqs}")]
    return []


_EQUAL = re.compile(r"material (?:is |remains |stays |looks )?(?:roughly |about )?"
                    r"(?:equal|level|even|balanced)|equal material|level material"
                    r"|material (?:equality|balance is equal)", re.I)
_UP = re.compile(r"\b(white|black) (?:is|remains|stays|will be|would be) (?:still )?"
                 r"(up|ahead|down|behind)\b(?: (?:by )?(?:a|an|one|two|three|\d+) "
                 r"(pawns?|pieces?|knights?|bishops?|rooks?|queens?|exchange))?", re.I)


def _check_material(s: str, boards: list) -> list:
    out = []
    mats = [_material(b) for b in boards]
    if _EQUAL.search(s) and all(m != 0 for m in mats):
        out.append(Failure("material", _EQUAL.search(s).group(0),
                           _material_words(boards[-1])))
    for m in _UP.finditer(s):
        side = 1 if m.group(1).lower() == "white" else -1
        sign = 1 if m.group(2).lower() in ("up", "ahead") else -1
        noun = (m.group(3) or "").lower().rstrip("s")
        if noun in NAME_TO_TYPE:
            # "Black is up a bishop" is about bishops, not the total: with
            # White a knight up in return the net can be level and it's true.
            pt = NAME_TO_TYPE[noun]
            diffs = [side * (len(b.pieces(pt, chess.WHITE)) - len(b.pieces(pt, chess.BLACK)))
                     for b in boards]
            if all(sign * d <= 0 for d in diffs):
                out.append(Failure("material", m.group(0), _material_words(boards[-1])))
        elif all(side * sign * x <= 0 for x in mats):
            out.append(Failure("material", m.group(0), _material_words(boards[-1])))
    return out


def _material_words(board: chess.Board) -> str:
    from .facts import material_summary
    return material_summary(board)


_ONLY = re.compile(r"\b(?:only|just|a single) (?:one )?(?:legal )?(?:move|reply|response)\b"
                   r"(?! (?:that|which|to) )", re.I)
_COUNT = re.compile(r"\b(\d+|" + "|".join(NUMBERS) + r") (?:legal )?(?:moves|replies)\b"
                    r"(?: (?:available|to choose from|in total))?", re.I)


def _check_legal(s: str, boards: list) -> list:
    out = []
    counts = {b.legal_moves.count() for b in boards}
    if _ONLY.search(s) and 1 not in counts and re.search(r"\blegal\b", s, re.I):
        out.append(Failure("legal", _ONLY.search(s).group(0),
                           "there is more than one legal move: "
                           + ", ".join(str(c) for c in sorted(counts))))
    for m in _COUNT.finditer(s):
        if not re.search(r"\blegal\b", m.group(0), re.I):
            continue                      # "two moves later" is not a count
        w = m.group(1).lower()
        n = int(w) if w.isdigit() else NUMBERS[w]
        if n not in counts:
            out.append(Failure("legal", m.group(0),
                               f"the number of legal moves is "
                               f"{' or '.join(str(c) for c in sorted(counts))}, not {n}"))
    return out


# --- Consequences: what a move leads to ------------------------------------

def passed_pawns(board: chess.Board, color: bool) -> list:
    out = []
    step = 1 if color == chess.WHITE else -1
    for sq in board.pieces(chess.PAWN, color):
        f, r = chess.square_file(sq), chess.square_rank(sq)
        blocked = False
        rr = r + step
        while 0 <= rr <= 7 and not blocked:
            for ff in (f - 1, f, f + 1):
                if 0 <= ff <= 7:
                    q = board.piece_at(chess.square(ff, rr))
                    if q and q.piece_type == chess.PAWN and q.color != color:
                        blocked = True
            rr += step
        if not blocked:
            out.append(sq)
    return out


_GAIN = r"(?:creat|gain|get|give|allow|leav|lead|result|produc|obtain|win|grant|hand|caus|saddl|introduc|form)\w*"
_DENY = re.compile(r"\b(?:avoid|prevent|stop|deny|without|instead of|not|no)\w*\b", re.I)
_STRUCT = re.compile(r"(passed|doubled|isolated) pawns?"
                     r"(?: on (?:the )?([a-h])-file| on \**([a-h][1-8])\b)?", re.I)


def _side_before(text: str, pos: int) -> Optional[bool]:
    m = list(re.finditer(r"\b(white|black)\b", text[:pos], re.I))
    return None if not m else (m[-1].group(1).lower() == "white")


def _structure(board: chess.Board, color: bool, kind: str) -> set:
    """Files on which `color` has a passed, doubled or isolated pawn."""
    pawns = board.pieces(chess.PAWN, color)
    files = [chess.square_file(sq) for sq in pawns]
    if kind == "passed":
        return {chess.FILE_NAMES[chess.square_file(sq)] for sq in passed_pawns(board, color)}
    if kind == "doubled":
        return {chess.FILE_NAMES[f] for f in set(files) if files.count(f) > 1}
    return {chess.FILE_NAMES[f] for f in set(files)
            if (f - 1) not in files and (f + 1) not in files}


def _check_structure(s: str, before: Optional[chess.Board], conseq: list) -> list:
    """'Creates a passed pawn on the f-file', 'avoids doubled pawns': true
    only if the move's own line has (or lacks) that pawn. A 'creates' claim
    about something that was already on the board before is false too."""
    out = []
    for m in _STRUCT.finditer(s):
        lead = s[max(0, m.start() - 70):m.start()]
        if not re.search(_GAIN, lead, re.I):
            continue                      # a description, not a consequence
        kind = m.group(1).lower()
        side = _side_before(s, m.start())
        sides = [side] if side is not None else [chess.WHITE, chess.BLACK]
        file_ = m.group(2) or (m.group(3)[0] if m.group(3) else None)

        def has(b):
            return any(file_ is None and _structure(b, c, kind)
                       or file_ is not None and file_.lower() in _structure(b, c, kind)
                       for c in sides)
        where = f" on the {file_}-file" if file_ else ""
        who = {True: "White", False: "Black"}.get(side, "either side")
        if _DENY.search(lead):
            # Judged where the line ends, as the line comparison is: a pawn
            # weakness traded off along the way counts as avoided.
            if conseq and has(conseq[-1]):
                out.append(Failure(kind + " pawn", m.group(0),
                                   f"there is still a {kind} pawn{where} after that move"))
        elif not any(has(b) for b in conseq):
            out.append(Failure(kind + " pawn", m.group(0),
                               f"{who} has no {kind} pawn{where} after that move"))
        elif (kind != "passed" and before is not None and has(before)
              and re.search(r"creat|caus|saddl|produc|introduc|form", lead, re.I)):
            out.append(Failure(kind + " pawn", m.group(0),
                               f"the {kind} pawns{where} were already there before "
                               f"the move; it did not create them"))
    return out


_THREAT_GONE = re.compile(
    r"(?:remov|eliminat|neutrali[sz]|end|parr|meet|deal\w* with|ward\w* off|stop)\w*"
    r" (?:the |a |any |an )?(?:immediate )?(?:threats?|attack) (?:to|against|on) (?:the )?" + REF, re.I)


def _threatened(board: chess.Board, sq: int) -> bool:
    p = board.piece_at(sq)
    if not p:
        return False
    atk = board.attackers(not p.color, sq)
    if not atk:
        return False
    if any(VALUES[board.piece_at(a).piece_type] < VALUES[p.piece_type] for a in atk):
        return True
    return not board.attackers(p.color, sq)


def _check_threat_removed(s: str, conseq: list) -> list:
    """'X removes the threat to the knight on c3': right after that move,
    the knight is no longer attacked by something cheaper or left hanging."""
    if not conseq:
        return []
    first = conseq[0]
    out = []
    for m in _THREAT_GONE.finditer(s):
        name, sq = m.group(1).lower(), chess.parse_square(m.group(2))
        if _piece(first, sq, name) and _threatened(first, sq):
            by = ", ".join(PIECE_NAMES[first.piece_type_at(a)] + " on " + chess.square_name(a)
                           for a in first.attackers(not first.color_at(sq), sq))
            out.append(Failure("threat", m.group(0),
                               f"after that move the {name} on {chess.square_name(sq)} "
                               f"is still attacked (by the {by})"))
    return out


_LOST_ATTACK = re.compile(
    r"(?:gives? up|giving up|loses?|losing|drops?|abandons?) (?:an|its|the|his|her|their|any)? ?"
    r"attack (?:on|against) (?:the )?" + REF
    + r"|no longer (?:attacks?|attacking|threatens?|hits?) (?:the )?" + REF, re.I)


def _check_lost_attack(s: str, boards: list) -> list:
    """'The knight gives up its attack on the rook on f8': somewhere in the
    positions looked at, that piece must have been attacked at all."""
    out = []
    for m in _LOST_ATTACK.finditer(s):
        g = [x for x in m.groups() if x]
        name, sq = g[0].lower(), chess.parse_square(g[1])
        if re.search(r"\b(?:not|never|nor|without)\b|n't\b", s[max(0, m.start() - 40):m.start()],
                     re.I):
            continue                      # "does not involve giving up an attack on ..."
        held = [b for b in boards if _piece(b, sq, name)]
        if held and not any(b.attackers(not b.color_at(sq), sq) for b in held):
            out.append(Failure("attack", m.group(0),
                               f"the {name} on {chess.square_name(sq)} was never attacked, "
                               f"so no attack on it was given up"))
    return out


_KEEPS = re.compile(r"\b(?:preserv|keep|kept|sav|retain|maintain)\w*"
                    r"(?: the| its| their| white's| black's)?[^.;:]{0,15}?" + REF, re.I)


def _check_keeps(s: str, conseq: list) -> list:
    """'X preserves the pawn on e5': right after that move the pawn is still
    on e5. (exf6 does not preserve the e5 pawn: that pawn is the capturer.)"""
    if not conseq:
        return []
    first = conseq[0]
    out = []
    for m in _KEEPS.finditer(s):
        if re.search(r"\b(?:had|if)\b", s[max(0, m.start() - 30):m.start()], re.I):
            continue                     # "had they kept the king on f8": the other line
        name, sq = m.group(1).lower(), chess.parse_square(m.group(2))
        if not _piece(first, sq, name):
            out.append(Failure("keeps", m.group(0),
                               f"after that move there is no {name} on "
                               f"{chess.square_name(sq)}"))
    return out


_PAIR_KEEP = re.compile(r"\b(?:keep|kept|retain|preserv|maintain|hold)\w*(?: on to)?"
                        r"(?: the| its| their| (?:white|black)'s)? bishop pair", re.I)
_PAIR_LOSE = re.compile(r"\b(?:los|give|gave|giv|sacrific|surrender|concede)\w*(?: up)?"
                        r"(?: the| its| their| (?:white|black)'s)? bishop pair", re.I)


def _check_bishop_pair(s: str, conseq: list, mover: Optional[bool]) -> list:
    """'...retains the bishop pair' / 'gives up the bishop pair', judged at
    the end of that move's line, for the side making the move (or the side
    named)."""
    if not conseq or mover is None:
        return []
    out = []
    end = conseq[-1]
    for pat, want in ((_PAIR_KEEP, True), (_PAIR_LOSE, False)):
        for m in pat.finditer(s):
            said = re.search(r"\b(white|black)'s", m.group(0), re.I)
            side = (said.group(1).lower() == "white") if said else mover
            has = len(end.pieces(chess.BISHOP, side)) >= 2
            if has != want:
                who = "White" if side else "Black"
                out.append(Failure("bishop pair", m.group(0),
                                   f"{who} {'does not keep' if want else 'still has'} "
                                   f"the bishop pair at the end of that line"))
    return out


_STILL = re.compile(r"\b(?:now|still|remains?|continues?|can|could|will|is free)\b", re.I)


def _check_captured(s: str, before: chess.Board, move: chess.Move) -> list:
    """The piece the move captured, spoken of as still in play: '9.Nxd4 ...
    Black's pawn on d4, which now has a clear path to attack'."""
    if not before.is_capture(move) or before.is_en_passant(move):
        return []
    victim = before.piece_at(move.to_square)
    out = []
    for m in REF_RE.finditer(s):
        name, sq = m.group(1).lower(), chess.parse_square(m.group(2))
        if sq != move.to_square or PIECE_NAMES[victim.piece_type] != name:
            continue
        said = re.search(r"\b(white|black)", m.group(0), re.I)
        if said and (said.group(1).lower() == "white") != victim.color:
            continue                         # the capturing piece, not the victim
        after = s[m.end():m.end() + 60]
        hit = _STILL.search(after)
        # "the bishop on c4, which was left undefended and still under
        # attack" describes it before the capture: past tense is fine.
        if hit and not re.search(r"\b(?:was|were|had)\b", after[:hit.start()], re.I):
            out.append(Failure("captured", m.group(0),
                               f"that {name} was captured by the move and is no "
                               f"longer on the board"))
    return out


_PROTECTS = re.compile(r"\b(?:protect|defend|guard)\w*(?: the| its)? " + REF, re.I)


def _check_protects_enemy(s: str, boards: list, mover: Optional[bool]) -> list:
    """'...Bxh5 would have protected the knight on f3' when the knight is
    White's and the move is Black's: a move cannot defend the other side's
    piece. Applies to 'What happened' and 'Better was', which are about the
    player's own move."""
    if mover is None:
        return []
    out = []
    for m in _PROTECTS.finditer(s):
        name, sq = m.group(1).lower(), chess.parse_square(m.group(2))
        colors = {b.piece_at(sq).color for b in boards if _piece(b, sq, name)}
        if colors and mover not in colors:
            them = "White" if not mover else "Black"
            out.append(Failure("defence", m.group(0),
                               f"the {name} on {chess.square_name(sq)} is {them}'s; "
                               f"a move by the other side cannot protect it"))
    return out


# --- "What now": the continuation must be the engine's ---------------------

_SQ = r"\**([a-h][1-8])\b"
_CAPTURE = re.compile(
    r"\b(?:re)?captur\w*(?: back)?(?: (?:the |black's |white's )?(?:" + _PIECE[2:] + r"|pawn)"
    r"(?: on " + _SQ + r")?)? with (?:the |its |a )?" + _PIECE + r" (?:from|on) " + _SQ, re.I)
_MOVE_TO = re.compile(
    r"\b(?:mov\w*|advanc\w*|push\w*|bring\w*|play\w*|put\w*|retreat\w*|develop\w*)"
    r" (?:the |its |his |her )?" + _PIECE + r"(?: from " + _SQ + r")? to " + _SQ, re.I)


def line_moves(played: Optional[list]) -> list:
    """(board before, move) for each move along the played line."""
    out = []
    for prev, cur in zip(played or [], (played or [])[1:]):
        if cur.move_stack:
            out.append((prev, cur.peek()))
    return out


_SUBJECT = re.compile(r"\b(white|black)\s+(?:will|should|would|can|could|must|may|might|"
                      r"to|then|answers|responds|replies|plays|moves|captures|recaptures|"
                      r"is likely to|is expected to)\b", re.I)


def _subject(s: str, pos: int) -> Optional[bool]:
    """The side doing the move described at `pos`: the last 'White will',
    'Black should' ... before it. 'White should respond by capturing the
    Black pawn on f7 ... and then retreating the king to f8' (11.hxg6) is
    White's plan, so the king retreat must be a White move; the f8 retreat
    in the engine's line was Black's."""
    found = list(_SUBJECT.finditer(s[:pos]))
    if not found:
        return None
    return found[-1].group(1).lower() == "white"


def _check_continuation(s: str, moves: list) -> list:
    """A move described in 'What now' must be one of the engine's next
    moves: 'Black recaptures with the pawn on c6' when the engine's line
    recaptures with the knight is a guess, not the analysis."""
    if not moves:
        return []
    from .describe import describe_move
    plan = "; ".join(describe_move(b, m) for b, m in moves[:2])
    out = []
    for m in _CAPTURE.finditer(s):
        target_sq, piece, frm = m.group(2), m.group(3).lower(), m.group(4)
        side = _subject(s, m.start())
        ok = any(b.is_capture(mv) and chess.square_name(mv.from_square) == frm
                 and PIECE_NAMES[b.piece_type_at(mv.from_square)] == piece
                 and (target_sq is None or chess.square_name(mv.to_square) == target_sq)
                 and (side is None or b.turn == side)
                 for b, mv in moves)
        if not ok:
            out.append(Failure("continuation", m.group(0),
                               f"that capture is not in the engine's line, which is: {plan}"))
    for m in _MOVE_TO.finditer(s):
        piece, frm, to = m.group(1).lower(), m.group(2), m.group(3)
        side = _subject(s, m.start())
        ok = any(PIECE_NAMES[b.piece_type_at(mv.from_square)] == piece
                 and chess.square_name(mv.to_square) == to
                 and (frm is None or chess.square_name(mv.from_square) == frm)
                 and (side is None or b.turn == side)
                 for b, mv in moves)
        if not ok:
            out.append(Failure("continuation", m.group(0),
                               f"that move is not in the engine's line, which is: {plan}"))
    return out


_SAVE = re.compile(r"\b(?:defend|protect|save|support|guard|rescue)\w*\s+(?:the|its|his|her|"
                   r"their|white's|black's|that)\s+(pawn|knight|bishop|rook|queen)\b"
                   r"(?!\s+(?:on|from|at)\s+[a-h][1-8])", re.I)


def _check_gone_piece(s: str, moves: list) -> list:
    """'What now: White takes the bishop on d4. Black should play ...Ne5 to
    defend the bishop' (6...Bxd4): the opponent's reply captured it, so
    there is no bishop left to defend."""
    if not moves:
        return []
    b, reply = moves[0]
    if not b.is_capture(reply) or b.is_en_passant(reply):
        return []
    victim = b.piece_at(reply.to_square)
    if victim is None:
        return []
    name = PIECE_NAMES[victim.piece_type]
    after = b.copy(stack=False)
    after.push(reply)
    others = after.pieces(victim.piece_type, victim.color)
    if any(after.attackers(not victim.color, sq) for sq in others):
        return []                       # another one of that kind does need defending
    rest = (f"; {'the other ' + name + ' is' if len(others) == 1 else 'the others are'} "
            f"not attacked" if others else f"; there is no {name} left to defend")
    out = []
    for m in _SAVE.finditer(s):
        if m.group(1).lower() == name:
            out.append(Failure("continuation", m.group(0),
                               f"the {name} was captured on {chess.square_name(reply.to_square)} "
                               f"by {b.san(reply)}{rest}"))
    return out


_SHELTER = re.compile(
    r"\b(?:weaken|expos|damag|compromis|reduc|remov|strip)\w*\s+(?:the\s+)?(white|black)'s\s+king"
    r"(?:'s)?(?:\s+(?:position|shelter|safety|pawn shelter|cover))?", re.I)


def _shield(board: chess.Board, white: bool) -> int:
    from .features import extract_features
    f = extract_features(board)
    return f.white_king_pawn_shield if white else f.black_king_pawn_shield


def _check_shelter(s: str, before: Optional[chess.Board], conseq: list,
                   better: Optional[list] = None) -> list:
    """'10.Bxc4 weakens White's king position by removing a shield pawn'
    (the facts were about BLACK's shelter): the named side must have fewer
    shield pawns somewhere along the move's own line than before it, or
    than along the engine's choice. Only claims about the pawn shelter
    ("shield", "shelter"); "weakens the king's safety" with a check is
    something else."""
    if before is None or not conseq or not re.search(r"shield|shelter|pawn cover", s, re.I):
        return []
    out = []
    for m in _SHELTER.finditer(s):
        white = m.group(1).lower() == "white"
        start = _shield(before, white)
        best = max([_shield(b, white) for b in better or []] + [start])
        if not any(_shield(b, white) < best for b in conseq):
            out.append(Failure("shelter", m.group(0),
                               f"{'White' if white else 'Black'}'s king keeps its pawn shelter "
                               f"after that move ({start} shield pawns throughout its line)"))
    return out


_MOVE_DEFENDS = re.compile(
    r"\b(?:moving|moves?|putting|bringing|playing)\s+the\s+(king|queen|rook|bishop|knight)"
    r"(?:\s+from\s+[a-h][1-8])?\s+to\s+([a-h][1-8])[^.;]{0,60}?"
    r"(?:defen[cs]e of|defends?|defending|protects?|protecting|protection of|guards?|guarding|"
    r"supports?|supporting)"
    r"\s+(?:the\s+|its\s+|his\s+)?(?:(?:white|black)(?:'s)?\s+)?"
    r"(?:(pawn|knight|bishop|rook|queen)\s+on\s+([a-h][1-8])|([a-h][1-8])[- ](pawn|knight|bishop|rook|queen)"
    r"|(pawn|knight|bishop|rook|queen)\b(?!\s+(?:on|from|at|to)\b|'s))",
    re.I)


def _check_move_defends(s: str, boards: list) -> list:
    """'Moving the queen to c2, which maintains defence of the a2 pawn'
    (15.Rdg1: the b2 pawn is in the way): a piece of that kind on that
    square must defend the target in some position looked at."""
    out = []
    for m in _MOVE_DEFENDS.finditer(s):
        piece, dest = m.group(1).lower(), chess.parse_square(m.group(2))
        if m.group(7):
            out += _check_move_defends_kind(m, piece, dest, m.group(7).lower(), boards)
            continue
        tname = (m.group(3) or m.group(6)).lower()
        target = chess.parse_square(m.group(4) or m.group(5))
        seen = [b for b in boards if _piece(b, dest, piece) and _piece(b, target, tname)]
        if seen and not any(_reaches(b, dest, target, xray=False) for b in seen):
            out.append(Failure("defence", m.group(0),
                               f"a {piece} on {chess.square_name(dest)} does not defend the "
                               f"{tname} on {chess.square_name(target)}"))
    return out


def _check_move_defends_kind(m, piece: str, dest: int, tname: str, boards: list) -> list:
    """'moving the queen from d1 to b3 to defend the knight' (9.e3, after
    ...Bxh5 the knight on f3 is pinned and attacked): no square named, so
    'the knight' is the one under attack when one is; the piece on `dest`
    must defend it in some position where it stands there."""
    kind = NAME_TO_TYPE[tname]
    seen = []
    for b in boards:
        p = b.piece_at(dest)
        if p is None or PIECE_NAMES[p.piece_type] != piece:
            continue
        ours = [sq for sq in b.pieces(kind, p.color) if sq != dest]
        hit = [sq for sq in ours if b.attackers(not p.color, sq)]
        targets = hit or ours
        if targets:
            seen.append((b, targets))
    if not seen or any(_reaches(b, dest, t, xray=False) for b, ts in seen for t in ts):
        return []
    b, ts = seen[0]
    names = " or ".join(chess.square_name(t) for t in ts)
    return [Failure("defence", m.group(0),
                    f"a {piece} on {chess.square_name(dest)} does not defend the {tname} "
                    f"on {names}")]


# --- The check ------------------------------------------------------------

def check_sentence(s: str, boards: list, move: Optional[chess.Move] = None,
                   conseq: Optional[list] = None) -> list:
    """`conseq`: the positions a claim about consequences ('creates a
    passed pawn', 'removes the threat') must hold in. Default: all."""
    conseq = conseq if conseq else boards
    out = []
    out += _check_structure(s, boards[0] if boards else None, conseq)
    out += _check_move_defends(s, boards)
    out += _check_threat_removed(s, conseq)
    out += _check_keeps(s, conseq)
    out += _check_placement(s, boards)
    out += _check_attacks(s, boards)
    out += _check_lost_attack(s, boards)
    out += _check_defences(s, boards)
    out += _check_lost_defence(s, boards, move)
    out += _check_undefended(s, boards, conseq)
    out += _check_pins(s, boards)
    out += _check_forks(s, boards)
    out += _check_material(s, boards)
    out += _check_legal(s, boards)
    for f in out:
        f.sentence = s.strip()
    return out


def _named_candidates(s: str, candidates: list) -> list:
    """The candidate moves a sentence names in SAN ('Bxd4', '...g5')."""
    out = []
    for before, mv, _ in candidates:
        san = before.san(mv).rstrip("+#")
        if re.search(r"(?<![\w])(?:\.\.\.)?" + re.escape(san) + r"(?![\w])", s):
            out.append((before, mv, _))
    return out


def _check_with_candidates(s: str, boards: list, candidates: list) -> list:
    """A sentence about one of several candidate moves ("Nxd4 leaves the
    pawn on e5 undefended") is judged on the position after THAT move. When
    it names one candidate, that one; when it names several or none, a
    claim fails only if it fails for every candidate and for the position
    as it is: the check must not remove a true sentence it cannot place."""
    named = _named_candidates(s, candidates)
    options = [(b, mv, line) for b, mv, line in (named if len(named) == 1 else candidates)]
    results = []
    for before, mv, line in options:
        results.append(check_sentence(s, [before] + list(line) + boards, mv, list(line)))
    if len(named) != 1:
        results.append(check_sentence(s, boards, None, boards))
    if not results:
        return []
    keys = [{(f.kind, f.claim) for f in r} for r in results]
    common = set.intersection(*keys)
    return [f for f in results[0] if (f.kind, f.claim) in common]


def ground(text: str, boards: list, move: Optional[chess.Move] = None,
           keep: str = "", played: Optional[list] = None,
           better: Optional[list] = None, candidates: Optional[list] = None) -> list:
    """All failed claims in `text`. Sentences containing `keep` (the
    code-written first sentence) are not checked. `played` and `better` are
    the positions after the played move and along the engine's choice:
    consequence claims under 'Better was' are checked on `better`, all
    others on `played`. `candidates`: [(board before, move, [positions
    after it, in order])] for an answer that weighs several moves; see
    _check_with_candidates."""
    out = []
    moves = line_moves(played)
    text = text.replace("\u2019", "'").replace("\u2018", "'")   # curly apostrophes
    for _, prefix, parts in split_sentences(text):
        conseq = better if "better" in prefix.lower() else played
        for s in parts:
            # The code-written sentences (possibly more than one) are not
            # the model's and are not checked.
            if keep and (keep.strip(" .") in s or (s.strip() and s.strip() in keep)):
                continue
            if candidates:
                out += _check_with_candidates(s, boards, candidates)
                continue
            found = check_sentence(s, boards, move, conseq)
            if played:
                for f in _check_shelter(s, boards[0] if boards else None, played, better):
                    f.sentence = s.strip()
                    found.append(f)
            if move is not None and boards:
                mover = boards[0].color_at(move.from_square)
                extra = _check_bishop_pair(s, conseq, mover) + _check_captured(s, boards[0], move)
                if "what happened" in prefix.lower() or "better" in prefix.lower():
                    extra += _check_protects_enemy(s, boards, mover)
                for f in extra:
                    f.sentence = s.strip()
                    found.append(f)
            if "what now" in prefix.lower():
                for f in _check_continuation(s, moves) + _check_gone_piece(s, moves):
                    f.sentence = s.strip()
                    found.append(f)
            out += found
    return out


def remove_failed(text: str, failures: list) -> str:
    """Drop every sentence with a failed claim. A line left with only its
    heading is dropped too, except 'What happened' (the code-written first
    sentence goes back in after this)."""
    def norm(x):
        return x.strip().replace("\u2019", "'").replace("\u2018", "'")
    bad = {norm(f.sentence) for f in failures}
    lines = text.split("\n")
    for i, prefix, parts in split_sentences(text):
        kept = [p for p in parts if norm(p) not in bad]
        if len(kept) == len(parts):
            continue
        if not kept and prefix and "what happened" not in prefix.lower():
            lines[i] = None
        else:
            lines[i] = (prefix + " ".join(kept)).rstrip()
    return "\n".join(line for line in lines if line is not None).strip()


def correction_text(failures: list) -> str:
    seen, out = set(), []
    for f in failures:
        key = (f.kind, f.correction)
        if key not in seen:
            seen.add(key)
            out.append(f"- You wrote \"{f.claim}\", but {f.correction}.")
    return "\n".join(out)
