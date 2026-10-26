"""Positional features, and comparison of where two moves lead."""

from __future__ import annotations

from dataclasses import dataclass, field

import chess

from .describe import side_name
from .facts import material_summary
from .engine import EngineAnalysis


@dataclass
class PositionalFeatures:
    """Extracted positional characteristics."""
    material_balance: int = 0           # centipawns (+ = white advantage)
    material_text: str = ""             # "White is up a bishop and a knight..."
    side_to_move: str = "white"

    # Pawn structure
    white_pawn_islands: int = 0
    black_pawn_islands: int = 0
    white_doubled_pawns: list = field(default_factory=list)
    black_doubled_pawns: list = field(default_factory=list)
    white_isolated_pawns: list = field(default_factory=list)
    black_isolated_pawns: list = field(default_factory=list)
    white_passed_pawns: list = field(default_factory=list)
    black_passed_pawns: list = field(default_factory=list)

    # Piece activity
    white_piece_mobility: int = 0
    black_piece_mobility: int = 0

    # King safety
    white_king_square: str = ""
    black_king_square: str = ""
    white_king_pawn_shield: int = 0     # pawns near king
    black_king_pawn_shield: int = 0
    white_king_open_files: list = field(default_factory=list)
    black_king_open_files: list = field(default_factory=list)

    # Center & space
    white_center_pawns: list = field(default_factory=list)  # pawns on d4,e4,d5,e5
    black_center_pawns: list = field(default_factory=list)
    white_space: int = 0               # squares controlled beyond rank 4
    black_space: int = 0

    # Piece placement
    knights_on_outposts: list = field(default_factory=list)
    bishops_pair: dict = field(default_factory=dict)  # {white: bool, black: bool}
    rooks_on_open_files: list = field(default_factory=list)
    rooks_on_seventh: list = field(default_factory=list)

    # Tactical elements
    is_check: bool = False
    hanging_pieces: list = field(default_factory=list)

    def summary(self, exchange_in_progress: bool = False) -> str:
        """
        Human-readable summary for the LLM prompt.

        exchange_in_progress: the last move was a capture and the opponent can
        recapture on that square. Material and pawn structure are then
        snapshots of a half-finished trade (e.g. 'doubled d-pawns' that vanish
        next move), so they are withheld instead of reported as weaknesses.
        """
        lines = []
        lines.append(f"Side to move: {self.side_to_move}")

        if exchange_in_progress:
            lines.append("Material and pawn structure: NOT ASSESSED — an "
                         "exchange is in progress (a recapture is available), "
                         "so both will change on the next move. Do not "
                         "comment on material count or pawn weaknesses.")
            return self._summary_rest(lines)

        lines.append(self.material_text or
                     f"Material balance: {self.material_balance:+d} cp")

        # Pawn structure
        pawn_notes = []
        if self.white_isolated_pawns:
            pawn_notes.append(f"White isolated pawns: {', '.join(self.white_isolated_pawns)}")
        if self.black_isolated_pawns:
            pawn_notes.append(f"Black isolated pawns: {', '.join(self.black_isolated_pawns)}")
        if self.white_doubled_pawns:
            pawn_notes.append(f"White doubled pawns on files: {', '.join(self.white_doubled_pawns)}")
        if self.black_doubled_pawns:
            pawn_notes.append(f"Black doubled pawns on files: {', '.join(self.black_doubled_pawns)}")
        if self.white_passed_pawns:
            pawn_notes.append(f"White passed pawns: {', '.join(self.white_passed_pawns)}")
        if self.black_passed_pawns:
            pawn_notes.append(f"Black passed pawns: {', '.join(self.black_passed_pawns)}")
        if pawn_notes:
            lines.append("Pawn structure: " + "; ".join(pawn_notes))
        else:
            lines.append(f"Pawn structure: {self.white_pawn_islands} white islands, {self.black_pawn_islands} black islands; no isolated, doubled or passed pawns")
        return self._summary_rest(lines)

    def _summary_rest(self, lines: list) -> str:
        """Features that stay meaningful even mid-exchange."""
        # Center
        if self.white_center_pawns or self.black_center_pawns:
            lines.append(f"Center pawns — White: {', '.join(self.white_center_pawns) or 'none'}, Black: {', '.join(self.black_center_pawns) or 'none'}")

        # Space
        lines.append(f"Space: White controls {self.white_space} squares in Black's half, Black controls {self.black_space} squares in White's half")

        # Piece activity
        lines.append(f"Mobility: White {self.white_piece_mobility} legal moves, Black {self.black_piece_mobility} legal moves")

        # Knights on outposts
        lines.append("Knight outposts: " + (", ".join(self.knights_on_outposts)
                                            if self.knights_on_outposts else "none"))

        # Bishop pair
        wp = self.bishops_pair.get("white", False)
        bp = self.bishops_pair.get("black", False)
        if wp and not bp:
            lines.append("Bishop pair: White has it (Black does not)")
        elif bp and not wp:
            lines.append("Bishop pair: Black has it (White does not)")
        else:
            lines.append("Bishop pair: " + ("both sides have it" if wp else
                                            "neither side has it"))

        # Rooks
        lines.append("Rooks on open files: " + (", ".join(self.rooks_on_open_files)
                                                or "none"))
        lines.append("Rooks on the 7th rank: " + (", ".join(self.rooks_on_seventh)
                                                  or "none"))

        # King safety
        lines.append(f"King safety — White king: {self.white_king_square} ({self.white_king_pawn_shield} shield pawns)"
                      f", Black king: {self.black_king_square} ({self.black_king_pawn_shield} shield pawns)")
        lines.append("  Files with no White pawn next to the White king: "
                     + (", ".join(self.white_king_open_files) or "none"))
        lines.append("  Files with no Black pawn next to the Black king: "
                     + (", ".join(self.black_king_open_files) or "none"))

        if self.is_check:
            lines.append("KING IS IN CHECK")

        return "\n".join(lines)


def extract_features(board: chess.Board) -> PositionalFeatures:
    """Extract positional features from a board position."""
    f = PositionalFeatures()
    f.side_to_move = "white" if board.turn == chess.WHITE else "black"
    f.is_check = board.is_check()

    # --- Material ---
    piece_values = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
                    chess.ROOK: 500, chess.QUEEN: 900}
    white_mat = sum(piece_values.get(p.piece_type, 0)
                    for p in board.piece_map().values() if p.color == chess.WHITE)
    black_mat = sum(piece_values.get(p.piece_type, 0)
                    for p in board.piece_map().values() if p.color == chess.BLACK)
    f.material_balance = white_mat - black_mat
    f.material_text = material_summary(board)

    # --- Pawn structure ---
    white_pawns_by_file = [0] * 8
    black_pawns_by_file = [0] * 8
    white_pawn_squares = []
    black_pawn_squares = []

    for sq in chess.SQUARES:
        piece = board.piece_at(sq)
        if piece and piece.piece_type == chess.PAWN:
            file = chess.square_file(sq)
            rank = chess.square_rank(sq)
            if piece.color == chess.WHITE:
                white_pawns_by_file[file] += 1
                white_pawn_squares.append(sq)
            else:
                black_pawns_by_file[file] += 1
                black_pawn_squares.append(sq)

    # Pawn islands
    f.white_pawn_islands = sum(1 for i in range(8) if white_pawns_by_file[i] > 0 and (i == 0 or white_pawns_by_file[i-1] == 0))
    f.black_pawn_islands = sum(1 for i in range(8) if black_pawns_by_file[i] > 0 and (i == 0 or black_pawns_by_file[i-1] == 0))

    # Doubled pawns
    for file in range(8):
        file_letter = chr(ord('a') + file)
        if white_pawns_by_file[file] > 1:
            f.white_doubled_pawns.append(file_letter)
        if black_pawns_by_file[file] > 1:
            f.black_doubled_pawns.append(file_letter)

    # Isolated pawns
    for file in range(8):
        file_letter = chr(ord('a') + file)
        adj_white = sum(white_pawns_by_file[f] for f in [file-1, file+1] if 0 <= f < 8)
        adj_black = sum(black_pawns_by_file[f] for f in [file-1, file+1] if 0 <= f < 8)
        if white_pawns_by_file[file] > 0 and adj_white == 0:
            f.white_isolated_pawns.append(file_letter)
        if black_pawns_by_file[file] > 0 and adj_black == 0:
            f.black_isolated_pawns.append(file_letter)

    # Passed pawns
    for sq in white_pawn_squares:
        file = chess.square_file(sq)
        rank = chess.square_rank(sq)
        is_passed = True
        for r in range(rank + 1, 8):
            for ff in [file - 1, file, file + 1]:
                if 0 <= ff < 8:
                    s = chess.square(ff, r)
                    p = board.piece_at(s)
                    if p and p.piece_type == chess.PAWN and p.color == chess.BLACK:
                        is_passed = False
                        break
            if not is_passed:
                break
        if is_passed:
            f.white_passed_pawns.append(chess.square_name(sq))

    for sq in black_pawn_squares:
        file = chess.square_file(sq)
        rank = chess.square_rank(sq)
        is_passed = True
        for r in range(rank - 1, -1, -1):
            for ff in [file - 1, file, file + 1]:
                if 0 <= ff < 8:
                    s = chess.square(ff, r)
                    p = board.piece_at(s)
                    if p and p.piece_type == chess.PAWN and p.color == chess.WHITE:
                        is_passed = False
                        break
            if not is_passed:
                break
        if is_passed:
            f.black_passed_pawns.append(chess.square_name(sq))

    # --- Center control ---
    center_squares = [chess.D4, chess.E4, chess.D5, chess.E5]
    for sq in center_squares:
        piece = board.piece_at(sq)
        if piece and piece.piece_type == chess.PAWN:
            name = chess.square_name(sq)
            if piece.color == chess.WHITE:
                f.white_center_pawns.append(name)
            else:
                f.black_center_pawns.append(name)

    # --- Space ---
    for sq in chess.SQUARES:
        rank = chess.square_rank(sq)
        attackers_w = board.attackers(chess.WHITE, sq)
        attackers_b = board.attackers(chess.BLACK, sq)
        if rank >= 4 and len(attackers_w) > len(attackers_b):
            f.white_space += 1
        if rank <= 3 and len(attackers_b) > len(attackers_w):
            f.black_space += 1

    # --- Mobility ---
    f.white_piece_mobility = 0
    f.black_piece_mobility = 0
    # Count legal moves for current side
    current_moves = board.legal_moves.count()
    # Switch sides to count the other
    test_board = board.copy()
    test_board.turn = not test_board.turn
    other_moves = test_board.legal_moves.count()

    if board.turn == chess.WHITE:
        f.white_piece_mobility = current_moves
        f.black_piece_mobility = other_moves
    else:
        f.black_piece_mobility = current_moves
        f.white_piece_mobility = other_moves

    # --- King safety ---
    wk = board.king(chess.WHITE)
    bk = board.king(chess.BLACK)
    f.white_king_square = chess.square_name(wk) if wk else "?"
    f.black_king_square = chess.square_name(bk) if bk else "?"

    # Pawn shield (pawns on adjacent files within 2 ranks of king)
    def pawn_shield(king_sq, color):
        if king_sq is None:
            return 0
        kf = chess.square_file(king_sq)
        kr = chess.square_rank(king_sq)
        count = 0
        direction = 1 if color == chess.WHITE else -1
        for df in [-1, 0, 1]:
            ff = kf + df
            if 0 <= ff < 8:
                for dr in [1, 2]:
                    rr = kr + direction * dr
                    if 0 <= rr < 8:
                        s = chess.square(ff, rr)
                        p = board.piece_at(s)
                        if p and p.piece_type == chess.PAWN and p.color == color:
                            count += 1
                            break
        return count

    f.white_king_pawn_shield = pawn_shield(wk, chess.WHITE)
    f.black_king_pawn_shield = pawn_shield(bk, chess.BLACK)

    # Open files near king
    def king_open_files(king_sq, color):
        if king_sq is None:
            return []
        kf = chess.square_file(king_sq)
        open_files = []
        for df in [-1, 0, 1]:
            ff = kf + df
            if 0 <= ff < 8:
                has_own_pawn = False
                for r in range(8):
                    p = board.piece_at(chess.square(ff, r))
                    if p and p.piece_type == chess.PAWN and p.color == color:
                        has_own_pawn = True
                        break
                if not has_own_pawn:
                    open_files.append(chr(ord('a') + ff))
        return open_files

    f.white_king_open_files = king_open_files(wk, chess.WHITE)
    f.black_king_open_files = king_open_files(bk, chess.BLACK)

    # --- Knight outposts ---
    for sq in chess.SQUARES:
        piece = board.piece_at(sq)
        if piece and piece.piece_type == chess.KNIGHT:
            rank = chess.square_rank(sq)
            file = chess.square_file(sq)
            color = piece.color
            # An outpost: knight supported by own pawn, can't be attacked by enemy pawns
            is_outpost = False
            if color == chess.WHITE and rank >= 3:  # at least rank 4
                # Check if supported by white pawn
                supported = False
                for df in [-1, 1]:
                    ff = file + df
                    if 0 <= ff < 8:
                        s = chess.square(ff, rank - 1)
                        p = board.piece_at(s)
                        if p and p.piece_type == chess.PAWN and p.color == chess.WHITE:
                            supported = True
                # Check if enemy pawns can attack
                can_be_challenged = False
                for df in [-1, 1]:
                    ff = file + df
                    if 0 <= ff < 8:
                        for r in range(rank + 1, 8):
                            p = board.piece_at(chess.square(ff, r))
                            if p and p.piece_type == chess.PAWN and p.color == chess.BLACK:
                                can_be_challenged = True
                                break
                if supported and not can_be_challenged:
                    is_outpost = True
            elif color == chess.BLACK and rank <= 4:  # at least rank 5 (0-indexed rank 4)
                supported = False
                for df in [-1, 1]:
                    ff = file + df
                    if 0 <= ff < 8:
                        s = chess.square(ff, rank + 1)
                        p = board.piece_at(s)
                        if p and p.piece_type == chess.PAWN and p.color == chess.BLACK:
                            supported = True
                can_be_challenged = False
                for df in [-1, 1]:
                    ff = file + df
                    if 0 <= ff < 8:
                        for r in range(rank - 1, -1, -1):
                            p = board.piece_at(chess.square(ff, r))
                            if p and p.piece_type == chess.PAWN and p.color == chess.WHITE:
                                can_be_challenged = True
                                break
                if supported and not can_be_challenged:
                    is_outpost = True
            if is_outpost:
                side = "White" if color == chess.WHITE else "Black"
                f.knights_on_outposts.append(f"{side} knight on {chess.square_name(sq)}")

    # --- Bishop pair ---
    white_bishops = len(board.pieces(chess.BISHOP, chess.WHITE))
    black_bishops = len(board.pieces(chess.BISHOP, chess.BLACK))
    f.bishops_pair = {"white": white_bishops >= 2, "black": black_bishops >= 2}

    # --- Rooks on open files & 7th rank ---
    for sq in chess.SQUARES:
        piece = board.piece_at(sq)
        if piece and piece.piece_type == chess.ROOK:
            file = chess.square_file(sq)
            rank = chess.square_rank(sq)
            color = piece.color
            side = "White" if color == chess.WHITE else "Black"
            sq_name = chess.square_name(sq)

            # Open file (no pawns)
            has_any_pawn = False
            for r in range(8):
                p = board.piece_at(chess.square(file, r))
                if p and p.piece_type == chess.PAWN:
                    has_any_pawn = True
                    break
            if not has_any_pawn:
                f.rooks_on_open_files.append(f"{side} R{sq_name}")

            # 7th rank
            if (color == chess.WHITE and rank == 6) or (color == chess.BLACK and rank == 1):
                f.rooks_on_seventh.append(f"{side} R{sq_name}")

    return f


def snapshot(board: chess.Board, me: bool) -> dict:
    """
    Comparable position facts from the point of view of side `me`.
    Values are short strings so two snapshots can be diffed and printed.
    """
    f = extract_features(board)
    vals = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
            chess.ROOK: 5, chess.QUEEN: 9}
    mat = sum((1 if p.color == me else -1) * vals.get(p.piece_type, 0)
              for p in board.piece_map().values())
    W = me == chess.WHITE
    mine, theirs = ("White", "Black") if W else ("Black", "White")
    pick = lambda w, b: (w, b) if W else (b, w)

    shield_me, shield_op = pick(f.white_king_pawn_shield, f.black_king_pawn_shield)
    files_me, files_op = pick(f.white_king_open_files, f.black_king_open_files)
    passed_me, passed_op = pick(f.white_passed_pawns, f.black_passed_pawns)
    iso_me, iso_op = pick(f.white_isolated_pawns, f.black_isolated_pawns)
    dbl_me, dbl_op = pick(f.white_doubled_pawns, f.black_doubled_pawns)
    bp_me, bp_op = pick(f.bishops_pair.get("white"), f.bishops_pair.get("black"))
    mob_me, mob_op = pick(f.white_piece_mobility, f.black_piece_mobility)
    rooks_open = [r for r in f.rooks_on_open_files if r.startswith(mine)]
    outposts = [k for k in f.knights_on_outposts if k.startswith(mine)]
    js = lambda xs: ", ".join(xs) if xs else "none"
    # Each entry: (text shown, number, direction). direction +1 means a
    # higher number is better for `me`, -1 means lower is better. Code, not
    # the model, decides which move a difference favours.
    return {
        "material": ((f"{mine} is {abs(mat)} pawn(s) "
                      f"{'up' if mat > 0 else 'down'}" if mat else "level"), mat, +1),
        f"{mine}'s king shelter": (f"{shield_me} shield pawns", shield_me, +1),
        f"{theirs}'s king shelter": (f"{shield_op} shield pawns", shield_op, -1),
        f"files with no {mine} pawn next to {mine}'s king":
            (js(files_me), len(files_me), -1),
        f"files with no {theirs} pawn next to {theirs}'s king":
            (js(files_op), len(files_op), +1),
        f"{mine}'s passed pawns": (js(passed_me), len(passed_me), +1),
        f"{theirs}'s passed pawns": (js(passed_op), len(passed_op), -1),
        f"{mine}'s isolated pawns": (js(iso_me), len(iso_me), -1),
        f"{theirs}'s isolated pawns": (js(iso_op), len(iso_op), +1),
        f"{mine}'s doubled pawns": (js(dbl_me), len(dbl_me), -1),
        f"{theirs}'s doubled pawns": (js(dbl_op), len(dbl_op), +1),
        f"{mine} has the bishop pair": ("yes" if bp_me else "no", int(bool(bp_me)), +1),
        f"{theirs} has the bishop pair": ("yes" if bp_op else "no", int(bool(bp_op)), -1),
        f"{mine}'s rooks on open files": (js(rooks_open), len(rooks_open), +1),
        f"{mine}'s knights on outposts": (js(outposts), len(outposts), +1),
        "_mobility": (mob_me, mob_op),
    }


@dataclass
class Difference:
    """One measurable difference between where the played move leads and
    where the engine's choice leads, after the same number of moves."""
    key: str                 # "Black's king shelter", "material", ...
    played: str              # value after the player's move, as text
    engine: str              # value after the engine's choice
    favours: str             # "engine" | "player" | "other"
    caused: bool = True      # False: already so before the move

    def line(self) -> str:
        out = (f"  - {self.key}: after the player's move → {self.played}; "
               f"after the engine's choice → {self.engine}")
        if not self.caused:
            # e.g. Black's doubled c-pawns before 12...Kxf7: the move did not
            # create them, the engine's line gets rid of them. Without this
            # the model read the line as 'the move created doubled pawns'.
            out += " (already so before the move: the player's move did not cause it)"
        return out


def line_diff(board_before: chess.Board, played: chess.Move,
              eng_before: EngineAnalysis, eng_after: EngineAnalysis,
              plies: int = 4) -> list:
    """
    Where the played move leads vs where the engine's choice leads, after
    the same number of half-moves of best play: [Difference]. Empty when
    the played move is the engine's choice.
    """
    me = board_before.turn
    best_san = eng_before.best_move
    if not best_san or best_san == board_before.san(played):
        return []

    def walk(start: chess.Board, first: chess.Move, cont: list) -> chess.Board:
        """Play `plies` half-moves, then keep going (up to 4 more) while
        the last move was a capture that can be recaptured, so material
        isn't counted in the middle of a trade."""
        b = start.copy()
        b.push(first)
        for san in cont:
            done = len(b.move_stack) - len(start.move_stack)
            last = b.peek()
            prev = b.copy()
            prev.pop()
            mid_trade = (prev.is_capture(last)
                         and bool(b.attackers(b.turn, last.to_square)))
            if done >= plies + 4 or (done >= plies and not mid_trade):
                break
            try:
                b.push_san(san)
            except ValueError:
                break
        return b

    try:
        best_mv = board_before.parse_san(best_san)
    except ValueError:
        return []
    yours = walk(board_before, played, eng_after.best_line)
    # eng_before.best_line starts with the best move itself
    engines = walk(board_before, best_mv, eng_before.best_line[1:])
    a, b = snapshot(yours, me), snapshot(engines, me)
    before = snapshot(board_before, me)

    out = []
    for key in a:
        if key == "_mobility" or a[key][0] == b[key][0]:
            continue
        (ta, na, d), (tb, nb, _) = a[key], b[key]
        fav = "player" if na * d > nb * d else "engine" if nb * d > na * d else "other"
        out.append(Difference(key, ta, tb, fav, caused=before[key][0] != ta))
    (ma, _), (mb, _) = a["_mobility"], b["_mobility"]
    if abs(ma - mb) >= 6:
        out.append(Difference(f"{side_name(me)}'s number of legal moves", str(ma), str(mb),
                              "player" if ma > mb else "engine"))
    return out


def compare_lines(board_before: chess.Board, played: chess.Move,
                  eng_before: EngineAnalysis, eng_after: EngineAnalysis,
                  plies: int = 4) -> list:
    """
    line_diff() as text for the prompt. This is the 'why' for quiet moves:
    e.g. after the engine's move the king keeps its shelter; after yours a
    file opens next to it.
    """
    diffs = line_diff(board_before, played, eng_before, eng_after, plies)
    if not diffs:
        return []
    for_engine = [d.line() for d in diffs if d.favours == "engine"]
    for_player = [d.line() for d in diffs if d.favours == "player"]
    other = [d.line() for d in diffs if d.favours == "other"]
    out = ["- In favour of the ENGINE'S CHOICE (what the player gave up):"]
    out += for_engine or ["  - nothing measurable — its advantage is dynamic"]
    out += ["- In favour of the PLAYER'S MOVE (what the player gained):"]
    out += for_player or ["  - nothing"]
    if other:
        out += ["- Other differences (neither clearly better):"] + other
    return out
