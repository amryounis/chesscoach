"""Stockfish (objective analysis) and Maia (human move prediction)."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional

import chess
import chess.engine

from . import config


@dataclass
class EngineAnalysis:
    """Raw output from Stockfish."""
    score_cp: Optional[int] = None      # centipawns from white's view
    score_mate: Optional[int] = None    # mate in N (positive = white mates)
    best_move: str = ""
    best_line: list = field(default_factory=list)
    alt_lines: list = field(default_factory=list)  # list of (score, [moves])
    alt_scores: list = field(default_factory=list)  # list of (cp, mate), same order
    depth: int = 0
    wdl: Optional[tuple] = None         # (win, draw, loss) per mille, White's view (UCI_ShowWDL)


def white_mate(score) -> int:
    """Mate distance from White's view (+ White mates) for a mate score.

    Stockfish reports a position that is already checkmate as "mate 0",
    and python-chess's .mate() returns a bare 0 for both sides, so the
    winner was lost: win_pct gave White 0% even when White had mated. A
    delivered mate is +1 / -1 for the winner here, the convention the
    review already uses for it ("mate in 1" for the move that mates)."""
    m = score.mate()
    if m == 0:
        return 1 if score == chess.engine.MateGiven else -1
    return m


def run_stockfish(board: chess.Board, depth: Optional[int] = None,
                  multipv: Optional[int] = None, engine=None) -> EngineAnalysis:
    """Run Stockfish and return structured analysis."""
    result = EngineAnalysis()

    depth = depth or config.settings.depth
    multipv = multipv or config.settings.multipv
    own = engine is None
    if own:
        config.settings.require_stockfish()

    if own:
        engine = chess.engine.SimpleEngine.popen_uci(config.settings.stockfish_path)
    try:
        # Win/draw/loss from Stockfish's own model, where the engine has it
        if "UCI_ShowWDL" in engine.options and not getattr(engine, "_cc_wdl", False):
            engine.configure({"UCI_ShowWDL": True})
            engine._cc_wdl = True
        # Depth, but never more than max_seconds on one position: a few
        # endgames explode at high depth, and one position must not stall
        # a whole review (or make Stop wait for minutes).
        cap = config.settings.max_seconds
        info = engine.analyse(board, chess.engine.Limit(depth=depth, time=cap if cap and cap > 0 else None),
                              multipv=multipv)

        for i, line_info in enumerate(info):
            score = line_info["score"].white()
            pv = line_info.get("pv", [])

            if i == 0:
                result.depth = line_info.get("depth", depth)
                if score.is_mate():
                    result.score_mate = white_mate(score)
                else:
                    result.score_cp = score.score()
                result.best_move = board.san(pv[0]) if pv else ""
                w = line_info.get("wdl")
                if w is not None:
                    ww = w.white()
                    result.wdl = (ww.wins, ww.draws, ww.losses)
                # push moves to get SAN for the full line
                temp = board.copy()
                san_line = []
                for m in pv[:8]:
                    san_line.append(temp.san(m))
                    temp.push(m)
                result.best_line = san_line
            else:
                sc = white_mate(score) if score.is_mate() else score.score()
                temp = board.copy()
                san_line = []
                for m in pv[:6]:
                    san_line.append(temp.san(m))
                    temp.push(m)
                result.alt_lines.append((sc, san_line))
                result.alt_scores.append(
                    (None, white_mate(score)) if score.is_mate() else (score.score(), None))
    finally:
        if own:
            engine.quit()

    return result


@dataclass
class MaiaPrediction:
    """What Maia 3 thinks a human would play."""
    predicted_move: str = ""       # SAN notation
    is_best_move: bool = False     # does it match Stockfish's pick?
    error_msg: str = ""            # if Maia couldn't run


def open_maia(elo: Optional[int] = None, temperature: float = 0.0):
    """
    Start a Maia 3 process, or return None if Maia isn't installed.

    temperature 0 = always its single most likely move (use for predicting
    what a player at `elo` would play); around 1.0 = samples moves the way
    real players vary (use for playing against it).
    """
    if not shutil.which(config.settings.maia_cmd):
        return None
    try:
        eng = chess.engine.SimpleEngine.popen_uci(
            config.settings.maia_cmd, stderr=subprocess.DEVNULL)
    except Exception:
        return None
    opts = {"Elo": elo or config.settings.player_elo,
            "Temperature": str(temperature)}
    eng.configure({k: v for k, v in opts.items() if k in eng.options})
    return eng


def run_maia(board: chess.Board, engine=None) -> MaiaPrediction:
    """Ask Maia 3 what a human would play. Pass an open engine to reuse it."""
    result = MaiaPrediction()
    own = engine is None
    if own:
        engine = open_maia()
        if engine is None:
            result.error_msg = (f"Maia 3 not found (command: "
                                f"{config.settings.maia_cmd}). Skipping.")
            return result
    try:
        info = engine.play(board, chess.engine.Limit(nodes=1))
        if info.move:
            result.predicted_move = board.san(info.move)
    except Exception as e:
        result.error_msg = f"Maia 3 error: {e}"
    finally:
        if own:
            engine.quit()
    return result
