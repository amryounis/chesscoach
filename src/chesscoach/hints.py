"""
Hints that make the player think, and the blunder alert.

A hint gives away a little at a time, so the player finds the move with as
little help as possible:

  1. what is going on   a threat to meet, a tactic, a mate, an only move,
                        or nothing urgent and what kind of move fits
  2. the square         where the move goes (Chessmaster's first hint)
  3. the piece          which piece goes there
  4. the move           and what it does, with the engine's line

Every step is worked out by code from Stockfish's search; no model is
involved, so a hint comes after one search and can't be wrong about the
board.

The blunder alert asks "are you sure?" before a move that throws away a
lot of winning chance. It never says why: finding what the opponent can do
is the lesson. Measured in winning chance, not pawns, as everywhere else
here: losing a pawn and a half matters at 0.0 and not at +8.
"""

from __future__ import annotations

import re
from typing import Optional

import chess

from . import config
from .assessment import label_for_drop, win_pct
from .classify import ONLY_MOVE
from .describe import PIECE_NAMES, describe_move, side_name
from .engine import run_stockfish
from .motifs import _balance, line_motifs
from .motifs import describe as motif_words

STEPS = 4
# How far into the engine's line a hint looks for a tactic and its payoff,
# and how much of it step 4 shows: a fork answered by taking the pawn, then
# won through a pin, pays off at ply 7.
LOOK_PLIES = 8
# Alert levels offered in Settings (points of winning chance lost; 0 = off)
ALERT_LEVELS = {0: "Off", 30: "Blunders", 20: "Mistakes and blunders",
                10: "Inaccuracies too"}


def _win(cp, mate, color: bool) -> float:
    w = win_pct(cp, mate, color == chess.WHITE)
    return 50.0 if w is None else w


def _circle(sq: int, brush: str = "green") -> dict:
    return {"orig": chess.square_name(sq), "brush": brush}


def _arrow(move: chess.Move, brush: str = "green") -> dict:
    return {"orig": chess.square_name(move.from_square),
            "dest": chess.square_name(move.to_square), "brush": brush}


# Step 1 for a tactic: the idea, without the move
TACTIC_HINT = {
    "hanging_piece": "Something is left unprotected. Look at every capture.",
    "fork": "There is a fork: one piece can attack two things at once.",
    "pin": "There is a pin to use.",
    "skewer": "There is a skewer: attack a big piece with a smaller one behind it.",
    "discovered_attack": "There is a discovered attack: move one piece to uncover another.",
    "double_check": "There is a double check.",
    "capturing_defender": "Take away a defender, and something falls.",
    "sacrifice": "Giving up material can pay off here.",
    "promotion": "A pawn can promote.",
    "advanced_pawn": "Your advanced pawn is the key.",
}


def _quiet(board: chess.Board, move: chess.Move) -> str:
    """Step 1 when nothing tactical is going on: what kind of move fits."""
    p = board.piece_at(move.from_square)
    home = 0 if board.turn == chess.WHITE else 7
    if board.is_castling(move):
        return "Nothing urgent. Think about your king's safety."
    if p and p.piece_type in (chess.KNIGHT, chess.BISHOP) \
            and chess.square_rank(move.from_square) == home:
        return "Nothing urgent. Bring a piece that hasn't moved yet into the game."
    if p and p.piece_type == chess.PAWN:
        return "No tactic here. The best plan starts with a pawn move."
    if board.is_capture(move):
        return "No big tactic here, but there is a capture worth looking at."
    return "No tactic here. Find a better square for one of your pieces."


def _situation(board: chess.Board, move: chess.Move, line: list, mate: Optional[int],
               threat) -> tuple:
    """(text, shapes) for step 1."""
    me, opp = board.turn, side_name(not board.turn)
    if mate is not None and (mate > 0) == (me == chess.WHITE):
        n = abs(mate)
        text = ("There is checkmate in one move." if n == 1 else
                f"You have a forced mate in {n}. Look at the checks first.")
        return text, []
    if board.is_check():
        return ("You are in check. Of the ways out (take the checking piece, block, "
                "move the king), one is clearly best."), []
    tags = [t for t in line_motifs(board, line, plies=LOOK_PLIES)
            if t not in ("mate", "back_rank_mate")]
    gain = _gain(board, line)
    if tags and gain >= 2:
        return TACTIC_HINT.get(tags[0], "There is a tactic for you here."), []
    if threat is not None:
        nb = board.copy(stack=False)
        nb.push(chess.Move.null())
        if threat.mate:
            return f"Watch out: {opp} threatens checkmate with {nb.san(threat.move)}.", \
                [_arrow(threat.move, "threat")]
        return f"Watch out: {opp} threatens to {_infinitive(threat.words, opp, me)}.", \
            [_arrow(threat.move, "threat")]
    if gain >= 2:
        return "You can win material here. Look at what is not defended.", []
    if tags:
        return TACTIC_HINT.get(tags[0], "There is a tactic for you here."), []
    return _quiet(board, move), []


def _attacks(board: chess.Board, move: chess.Move) -> list:
    """What the moved piece newly attacks that is worth attacking: the king,
    a piece worth more than it, or an undefended piece (not pawns)."""
    me = board.turn
    after = board.copy(stack=False)
    after.push(move)
    mover = after.piece_at(move.to_square)
    if mover is None:
        return []
    from .motifs import VALUES
    out = []
    for sq in after.attacks(move.to_square):
        p = after.piece_at(sq)
        if not p or p.color == me or p.piece_type == chess.PAWN:
            continue
        if p.piece_type == chess.KING:
            continue                      # "giving check" is already said
        if VALUES[p.piece_type] > VALUES[mover.piece_type] or not after.attackers(p.color, sq):
            if sq in board.attacks(move.from_square) and board.piece_at(move.from_square) == mover:
                continue                  # it attacked that before the move too
            out.append(f"{side_name(p.color)}'s {PIECE_NAMES[p.piece_type]} on {chess.square_name(sq)}")
    return out


def _join(items: list) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _mates(board: chess.Board, move: chess.Move) -> bool:
    b = board.copy(stack=False)
    b.push(move)
    return b.is_checkmate()


def _infinitive(words: str, opp: str, me: bool) -> str:
    """'Black captures White's bishop on c4 with ...' -> 'capture your
    bishop on c4 with ...' (describe_move's sentence, as a threat)."""
    w = words[len(opp) + 1:] if words.startswith(opp + " ") else words
    for verb in ("captures", "moves", "castles"):
        if w.startswith(verb + " "):
            w = verb[:-1] + w[len(verb):]
            break
    return w.replace(f"{side_name(me)}'s ", "your ")


def _gain(board: chess.Board, line: list) -> int:
    """Material won along the line (pawn units, for the side to move)."""
    b = board.copy(stack=False)
    start = _balance(b, board.turn)
    for mv in line[:LOOK_PLIES]:
        if mv not in b.legal_moves:
            break
        b.push(mv)
    return _balance(b, board.turn) - start


def hint(board: chess.Board, engine, best: Optional[chess.Move] = None,
         depth: Optional[int] = None) -> dict:
    """All four steps for the side to move. `best` forces the move to hint
    at (Practice uses the review's best move, the one it will judge by).
    -> {fen, best_uci, best_san, steps: [{text, shapes}]}"""
    if board.is_game_over():
        raise ValueError("The game is over: there is no move to hint at.")
    a = run_stockfish(board, depth=depth, multipv=2, engine=engine)
    line = []
    b = board.copy(stack=False)
    for san in a.best_line:
        try:
            mv = b.parse_san(san)
        except ValueError:
            break
        line.append(mv)
        b.push(mv)
    mate = a.score_mate
    if best is not None and (not line or line[0] != best):
        after = board.copy(stack=False)
        after.push(best)
        rest = []
        if not after.is_game_over():
            r = run_stockfish(after, depth=depth, multipv=1, engine=engine)
            for san in r.best_line:
                try:
                    mv = after.parse_san(san)
                except ValueError:
                    break
                rest.append(mv)
                after.push(mv)
            mate = r.score_mate
        line = [best] + rest
    if not line:
        raise ValueError("Stockfish found no move here.")
    move = line[0]
    me = board.turn

    threat = None
    if config.settings.concepts and not board.is_check():
        from .concepts import threat as find_threat
        try:
            threat = find_threat(engine, board)
        except Exception:
            threat = None
    text, shapes = _situation(board, move, line, mate, threat)
    # An only move: everything else loses a lot.
    mates = mate is not None and (mate > 0) == (me == chess.WHITE)
    if a.alt_scores and best is None and not mates:
        cp2, m2 = a.alt_scores[0]
        if _win(a.score_cp, a.score_mate, me) - _win(cp2, m2, me) >= ONLY_MOVE:
            text += " Careful: only one move keeps your position."

    piece = board.piece_at(move.from_square)
    pname = PIECE_NAMES[piece.piece_type] if piece else "piece"
    to = chess.square_name(move.to_square)
    if board.is_castling(move):
        where = "The move is about your king: it goes to " + to + "."
    elif board.is_capture(move):
        where = f"The move takes on {to}."
    else:
        where = f"The move goes to {to}."
    which = f"Your {pname} on {chess.square_name(move.from_square)} goes there."
    if board.is_castling(move):
        which = "Your king goes there, with the rook beside it: castling."

    san = board.san(move)
    what = describe_move(board, move)
    full = f"{san}. {what}."
    attacked = _attacks(board, move)
    if attacked and not _mates(board, move):
        full += f" It attacks {_join(attacked)}."
    tags = [t for t in line_motifs(board, line, mate, plies=LOOK_PLIES)
            if not _mates(board, move)]
    if tags:
        full += f" The idea: {motif_words(tags[:2])}."
    if len(line) > 1:
        full += f" The engine's line: {board.variation_san(line[:LOOK_PLIES])}."
    steps = [
        {"text": text, "shapes": shapes},
        {"text": where, "shapes": [_circle(move.to_square)]},
        {"text": which, "shapes": [_circle(move.from_square), _circle(move.to_square)]},
        {"text": full, "shapes": [_arrow(move)]},
    ]
    return {"fen": board.fen(), "best_uci": move.uci(), "best_san": san, "steps": steps}


def alert(board: chess.Board, move: chess.Move, engine, threshold: float) -> Optional[dict]:
    """The blunder alert: None when the move is fine (or the alert is off),
    else {san, label, drop, text}. Two short searches (concept depth)."""
    if threshold <= 0 or move not in board.legal_moves:
        return None
    if board.legal_moves.count() == 1:
        return None
    from .concepts import search
    me = board.turn
    after = board.copy(stack=False)
    after.push(move)
    if after.is_checkmate():
        return None
    now = search(engine, board)
    w0 = _win(now.cp, now.mate, me)
    if after.is_game_over(claim_draw=True):
        w1 = 50.0
    else:
        s = search(engine, after)
        w1 = _win(s.cp, s.mate, me)
    drop = w0 - w1
    if drop < threshold:
        return None
    san = board.san(move)
    label = label_for_drop(drop)
    word = {"Blunder": "a blunder", "Mistake": "a mistake"}.get(label, "an inaccuracy")
    opp = side_name(not me)
    return {"san": san, "uci": move.uci(), "label": label, "drop": round(drop, 1),
            "text": f"Are you sure about {san}? It looks like {word}. "
                    f"Check what {opp} can do after it."}


_HINT = re.compile(r"\[%hint\s+(\d)\]")
_ALERT = re.compile(r"\[%alert\s+([^\]\s]+)\s*\]")


def help_taken(game) -> list:
    """The help the player took in a game played here, from its PGN:
    [{ply, san, hint (steps seen, 0-4), alerts: [SAN questioned],
      changed: True when the alert's move was not played}]"""
    out = []
    for i, node in enumerate(game.mainline()):
        c = node.comment or ""
        h, alerts = _HINT.search(c), _ALERT.findall(c)
        if not (h or alerts):
            continue
        san = node.parent.board().san(node.move)
        out.append({"ply": i, "san": san, "hint": int(h.group(1)) if h else 0,
                    "alerts": alerts, "changed": bool(alerts) and san not in alerts})
    return out
