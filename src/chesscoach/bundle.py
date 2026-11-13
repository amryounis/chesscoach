"""
chesscoach as an installed Windows program (the installer: packaging/).

The installer is chesscoach and its Python frozen into one folder by
PyInstaller, with Stockfish beside it in stockfish/: two builds, one for
processors with AVX2 (most since 2013) and one for older ones, and the
faster one is chosen here. Ollama is not included (it is its own program,
and the models are gigabytes): the first-run setup checks for it and
downloads the model, or sets up an online model instead. Maia is not
included either (it needs PyTorch); the app works without it.

Nothing here does anything when chesscoach runs from Python (pip install).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

AVX2 = "stockfish-avx2.exe"
GENERIC = "stockfish.exe"


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Optional[Path]:
    """The installed program's folder, or None when run from Python."""
    return Path(sys.executable).resolve().parent if frozen() else None


def cpu_has_avx2() -> bool:
    try:
        if os.name == "nt":
            import ctypes
            return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(40))  # PF_AVX2
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            return " avx2" in f.read()
    except Exception:
        return False


def bundled_stockfish(folder: Optional[Path] = None, avx2: Optional[bool] = None) -> str:
    """The Stockfish that came with the installer: the AVX2 build when the
    processor has AVX2, else the build for any 64-bit processor. "" when
    there is none (not installed by the installer)."""
    folder = folder or (app_dir() / "stockfish" if app_dir() else None)
    if folder is None or not folder.is_dir():
        return ""
    use_avx2 = cpu_has_avx2() if avx2 is None else avx2
    for name in ([AVX2, GENERIC] if use_avx2 else [GENERIC]):
        p = folder / name
        if p.is_file():
            return str(p)
    return ""


def quiet_streams() -> None:
    """A program without a console has no stdout/stderr: print() would fail."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
