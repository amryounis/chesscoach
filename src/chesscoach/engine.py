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
        info = engine.analyse(board, chess.engine.Limit(depth=depth),
                              multipv=multipv)

        for i, line_info in enumerate(info):
            score = line_info["score"].white()
            pv = line_info.get("pv", [])

            if i == 0:
                result.depth = line_info.get("depth", depth)
                if score.is_mate():
                    result.score_mate = score.mate()
                else:
                    result.score_cp = score.score()
                result.best_move = board.san(pv[0]) if pv else ""
                # push moves to get SAN for the full line
                temp = board.copy()
                san_line = []
                for m in pv[:8]:
                    san_line.append(temp.san(m))
                    temp.push(m)
                result.best_line = san_line
            else:
                sc = score.mate() if score.is_mate() else score.score()
                temp = board.copy()
                san_line = []
                for m in pv[:6]:
                    san_line.append(temp.san(m))
                    temp.push(m)
                result.alt_lines.append((sc, san_line))
                result.alt_scores.append(
                    (None, score.mate()) if score.is_mate() else (score.score(), None))
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
