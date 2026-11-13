"""
The first-run setup: what this computer has, and what to use.

  engine   Stockfish (the installer brings it; else the path in Settings)
  coach    Ollama on this computer, with a model that suits the graphics
           card, or an online model with a key (Gemini has a free one)
  you      your Lichess / Chess.com names and rating (optional)

Which model suits the computer, from the graphics card's memory (NVIDIA:
`nvidia-smi`; others aren't measured, and the online model is suggested):

  12 GB or more   qwen3:14b   measured on chesscoach's answer keys
  8 GB or more    qwen3:8b    not measured: expect more mistakes
  less, or none   the online model (a 14B model on the processor alone
                  takes minutes per comment)

Nothing here downloads anything until the setup's Download button is
pressed; then Ollama itself downloads the model (`ollama pull`).
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
from typing import Optional

MEASURED = "qwen3:14b"
SMALLER = "qwen3:8b"
SIZES = {"qwen3:14b": "about 9 GB", "qwen3:8b": "about 5 GB"}


def _run(cmd: list, timeout: float = 5.0) -> str:
    """A command's output, without a console window flashing up on Windows."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=flags)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def gpu(run=_run) -> Optional[dict]:
    """The NVIDIA graphics card with the most memory: {name, vram_gb}, or None."""
    out = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    best = None
    for line in out.splitlines():
        name, _, mem = line.rpartition(",")
        try:
            gb = round(int(mem.strip()) / 1024, 1)
        except ValueError:
            continue
        if best is None or gb > best["vram_gb"]:
            best = {"name": name.strip(), "vram_gb": gb}
    return best


def ram_gb() -> Optional[float]:
    try:
        if os.name == "nt":
            import ctypes

            class MEM(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MEM()
            m.dwLength = ctypes.sizeof(MEM)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return round(m.ullTotalPhys / 2 ** 30, 1)
        with open("/proc/meminfo", encoding="utf-8") as f:
            kb = int(re.search(r"MemTotal:\s+(\d+)", f.read()).group(1))
        return round(kb / 2 ** 20, 1)
    except Exception:
        return None


def suggest(card: Optional[dict], ram: Optional[float]) -> dict:
    """What to use on this computer: {coach: ollama|online, model, text}."""
    if card and card["vram_gb"] >= 11.5:               # a 12 GB card reports a little under 12
        return {"coach": "ollama", "model": MEASURED,
                "text": f"Your {card['name']} ({card['vram_gb']:g} GB) runs {MEASURED}, the "
                        "model chesscoach is measured with."}
    if card and card["vram_gb"] >= 7.5:
        return {"coach": "ollama", "model": SMALLER,
                "text": f"Your {card['name']} ({card['vram_gb']:g} GB) runs {SMALLER}. It is "
                        f"smaller than {MEASURED} and hasn't been measured on chesscoach's "
                        "answer keys: expect more mistakes in the comments. An online model "
                        "is the other choice."}
    where = f"Your {card['name']} has {card['vram_gb']:g} GB" if card else \
        "No NVIDIA graphics card was found"
    return {"coach": "online", "model": "",
            "text": f"{where}: a model on this computer would be slow (minutes per comment) "
                    "or weak. An online model with your own key is the better choice here; "
                    "Google Gemini has a free one."}


def ollama_status(model: str, client=None) -> dict:
    """{running, models, has}: whether Ollama answers, what it has, and
    whether `model` is among them."""
    try:
        if client is None:
            import ollama
            client = ollama.Client(timeout=4)
        listed = client.list()
    except Exception:
        return {"running": False, "models": [], "has": False}
    raw = listed.get("models", []) if isinstance(listed, dict) else getattr(listed, "models", [])
    names = []
    for m in raw or []:
        n = (m.get("model") or m.get("name")) if isinstance(m, dict) else \
            (getattr(m, "model", None) or getattr(m, "name", None))
        if n:
            names.append(n)
    want = (model or "").strip()
    has = any(n == want or n == f"{want}:latest" or n.split(":")[0] == want for n in names)
    return {"running": True, "models": sorted(names), "has": has}


class Pull:
    """Downloading a model through Ollama, in the background, with progress."""

    def __init__(self):
        self._lock = threading.Lock()
        self.state = {"model": "", "status": "", "completed": 0, "total": 0, "done": False,
                      "error": "", "running": False}

    def start(self, model: str, client=None) -> dict:
        with self._lock:
            if self.state["running"]:
                return dict(self.state)
            self.state = {"model": model, "status": "starting", "completed": 0, "total": 0,
                          "done": False, "error": "", "running": True}
        threading.Thread(target=self._run, args=(model, client), daemon=True).start()
        return dict(self.state)

    def _run(self, model: str, client) -> None:
        try:
            if client is None:
                import ollama
                client = ollama.Client()
            for part in client.pull(model, stream=True):
                get = part.get if isinstance(part, dict) else (lambda k, d=None: getattr(part, k, d))
                with self._lock:
                    self.state["status"] = get("status", "") or self.state["status"]
                    if get("total"):
                        self.state["total"] = int(get("total"))
                        self.state["completed"] = int(get("completed") or 0)
            with self._lock:
                self.state.update(done=True, running=False, status="success")
        except Exception as e:
            with self._lock:
                self.state.update(running=False, error=f"The download stopped: {e}")

    def progress(self) -> dict:
        with self._lock:
            return dict(self.state)
