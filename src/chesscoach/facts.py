"""
Tier 1 facts: cheap, exact statements about a position, computed with
python-chess. Each function returns plain-English lines that can go into a
prompt or come back from an agent tool unchanged.

These cover the gaps the model experiments exposed: no model mentioned that
Black could still castle, several miscounted material ("material is equal"
two minor pieces down), none noticed the undefended bishop on c8, and two
lost track of a pawn that changed file in a line (25.bxa5 then axb6).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import chess

from .describe import (PIECE_NAMES, board_to_piece_list, describe_move,
                       describe_move_effects, piece_label, side_name)

VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
          chess.ROOK: 5, chess.QUEEN: 9}
ORDER = [chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT, chess.PAWN]
_WORDS = ["no", "a", "two", "three", "four", "five", "six", "seven", "eight",
          "nine", "ten"]


def count_phrase(n: int, piece_type: int) -> str:
    """'a bishop', 'two rooks', 'five pawns'."""
    name = PIECE_NAMES[piece_type]
    word = _WORDS[n] if n < len(_WORDS) else str(n)
    if n == 1 and piece_type == chess.PAWN:
        word = "a"
    return f"{word} {name}" + ("s" if n != 1 else "")


def join_words(items: list) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


# --- Material -------------------------------------------------------------

def material_counts(board: chess.Board, color: bool) -> dict:
    return {pt: len(board.pieces(pt, color)) for pt in ORDER}


def material_lines(board: chess.Board) -> list:
    """
    Material named piece by piece, then the difference in pieces, then the
    net in pawns. Models given '+420 cp' or a bare piece list miscounted;
    'White is up a bishop and a knight' leaves nothing to add up.
    """
    w, b = material_counts(board, chess.WHITE), material_counts(board, chess.BLACK)
    out = []
    for color, c in ((chess.WHITE, w), (chess.BLACK, b)):
        has = [count_phrase(c[pt], pt) for pt in ORDER if c[pt]]
        out.append(f"{side_name(color)} has: the king"
                   + (", " + ", ".join(has) if has else " only"))

    w_extra = [count_phrase(w[pt] - b[pt], pt) for pt in ORDER if w[pt] > b[pt]]
    b_extra = [count_phrase(b[pt] - w[pt], pt) for pt in ORDER if b[pt] > w[pt]]
    net = sum(VALUES[pt] * (w[pt] - b[pt]) for pt in ORDER)

    if not w_extra and not b_extra:
        out.append("Material is level: both sides have exactly the same pieces "
                   "and pawns.")
        return out
    parts = []
    if w_extra:
        parts.append(f"White is up {join_words(w_extra)}")
    if b_extra:
        parts.append(f"Black is up {join_words(b_extra)}")
    diff = "; ".join(parts)
    if net == 0:
        out.append(f"Material difference: {diff}. In total the material is "
                   "roughly balanced, but the pieces are different.")
    else:
        ahead = "White" if net > 0 else "Black"
        out.append(f"Material difference: {diff}. In total {ahead} is ahead "
                   f"by about {abs(net)} {'pawns' + chr(39) if abs(net) != 1 else 'pawn' + chr(39) + 's'} "
                   "worth of material.")
    return out


def material_summary(board: chess.Board) -> str:
    """The one-line difference, for places that only need that."""
    return material_lines(board)[-1]


# --- Castling -------------------------------------------------------------

def castling_lines(board: chess.Board) -> list:
    """
    Castling rights for both sides, and whether the side to move can castle
    on this move. Rights matter even when castling is not possible yet:
    a check that forces the king to move takes them away for good.
    """
    out = []
    for color in (chess.WHITE, chess.BLACK):
        name = side_name(color)
        k = board.has_kingside_castling_rights(color)
        q = board.has_queenside_castling_rights(color)
        if k and q:
            line = f"{name} can still castle on either side (has not lost the right)"
        elif k:
            line = (f"{name} can still castle kingside (has not lost the right); "
                    "queenside castling is no longer possible")
        elif q:
            line = (f"{name} can still castle queenside (has not lost the right); "
                    "kingside castling is no longer possible")
        else:
            line = f"{name} can no longer castle"
        if (k or q) and color == board.turn:
            legal = [m for m in board.legal_moves if board.is_castling(m)]
            if legal:
                sides = join_words(["kingside" if board.is_kingside_castling(m)
                                    else "queenside" for m in legal])
                line += f". Castling {sides} is legal on this move"
            elif board.is_check():
                line += ". It cannot castle on this move because its king is in check"
            else:
                line += (". It cannot castle on this move (a square between king "
                         "and rook is occupied or attacked)")
        out.append(line + ".")
    return out


# --- Undefended pieces ----------------------------------------------------

def loose_pieces(board: chess.Board, color: bool | None = None) -> list:
    """Squares of pieces (not pawns or kings) with no defender at all."""
    found = []
    for sq, p in sorted(board.piece_map().items()):
        if p.piece_type in (chess.KING, chess.PAWN):
            continue
        if color is not None and p.color != color:
            continue
        if not board.attackers(p.color, sq):
            found.append(sq)
    return found


def loose_piece_lines(board: chess.Board) -> list:
    """
    Undefended pieces, attacked or not ('loose pieces drop off'). Attacked
    ones are already in the attacked list; the unattacked ones are what a
    single check or fork can win, like the bishop on c8.
    """
    out = []
    for color in (chess.WHITE, chess.BLACK):
        sqs = loose_pieces(board, color)
        if not sqs:
            out.append(f"Every {side_name(color)} piece (other than pawns and "
                       "the king) is defended.")
            continue
        items = []
        for sq in sqs:
            attacked = bool(board.attackers(not color, sq))
            items.append(piece_label(board, sq)
                         + (" (attacked now)" if attacked else " (not attacked yet)"))
        out.append(f"Undefended {side_name(color)} pieces: " + "; ".join(items))
    return out


# --- Legal moves ----------------------------------------------------------

def legal_move_line(board: chess.Board, with_list: bool = False) -> str:
    moves = list(board.legal_moves)
    who = side_name(board.turn)
    if board.is_checkmate():
        return f"{who} is checkmated and has no legal moves."
    if not moves:
        return f"{who} has no legal moves: stalemate."
    text = f"{who} (to move) has {len(moves)} legal move{'s' if len(moves) != 1 else ''}"
    if board.is_check():
        text += " (it must answer a check)"
    if with_list:
        text += ": " + ", ".join(board.san(m) for m in moves)
    return text + "."


# --- Following a line -----------------------------------------------------

@dataclass
class LineStep:
    san: str
    text: str                      # the move in words
    effects: list = field(default_factory=list)
    notes: list = field(default_factory=list)   # file changes etc.


@dataclass
class LineResult:
    steps: list                    # [LineStep]
    board: chess.Board             # position at the end (or where it stopped)
    error: str = ""                # why the line stopped early, if it did

    def text(self) -> str:
        out = []
        for i, s in enumerate(self.steps, 1):
            out.append(f"{i}. {s.text}")
            out += [f"   - {e}" for e in s.effects + s.notes]
        if self.error:
            out.append(f"STOPPED: {self.error}")
        return "\n".join(out)


def _pawn_origin_files(start: chess.Board) -> dict:
    """square -> the file letter each pawn stood on at the start of a line."""
    return {sq: chess.square_name(sq)[0] for sq in chess.SQUARES
            if (p := start.piece_at(sq)) and p.piece_type == chess.PAWN}


_SQUARE_ONLY = re.compile(r"^(?:x|takes |captures? (?:on )?)?([a-h][1-8])[+#]?$", re.I)


def resolve_move(board: chess.Board, text: str):
    """
    (move, '') for a move as a person or a model might write it, or
    (None, message). Besides SAN and UCI it accepts a bare destination
    ("d4", "xd4", "takes d4") when exactly one legal move goes there. When
    a move is ambiguous ("Re1+" with two rooks able to go to e1) or a square
    has several candidates, the message names each candidate in words,
    instead of calling the move illegal.
    """
    t = (text or "").strip().rstrip("!?").strip()
    t = t.replace("0-0-0", "O-O-O").replace("0-0", "O-O")
    try:
        return board.parse_san(t), ""
    except chess.AmbiguousMoveError:
        to = re.search(r"([a-h][1-8])", t)
        letter = t[0] if t and t[0] in "KQRBN" else ""
        cands = [m for m in board.legal_moves
                 if to and chess.square_name(m.to_square) == to.group(1)
                 and board.san(m)[:1] == (letter or board.san(m)[:1])]
        return None, _candidates(board, t, cands)
    except ValueError:
        pass
    try:
        mv = chess.Move.from_uci(t)
        if mv in board.legal_moves:
            return mv, ""
    except ValueError:
        pass
    m = _SQUARE_ONLY.match(t)
    if m:
        sq = chess.parse_square(m.group(1))
        cands = [mv for mv in board.legal_moves if mv.to_square == sq]
        if t.lower().startswith(("x", "take", "capture")):
            cands = [mv for mv in cands if board.is_capture(mv)]
        if len(cands) == 1:
            return cands[0], ""
        if cands:
            return None, _candidates(board, t, cands)
    legal = ", ".join(board.san(mv) for mv in board.legal_moves)
    return None, (f"'{text}' is not a legal move for {side_name(board.turn)} here. "
                  f"Legal moves: {legal or 'none'}")


def _candidates(board: chess.Board, text: str, cands: list) -> str:
    opts = "; ".join(f"{board.san(m)} ({describe_move(board, m)})" for m in cands)
    return (f"'{text}' could mean more than one move: {opts}. "
            f"Use one of these exactly.")


def play_line(board: chess.Board, moves: list, effects: bool = True) -> LineResult:
    """
    Play a line move by move, describing each one, and keep track of what
    the models lost: a pawn that captures changes file (the b-pawn that
    takes on a5 is now an a-pawn), and a move that is legal or illegal is
    decided by the board, never by the reader.

    `moves` are SAN or UCI strings. An illegal or unreadable move stops the
    line with an error that lists the legal moves at that point.
    """
    b = board.copy()
    origin = _pawn_origin_files(b)            # current square -> start file
    steps = []
    for i, mv_text in enumerate(moves, 1):
        mv, why = resolve_move(b, mv_text)
        if mv is None:
            if "could mean" in why:
                return LineResult(steps, b, f"move {i}: {why}")
            legal = ", ".join(b.san(m) for m in b.legal_moves)
            return LineResult(steps, b, f"move {i} '{mv_text}' is not legal "
                              f"for {side_name(b.turn)} in that position. "
                              f"Legal moves there: {legal or 'none'}")
        san = b.san(mv)
        step = LineStep(san=san, text=describe_move(b, mv))
        if effects:
            step.effects = describe_move_effects(b, mv)
        p = b.piece_at(mv.from_square)
        captured_sq = mv.to_square
        if b.is_en_passant(mv):
            captured_sq = mv.to_square + (-8 if b.turn == chess.WHITE else 8)
        origin.pop(captured_sq, None)
        if p.piece_type == chess.PAWN:
            start_file = origin.pop(mv.from_square, chess.square_name(mv.from_square)[0])
            new_file = chess.square_name(mv.to_square)[0]
            if mv.promotion:
                step.notes.append(f"The pawn promotes on {chess.square_name(mv.to_square)}"
                                  " and is no longer a pawn")
            else:
                origin[mv.to_square] = start_file
                from_file = chess.square_name(mv.from_square)[0]
                if from_file != new_file:
                    was = (f"{from_file}-pawn" if start_file == from_file else
                           f"{from_file}-pawn (the {start_file}-pawn when the line began)")
                    who = side_name(p.color)
                    note = (f"The capture moves {who}'s {was} to the {new_file}-file: "
                            f"it is now {who}'s {new_file}-pawn on "
                            f"{chess.square_name(mv.to_square)}")
                    left = (b.pieces(chess.PAWN, p.color)
                            & chess.BB_FILES[chess.FILE_NAMES.index(from_file)]
                            & ~chess.BB_SQUARES[mv.from_square])
                    if not left:
                        note += f", and {who} has no pawn left on the {from_file}-file"
                    step.notes.append(note)
        b.push(mv)
        steps.append(step)
    return LineResult(steps, b)


# --- Everything at once ---------------------------------------------------

def position_facts(board: chess.Board, engine=None, legal_list: bool = False) -> str:
    """
    All Tier 1 facts for one position, as one text block. This is what the
    'Explain this position' prompt and the agent's position_facts tool use,
    and what the answer-key harness checks for recall.
    """
    from .engine import EngineAnalysis
    from .features import extract_features
    from .tactics import extract_tactical_features

    feats = extract_features(board)
    sections = [
        f"SIDE TO MOVE: {side_name(board.turn)}",
        "PIECES:\n" + board_to_piece_list(board),
        "MATERIAL:\n" + "\n".join(f"- {l}" for l in material_lines(board)),
        "LEGAL MOVES:\n- " + legal_move_line(board, with_list=legal_list),
        "TACTICAL FACTS:\n" + extract_tactical_features(board, engine or EngineAnalysis()),
        "POSITIONAL FEATURES:\n" + feats.summary(),
    ]
    return "\n\n".join(sections)
