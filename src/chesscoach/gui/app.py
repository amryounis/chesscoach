"""
Desktop window: a native window (pywebview) showing the page in ./static.

The page is served by a small local HTTP server on 127.0.0.1 so that ES
modules load the same way in every browser engine pywebview may use
(Edge WebView2 on Windows). Nothing is reachable from other machines.
"""

from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

from .. import __version__, config
from .api import Api

STATIC = Path(__file__).parent / "static"


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def serve_static() -> http.server.ThreadingHTTPServer:
    handler = functools.partial(_QuietHandler, directory=str(STATIC))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


WIDTH, HEIGHT = 1320, 860            # the window when not maximized, if the screen allows


def placement(screens: list, maximized: bool) -> dict:
    """Where the window opens: maximized, or centred on the main screen at a
    size that fits it. Left to Windows, each start opened lower down the
    screen than the last (it cascades new windows), until the bottom of the
    board was off screen. `screens`: pywebview's screens (width, height)."""
    w, h = WIDTH, HEIGHT
    out = {"maximized": bool(maximized)}
    if screens:
        sw, sh = screens[0].width, screens[0].height
        w, h = min(WIDTH, max(800, sw - 40)), min(HEIGHT, max(600, sh - 80))
        out.update(x=max(0, (sw - w) // 2), y=max(0, (sh - h) // 2 - 20))
    out.update(width=w, height=h)
    return out


def launch(debug: bool = False) -> None:
    try:
        import webview
    except ImportError as e:
        raise SystemExit(
            "The desktop app needs pywebview. Install it with:\n"
            '    pip install "chesscoach[gui]"') from e

    server = serve_static()
    api = Api()
    port = server.server_address[1]
    try:
        screens = list(webview.screens)
    except Exception:
        screens = []
    window = webview.create_window(
        f"chesscoach {__version__}", url=f"http://127.0.0.1:{port}/index.html", js_api=api,
        min_size=(1000, 680), **placement(screens, config.settings.start_maximized))
    api._window = window
    window.events.closing += api.shutdown
    try:
        webview.start(debug=debug)
    finally:
        api.shutdown()
        server.shutdown()


def main() -> None:
    """Entry point for the `chesscoach-app` shortcut (no console window)."""
    try:
        config.settings = config.load_settings()
    except config.ConfigError:
        config.settings = config.Settings()   # fix it from the Settings dialog
    launch()
