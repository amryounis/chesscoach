"""
Tier 2: concepts verified with Stockfish, computed only for the moments the
coach comments on. Each one answers a question the static facts can't:

  threats      What would the opponent do if it were their move again?
               (a null move: pass the turn and search). A real threat is
               one that gains them a clear amount, not every attack.
  ignored      Did the move leave a threat that existed before it standing?
  piece role   What was the moved piece holding together? Take it off the
               board and search: what the opponent can then do, beyond
               simply being a piece up, is the job it was doing.
  equal moves  Two moves within a few percent of winning chances are
               equally good; the difference is not worth inventing reasons.
  key facts    The relevance filter: of everything known about a moment,
               the few facts that change the evaluation or that the
               engine's line acts on, ranked, so the model leads with them.

Depth is `config.settings.concept_depth` (14 by default; the main
analysis stays at `depth`). Every search clears the hash first so results
don't depend on what was analysed before.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import chess
import chess.engine

from . import config
from .assessment import win_pct
from .describe import PIECE_NAMES, describe_move, piece_label, side_name

VALUES = {chess.PAWN: 100, chess.KNIGHT: 300, chess.BISHOP: 300,
          chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0}

# A threat or a role counts when it is worth at least this many points of
# winning chance for the side that would gain.
THREAT_WIN_PCT = 10.0
# ...and the threat move itself must add this much over the next-best use
# of the free move. Measured on the Fritz game: hxg6 +8, ...Bxh5 +7,
# ...dxe3 +9 (real threats) against Nxc6 +0 (only the extra tempo).
THREAT_SPECIFIC_PCT = 5.0
ROLE_WIN_PCT = 12.0
EQUAL_WIN_PCT = 3.0


@dataclass
class Search:
    cp: Optional[int]           # White's view
    mate: Optional[int]
    pv: list                    # [chess.Move]


def search(engine, board: chess.Board, depth: Optional[int] = None,
           exclude: Optional[chess.Move] = None) -> Search:
    """One clean search of a position given as a FEN (no move stack, so a
    null move or a removed piece is just a position to the engine).
    `exclude`: search every legal move except this one."""
    b = chess.Board(board.fen())
    try:
        engine.configure({"Clear Hash": None})
    except Exception:
        pass
    roots = None
    if exclude is not None:
        roots = [m for m in b.legal_moves if m != exclude]
        if not roots:
            return Search(None, None, [])
    info = engine.analyse(b, chess.engine.Limit(depth=depth or config.settings.concept_depth),
                          root_moves=roots)
    score = info["score"].white()
    return Search(None if score.is_mate() else score.score(),
                  score.mate() if score.is_mate() else None,
                  list(info.get("pv", [])))


def _win(s: Search, color: bool) -> float:
    return win_pct(s.cp, s.mate, color == chess.WHITE)


def _line_words(board: chess.Board, pv: list, n: int = 1) -> str:
    b = board.copy(stack=False)
    words = []
    for mv in pv[:n]:
        if mv not in b.legal_moves:
            break
        words.append(describe_move(b, mv))
        b.push(mv)
    return "; then ".join(words)


# --- Threats --------------------------------------------------------------

@dataclass
class Threat:
    side: bool                  # who threatens
    move: chess.Move
    words: str                  # "White captures Black's pawn on e5 with ..."
    gain: float                 # winning chance gained, in points
    mate: bool = False
    pv: list = field(default_factory=list)   # the engine's line with the free move

    def text(self) -> str:
        what = ("it would be checkmate" if self.mate else
                f"worth about {self.gain:.0f} points of winning chance")
        return f"{side_name(self.side)} threatens this: {self.words} ({what})"


def _forcing(board: chess.Board, move: chess.Move) -> bool:
    return (board.is_capture(move) or board.gives_check(move)
            or move.promotion is not None)


def threat(engine, board: chess.Board, now: Optional[Search] = None) -> Optional[Threat]:
    """What the side NOT to move threatens: pass the move to it and search.

    A threat must be a forcing move (capture, check, promotion) or a mate,
    and its value is what THAT move adds over the next-best use of the free
    move: having two moves in a row is worth something by itself, and that
    is not a threat. None when in check (no null move) or nothing qualifies.
    """
    if board.is_check():
        return None
    side = not board.turn
    nb = board.copy(stack=False)
    nb.push(chess.Move.null())
    if nb.is_check():                                 # passing exposes the king
        return None
    passed = search(engine, nb)
    if not passed.pv:
        return None
    move = passed.pv[0]
    mate = passed.mate is not None and (passed.mate > 0) == (side == chess.WHITE)
    if not mate and not _forcing(nb, move):
        return None
    now = now or search(engine, board)
    gain = _win(passed, side) - _win(now, side)
    if not mate:
        if gain < THREAT_WIN_PCT:
            return None
        rest = search(engine, nb, exclude=move)
        specific = _win(passed, side) - _win(rest, side) if rest.pv else gain
        if specific < THREAT_SPECIFIC_PCT:
            return None
        gain = min(gain, specific)
    return Threat(side, move, describe_move(nb, move), gain, mate, list(passed.pv))


# --- The role of a piece --------------------------------------------------

@dataclass
class Role:
    square: int
    piece: chess.Piece
    stops: str                  # what the opponent could do without it
    drop: float                 # winning chance it is worth beyond its material
    move: Optional[chess.Move] = None   # the opponent's move it prevents

    def text(self, board: chess.Board, moved_to: Optional[int] = None) -> str:
        name = f"the {PIECE_NAMES[self.piece.piece_type]} on {chess.square_name(self.square)}"
        lead = (f"Before the move, {name} was holding something together"
                if moved_to is not None else f"{name.capitalize()} is holding something together")
        return (f"{lead}: without it, {self.stops} "
                f"(worth about {self.drop:.0f} points of winning chance beyond the piece itself)")


def role(engine, board: chess.Board, square: int,
         now: Optional[Search] = None) -> Optional[Role]:
    """Take the piece on `square` off the board, give the move to the
    opponent and search. The loss beyond the piece's own value is what it
    was holding; the opponent's best move then shows what."""
    p = board.piece_at(square)
    if not p or p.piece_type in (chess.KING, chess.PAWN):
        return None
    side = p.color
    # The position as it stands, with the opponent to move.
    base = board.copy(stack=False)
    if base.turn == side:
        if base.is_check():
            return None
        base.push(chess.Move.null())
    without = base.copy(stack=False)
    without.remove_piece_at(square)
    if not without.is_valid():
        return None
    with_s = search(engine, base) if now is None else now
    without_s = search(engine, without)
    if not without_s.pv:
        return None
    # Material-adjusted: the piece's own value is expected to go; only the
    # loss beyond that is a role. Compare in centipawns, then winning chance.
    sign = 1 if side == chess.WHITE else -1
    if with_s.mate is None and without_s.mate is None:
        adj = Search(without_s.cp + sign * VALUES[p.piece_type], None, without_s.pv)
    else:
        adj = without_s
    drop = _win(with_s, side) - _win(adj, side)
    if drop < ROLE_WIN_PCT:
        return None
    # The opponent's best move without the piece must be one the piece
    # actually stops: it covers the target square or stands in the way,
    # and with the piece there the opponent would not play it. Otherwise
    # the loss is a general weakening, not a job this piece was doing
    # (the first version reported the bishop on c8 'holding' g6).
    stop = without_s.pv[0]
    covers = (stop.to_square in base.attacks(square)
              or square in chess.SquareSet(chess.between(stop.from_square, stop.to_square)))
    if not covers or (with_s.pv and with_s.pv[0] == stop):
        return None
    return Role(square, p, _line_words(without, without_s.pv), drop, stop)


# --- Putting it together for one move -------------------------------------

@dataclass
class Concepts:
    key_facts: list = field(default_factory=list)     # ranked, at most 5
    equal_sentence: str = ""                          # written into the comment by code
    loose_sentence: str = ""                          # likewise: a piece of ours the line takes
    threats: list = field(default_factory=list)       # [str]
    notes: list = field(default_factory=list)         # everything, for the report
    # Structured, for the game summary (phase 5): what the move got wrong.
    threat_before: Optional[Threat] = None            # the opponent's threat when moving
    threat_ignored: Optional[Threat] = None           # the threat still stands after it
    job_given_up: Optional[Role] = None               # what the moved piece held, now lost
    left_loose: list = field(default_factory=list)    # own squares left hanging, line takes


def equal_alternative(board_before: chess.Board, played: chess.Move, eng_before):
    """(the other move in words, played move is the engine's first choice)
    when the played move and another candidate are within a few points of
    winning chance; else None. Works both ways: the played move may be the
    engine's second choice, or its first with a second just as good."""
    san = board_before.san(played)
    if not eng_before.best_move:
        return None
    me = board_before.turn == chess.WHITE
    best = win_pct(eng_before.score_cp, eng_before.score_mate, me)
    lines = list(zip(eng_before.alt_scores, eng_before.alt_lines))
    if best is None:
        return None
    # Mates are not "practically equal" to anything: 39...Ne5# mates, 39...e2
    # only wins (both are 100% winning chance, which is where the band fails).
    after = board_before.copy(stack=False)
    after.push(played)
    if (after.is_checkmate() or eng_before.score_mate is not None
            or any(mate is not None for (_, mate) in eng_before.alt_scores[:1])):
        return None
    if eng_before.best_move == san:
        if not lines or not lines[0][1][1]:
            return None
        (cp, mate), (_, line) = lines[0]
        second = win_pct(cp, mate, me)
        if second is None or best - second >= EQUAL_WIN_PCT:
            return None
        other_san, played_first = line[0], True
    else:
        for (cp, mate), (_, line) in lines:
            if line and line[0] == san:
                mine = win_pct(cp, mate, me)
                if mine is not None and best - mine < EQUAL_WIN_PCT:
                    break
        else:
            return None
        other_san, played_first = eng_before.best_move, False
    try:
        other = describe_move(board_before, board_before.parse_san(other_san))
    except ValueError:
        return None
    return other, played_first


def equal_note(board_before: chess.Board, played: chess.Move, eng_before) -> str:
    """Key fact: the two moves are practically equal, so no reason gets invented."""
    alt = equal_alternative(board_before, played, eng_before)
    if not alt:
        return ""
    other, first = alt
    if first:
        return (f"Another move ({other}) is practically as good as this one; "
                f"do not invent a reason why one is better")
    return (f"The engine's first choice was a different move ({other}), but the "
            f"two are practically equal; do not invent a reason why one is better")


def equal_sentence(board_before: chess.Board, played: chess.Move, eng_before) -> str:
    """The same fact as a sentence for the comment itself: the model was
    given it as a key fact and left it out twice, so code writes it."""
    alt = equal_alternative(board_before, played, eng_before)
    if not alt:
        return ""
    other, first = alt
    if first:
        return f"Another move ({other}) is practically as good."
    return (f"The engine preferred another move ({other}), but the two are "
            f"practically equal.")


def _pv_captures(board: chess.Board, line: list, square: int, plies: int = 4) -> bool:
    """Does the engine's line capture whatever stands on `square`? (Moving
    the piece away, as in cxd5 for a pawn on c4, does not count.)"""
    owner = board.color_at(square)
    b = board.copy(stack=False)
    for san in line[:plies]:
        try:
            mv = b.parse_san(san)
        except ValueError:
            return False
        if mv.from_square == square:
            return False                     # it moved away first
        if mv.to_square == square and b.is_capture(mv) and b.turn != owner:
            return True
        b.push(mv)
    return False


def _san_if_legal(board: chess.Board, move: Optional[chess.Move]) -> str:
    return board.san(move) if move is not None and move in board.legal_moves else ""


def analyse_move(engine, board_before: chess.Board, played: chess.Move,
                 eng_before, eng_after, effects: list, comparison: list) -> Concepts:
    """Tier 2 for one move, plus the ranked key facts (lower rank first)."""
    c = Concepts()
    after = board_before.copy(stack=False)
    after.push(played)
    them = not board_before.turn
    opp_best = eng_after.best_line[0] if eng_after.best_line else ""
    ranked = []

    # 1. The opponent's threat before the move, and whether it still stands
    #    (it is still their best move afterwards).
    t = threat(engine, board_before)
    if t:
        c.threat_before = t
        c.threats.append(t.text())
        if opp_best and _san_if_legal(after, t.move) == opp_best:
            c.threat_ignored = t
            ranked.append((0, f"The move did not deal with a threat. Before it, "
                              f"{t.text()}. That is still {side_name(them)}'s best "
                              f"move now"))
        else:
            ranked.append((3, f"Before the move, {t.text()}. After the move that is "
                              f"no longer {side_name(them)}'s best move"))

    # 2. What the move itself threatens.
    t2 = threat(engine, after)
    if t2:
        c.threats.append(t2.text())
        ranked.append((2, f"The move threatens checkmate: {t2.words}" if t2.mate else
                          f"The move creates a threat: {t2.words} (worth about "
                          f"{t2.gain:.0f} points of winning chance if it is allowed)"))

    # 3. The job the moved piece was doing, and whether the move gave it up:
    #    what the piece prevented is now the opponent's best move.
    r = role(engine, board_before, played.from_square)
    if r:
        txt = r.text(board_before, moved_to=played.to_square)
        if opp_best and _san_if_legal(after, r.move) == opp_best:
            c.job_given_up = r
            ranked.append((0, txt + "; the move gave that job up, and that is now "
                              f"{side_name(them)}'s best move"))
        else:
            ranked.append((4, txt))
        c.notes.append(txt)

    # 4. Equal moves: no reason to invent.
    eq = equal_note(board_before, played, eng_before)
    if eq:
        ranked.append((0, eq))
        c.equal_sentence = equal_sentence(board_before, played, eng_before)

    # 5. Relevance filter over the static facts: keep what the engine's
    #    lines act on or what changes the evaluation.
    for e in effects:
        low = e.lower()
        if "ignores a threat" in low or "no longer defends" in low:
            ranked.append((1, e))
        elif "castling" in low or "removes the threat" in low or "out of danger" in low:
            ranked.append((2, e))
    # The first two differences in favour of the engine's choice.
    taken = 0
    for line in comparison:
        if line.startswith("- In favour of the PLAYER"):
            break
        if (line.startswith("  - ") and "number of legal moves" not in line
                and "nothing measurable" not in line and taken < 3):
            ranked.append((3, "Compared with the engine's choice: " + line[4:]))
            taken += 1
    mentioned = " ".join(t for _, t in ranked)
    for sq, p in after.piece_map().items():
        if f" on {chess.square_name(sq)}" in mentioned:
            continue
        if (p.piece_type != chess.KING and after.attackers(not p.color, sq)
                and not after.attackers(p.color, sq)
                and _pv_captures(after, eng_after.best_line, sq)):
            ranked.append((1, f"The {piece_label(after, sq)} has no defender, and the "
                              f"engine's line goes for it"))
            if p.color == board_before.turn:
                c.left_loose.append(sq)
            if p.color == board_before.turn and not c.loose_sentence:
                c.loose_sentence = (f"It leaves the {PIECE_NAMES[p.piece_type]} on "
                                    f"{chess.square_name(sq)} without a defender, and the "
                                    f"engine's line captures it.")

    seen, out = set(), []
    for _, text in sorted(ranked, key=lambda x: x[0]):
        if text not in seen:
            seen.add(text)
            out.append(text)
    c.key_facts = out[:5]
    c.notes += c.threats
    return c
