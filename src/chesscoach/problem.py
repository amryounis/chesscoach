"""
"Report a problem": what a tester sends, so a problem on their computer can
be understood without asking them for details they wouldn't know.

  the version, Windows (or the system), how the app was installed
  the coach (Ollama and its model, or the online service and model)
  Stockfish and Maia: found or not
  the end of chesscoach.log (errors and review timings) and of maia.log

Keys and tokens are never in it: the settings' secrets are left out, and
anything that looks like one in the logs is masked. The tester sees the
text before it goes anywhere, and sends it themselves: as a GitHub issue
(the page opens filled in) or copied into an email or a message.
"""

from __future__ import annotations

import platform
import re
import shutil
from pathlib import Path
from urllib.parse import urlencode

from . import __version__, bundle, config

ISSUES = "https://github.com/amryounis/chesscoach/issues/new"
LOG_LINES = 60
URL_MAX = 7000                     # browsers and GitHub cut longer links

_SECRET_RE = re.compile(
    r"(?i)((?:api[_-]?key|key|token|authorization|bearer)[\"']?\s*[:=]?\s*[\"']?)[A-Za-z0-9._\-]{12,}")


def _mask(text: str) -> str:
    s = config.settings
    for secret in (getattr(s, name, "") for name in config.SECRETS):
        if secret and len(secret) >= 6:
            text = text.replace(secret, "<hidden>")
    return _SECRET_RE.sub(r"\1<hidden>", text)


def _tail(path: Path, n: int) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "(none)"
    return "\n".join(lines[-n:]) or "(empty)"


def build(what_happened: str = "") -> dict:
    """{text, url}: the report, and the GitHub page that opens with it."""
    s = config.settings
    folder = config.user_config_path().parent
    if s.coach_llm == "online":
        coach = f"online: {s.online_service}, model {s.online_model or '(none chosen)'}"
    else:
        coach = f"Ollama, model {s.model}"
    lines = [
        f"chesscoach {__version__} ({'installer' if bundle.frozen() else 'Python'})",
        f"System: {platform.system()} {platform.release()} ({platform.version()}), "
        f"Python {platform.python_version()}",
        f"Coach: {coach}",
        f"Stockfish: {'found' if s.stockfish_path and Path(s.stockfish_path).exists() else 'not found'}"
        f"{' (the bundled one)' if s.stockfish_path and s.stockfish_path == bundle.bundled_stockfish() else ''}",
        f"Maia: {'found' if shutil.which(s.maia_cmd or '') else 'not installed'}",
        f"Review depth {s.depth}, {s.max_seconds}s per position",
    ]
    text = "\n".join([
        "What happened (please describe it):",
        what_happened.strip() or "",
        "",
        "---- chesscoach details ----",
        *lines,
        "",
        "---- the end of chesscoach.log ----",
        _tail(folder / "chesscoach.log", LOG_LINES),
        "",
        "---- the end of maia.log ----",
        _tail(folder / "maia.log", 15),
    ])
    text = _mask(text)
    title = f"Problem in chesscoach {__version__}"

    def url_for(body):
        return f"{ISSUES}?{urlencode({'title': title, 'body': body})}"

    body = text
    url = url_for(body)
    while len(url) > URL_MAX and "\n" in body:        # the log's oldest lines go first
        head, _, rest = body.partition("---- the end of chesscoach.log ----\n")
        log = rest.split("\n")
        if len(log) <= 20:
            break
        body = head + "---- the end of chesscoach.log ----\n(earlier lines left out)\n" + \
            "\n".join(log[len(log) // 4:])
        url = url_for(body)
    if len(url) > URL_MAX:
        url = url_for(text.split("---- the end of chesscoach.log ----")[0]
                      + "(the log is too long for the link: paste it from Copy)")
    return {"text": text, "url": url, "folder": str(folder)}
