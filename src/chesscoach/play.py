"""
Play a game against a human-like opponent.

Maia 3 at a chosen Elo plays like a real player of that strength (it samples
moves, so it varies and makes human mistakes). Without Maia, Stockfish's own
strength limit is used instead; it can't go below about 1320 Elo and plays
less like a human.
"""

from __future__ import annotations

import datetime
import random
import shutil
from typing import Optional

import chess
import chess.engine
import chess.pgn

from . import config
from .engine import open_maia

# Maia samples from its move distribution at this temperature. 1.0 is the
# distribution as learned from real games; lower plays more predictably.
PLAY_TEMPERATURE = 1.0

# Stockfish's UCI_Elo range: below 1320 it simply won't go.
STOCKFISH_MIN_ELO, STOCKFISH_MAX_ELO = 1320, 3190


def describe_opponent(engine: str, elo: int, why: str = "") -> dict:
    """The opponent the player actually faces, said plainly.

    engine: "maia" or "stockfish"; elo: the rating the player asked for;
    why (Stockfish only): "missing" (Maia isn't installed) or "failed"
    (it is installed but didn't start).
    -> {engine, name, requested_elo, effective_elo, clamped, human_like, note}
    """
    if engine == "maia":
        return {"engine": "maia", "name": f"Maia {elo}", "requested_elo": elo,
                "effective_elo": elo, "clamped": False, "human_like": True,
                "note": f"Maia plays like a human rated about {elo}, mistakes included."}
    sf_elo = max(STOCKFISH_MIN_ELO, min(STOCKFISH_MAX_ELO, elo))
    cause = ("Maia didn't start" if why == "failed"
             else f"Maia isn't installed (command: {config.settings.maia_cmd})")
    if sf_elo > elo:
        strength = (f"Stockfish at {sf_elo}, its lowest setting, which is stronger "
                    f"than the {elo} you chose")
    elif sf_elo < elo:
        strength = f"Stockfish at {sf_elo}, its highest setting"
    else:
        strength = f"Stockfish limited to {sf_elo}"
    return {"engine": "stockfish", "name": f"Stockfish {sf_elo}", "requested_elo": elo,
            "effective_elo": sf_elo, "clamped": sf_elo != elo, "human_like": False,
            "note": (f"{cause}, so your opponent is {strength}. It plays like an engine "
                     "that blunders on purpose, not like a human of that rating.")}


def preview_opponent(elo: int) -> dict:
    """Who Play would use at this rating, without starting an engine. If
    Maia is installed but then fails to start, PlayGame.opponent says so."""
    return describe_opponent("maia" if shutil.which(config.settings.maia_cmd)
                             else "stockfish", int(elo))


class PlayGame:
    def __init__(self, player_color: chess.Color = chess.WHITE, elo: int = 1500,
                 temperature: float = PLAY_TEMPERATURE):
        self.player_color = player_color
        self.elo = elo
        self.board = chess.Board()
        self.result_reason = ""
        self.resigned: Optional[chess.Color] = None
        self.engine_name = ""
        # Help the player took, by ply (the number of moves before the
        # player's move): hint steps seen, and moves the blunder alert
        # questioned. Saved in the PGN as [%hint n] and [%alert SAN].
        self.hints: dict = {}
        self.alerts: dict = {}
        self._engine = open_maia(elo, temperature)
        self._limit = chess.engine.Limit(nodes=1)
        if self._engine is not None:
            self.opponent = describe_opponent("maia", elo)
        else:
            why = "failed" if shutil.which(config.settings.maia_cmd) else "missing"
            self.opponent = describe_opponent("stockfish", elo, why)
            config.settings.require_stockfish()
            self._engine = chess.engine.SimpleEngine.popen_uci(config.settings.stockfish_path)
            self._engine.configure({"UCI_LimitStrength": True,
                                    "UCI_Elo": self.opponent["effective_elo"]})
            self._limit = chess.engine.Limit(time=0.2)
        self.engine_name = self.opponent["name"]

    # -- state -------------------------------------------------------------

    @property
    def over(self) -> bool:
        return self.resigned is not None or self.board.is_game_over(claim_draw=True)

    def result(self) -> str:
        if self.resigned is not None:
            return "0-1" if self.resigned == chess.WHITE else "1-0"
        if self.board.is_game_over(claim_draw=True):
            return self.board.result(claim_draw=True)
        return "*"

    def status(self) -> str:
        """Human-readable state of the game."""
        if self.resigned is not None:
            who = "You" if self.resigned == self.player_color else self.engine_name
            return f"{who} resigned."
        outcome = self.board.outcome(claim_draw=True)
        if outcome:
            reason = outcome.termination.name.replace("_", " ").lower()
            if outcome.winner is None:
                return f"Draw ({reason})."
            won = outcome.winner == self.player_color
            return f"{'You win' if won else self.engine_name + ' wins'} ({reason})."
        side = "Your" if self.board.turn == self.player_color else f"{self.engine_name}'s"
        check = " — check!" if self.board.is_check() else ""
        return f"{side} move{check}"

    def legal_dests(self) -> dict:
        """{from_square: [to_squares]} for the side to move, e.g. {'e2': ['e3','e4']}."""
        dests: dict = {}
        for m in self.board.legal_moves:
            dests.setdefault(chess.square_name(m.from_square), []).append(
                chess.square_name(m.to_square))
        return dests

    def needs_promotion(self, from_sq: str, to_sq: str) -> bool:
        piece = self.board.piece_at(chess.parse_square(from_sq))
        rank = chess.square_rank(chess.parse_square(to_sq))
        return bool(piece and piece.piece_type == chess.PAWN and rank in (0, 7))

    # -- moves -------------------------------------------------------------

    def check_move(self, uci: str) -> chess.Move:
        """The player's move, if it may be played now (else ValueError)."""
        if self.over:
            raise ValueError("The game is over.")
        if self.board.turn != self.player_color:
            raise ValueError("It is not your move.")
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            raise ValueError(f"Illegal move: {uci}") from None
        if move not in self.board.legal_moves:
            raise ValueError(f"Illegal move: {uci}")
        return move

    def player_move(self, uci: str) -> str:
        """Play the player's move (UCI, e.g. 'e2e4' or 'e7e8q'); returns SAN."""
        move = self.check_move(uci)
        san = self.board.san(move)
        self.board.push(move)
        return san

    def engine_move(self) -> Optional[str]:
        """Let the opponent move if it's its turn; returns SAN or None."""
        if self.over or self.board.turn == self.player_color:
            return None
        move = self._engine.play(self.board, self._limit).move
        san = self.board.san(move)
        self.board.push(move)
        return san

    def takeback(self) -> None:
        """Undo the player's last move (and the reply to it), so it is the
        player's turn again. Never undoes the engine's opening move when the
        player is Black: there is no move of the player's before it, and
        undoing it left White to move with the player unable to move."""
        if self.resigned is not None:
            return
        # On the player's turn: the reply and the player's move. Otherwise
        # (the game ended on the player's move): just that move.
        n = 2 if self.board.turn == self.player_color else 1
        first_own = 0 if self.player_color == chess.WHITE else 1   # plies before the player's first move
        if len(self.board.move_stack) - n < first_own:
            return
        for _ in range(n):
            self.board.pop()

    def resign(self) -> None:
        if not self.over:
            self.resigned = self.player_color

    # -- export ------------------------------------------------------------

    def to_pgn_game(self) -> chess.pgn.Game:
        game = chess.pgn.Game.from_board(self.board)
        name = config.settings.player_name
        game.headers["Event"] = "chesscoach training game"
        game.headers["Site"] = "local"
        game.headers["Date"] = datetime.date.today().strftime("%Y.%m.%d")
        game.headers["White"] = name if self.player_color == chess.WHITE else self.engine_name
        game.headers["Black"] = self.engine_name if self.player_color == chess.WHITE else name
        game.headers["Result"] = self.result()
        node = game
        for ply, _ in enumerate(self.board.move_stack):
            node = node.variations[0]
            notes = []
            if self.hints.get(ply):
                notes.append(f"[%hint {self.hints[ply]}]")
            for san in self.alerts.get(ply, []):
                notes.append(f"[%alert {san}]")
            if notes:
                node.comment = " ".join(notes)
        return game

    def pgn(self) -> str:
        return str(self.to_pgn_game())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self) -> None:
        if self._engine is not None:
            try:
                self._engine.quit()
            except Exception:
                pass
            self._engine = None


def random_color() -> chess.Color:
    return random.choice([chess.WHITE, chess.BLACK])
