"""chesscoach.log, beside the config file: errors and review timings.

The windowed app (chesscoach-app) has no console, so this is where a
problem on the user's machine can be read afterwards."""

from __future__ import annotations

import datetime

from . import config

MAX_BYTES = 1_000_000


def append(where: str, text: str) -> None:
    try:
        path = config.user_config_path().parent / "chesscoach.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_BYTES:
            path.write_text("", encoding="utf-8")          # keep it small
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"--- {datetime.datetime.now().isoformat(timespec='seconds')} {where}\n"
                    f"{text.rstrip()}\n")
    except OSError:
        pass
