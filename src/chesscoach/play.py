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
from typing import Optional

import chess
import chess.engine
import chess.pgn

from . import config
from .engine import open_maia

# Maia samples from its move distribution at this temperature. 1.0 is the
# distribution as learned from real games; lower plays more predictably.
PLAY_TEMPERATURE = 1.0


class PlayGame:
    def __init__(self, player_color: chess.Color = chess.WHITE, elo: int = 1500,
                 temperature: float = PLAY_TEMPERATURE):
        self.player_color = player_color
        self.elo = elo
        self.board = chess.Board()
        self.result_reason = ""
        self.resigned: Optional[chess.Color] = None
        self.engine_name = ""
        self._engine = open_maia(elo, temperature)
        self._limit = chess.engine.Limit(nodes=1)
        if self._engine is not None:
            self.engine_name = f"Maia {elo}"
        else:
            config.settings.require_stockfish()
            self._engine = chess.engine.SimpleEngine.popen_uci(config.settings.stockfish_path)
            sf_elo = max(1320, min(3190, elo))
            self._engine.configure({"UCI_LimitStrength": True, "UCI_Elo": sf_elo})
            self._limit = chess.engine.Limit(time=0.2)
            self.engine_name = f"Stockfish {sf_elo}"

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

    def player_move(self, uci: str) -> str:
        """Play the player's move (UCI, e.g. 'e2e4' or 'e7e8q'); returns SAN."""
        if self.over:
            raise ValueError("The game is over.")
        if self.board.turn != self.player_color:
            raise ValueError("It is not your move.")
        move = chess.Move.from_uci(uci)
        if move not in self.board.legal_moves:
            raise ValueError(f"Illegal move: {uci}")
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
        """Undo the last move pair so it is the player's turn again."""
        if self.resigned is not None:
            return
        if self.board.move_stack and self.board.turn == self.player_color:
            self.board.pop()          # opponent's reply
        if self.board.move_stack and self.board.turn != self.player_color:
            self.board.pop()          # player's move
        # if the opponent moved first and that's all there is, keep it

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
