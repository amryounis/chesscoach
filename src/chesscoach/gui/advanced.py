"""
The advanced analyzer: uciboard (github.com/amryounis/advanced-chess-analayzer)
in its own window, opened from chesscoach on the game or position in hand.

uciboard is its own program and package: the UCI engine output laid out like
Arena's engine window, the tactics overlays (loose pieces by exchange
evaluation, pins and skewers, removable and overloaded defenders, king
safety, safe squares), pawn structure, square control, the Evolution graph
of Lu, Wang and Lin (IEEE TVCG 2014), the ChessY positional measures, move
quality marks, an annotated PGN export, and a list of UCI engines. chesscoach
does not copy any of it: it imports the installed package and opens its page
in a second window, so what is built in uciboard arrives here with an update
of that package. Installed with chesscoach's "advanced" extra.

The window uses the Stockfish chesscoach uses (its path is passed on), and
its live analysis is off until Analyse is pressed there, as in uciboard. One
analyzer window at a time: opening it again loads the new game into it.
"""

from __future__ import annotations

import importlib.util
import threading
from typing import Callable, Optional

from .. import config

INSTALL = 'pip install -e ".[gui,advanced]"'
TITLE = "chesscoach: advanced analysis"


def available() -> bool:
    return importlib.util.find_spec("uciboard") is not None


def _not_installed() -> ValueError:
    return ValueError("The advanced analyzer (uciboard) isn't installed. In the chesscoach "
                      f"folder, run: {INSTALL}")


class Analyzer:
    """One uciboard window, created on first use and reused after."""

    def __init__(self, create_window: Optional[Callable] = None):
        self._create = create_window          # webview.create_window; replaced in tests
        self._lock = threading.Lock()
        self._api = None                      # uciboard.api.Api
        self._server = None                   # its page's local HTTP server
        self._window = None

    @property
    def is_open(self) -> bool:
        return self._window is not None

    def open(self, pgn: str = "", ply: Optional[int] = None, fen: str = "") -> dict:
        """Show the analyzer on a game (PGN, at `ply` moves; None = the end)
        or, when there are no moves, on a position (FEN).
        -> {"version", "reused", "message"}"""
        if not available():
            raise _not_installed()
        import uciboard
        from uciboard import config as uconfig
        from uciboard.api import Api
        from uciboard.app import serve_static
        with self._lock:
            reused = self._api is not None
            if not reused:
                try:
                    settings = uconfig.load_settings(stockfish=config.settings.stockfish_path or None)
                except uconfig.ConfigError as e:
                    raise ValueError(f"The analyzer can't start: {e}") from None
                api = Api(settings)
            else:
                api = self._api
            message = self._load(api, pgn, ply, fen)
            if not reused:
                server = serve_static()
                create = self._create
                if create is None:
                    import webview
                    create = webview.create_window
                port = server.server_address[1]
                window = create(f"{TITLE} (uciboard {uciboard.__version__})",
                                url=f"http://127.0.0.1:{port}/index.html", js_api=api,
                                width=1240, height=820, min_size=(900, 600))
                self._api, self._server, self._window = api, server, window
                api._window = window          # its dialogs open on its own window (uciboard 0.10.1)
                try:
                    window.events.closing += self._closed
                except AttributeError:
                    pass
            else:
                # the page reads the board when it starts: start it again on the new game
                self._call(self._window, "evaluate_js", "location.reload()")
                self._call(self._window, "restore")
                self._call(self._window, "show")
            return {"version": uciboard.__version__, "reused": reused, "message": message}

    @staticmethod
    def _load(api, pgn: str, ply: Optional[int], fen: str) -> str:
        if pgn:
            st = api.load_pgn(pgn)
            if not st.get("rejected"):
                if ply is not None:
                    api.goto_ply(ply)
                return st.get("message", "")
        if fen:
            st = api.set_position(fen)
            if st.get("rejected"):
                raise ValueError(st.get("message") or "The analyzer can't open this position.")
            return ""
        api.reset()
        return ""

    @staticmethod
    def _call(window, name: str, *args) -> None:
        try:
            getattr(window, name)(*args)
        except Exception:
            pass                               # not every pywebview backend has every call

    def _closed(self, *_args) -> None:
        with self._lock:
            api, server = self._api, self._server
            self._api = self._server = self._window = None
        if api is not None:
            api.shutdown()                     # its engines stop with the window
        if server is not None:
            server.shutdown()

    def close(self) -> None:
        """chesscoach is closing: close the analyzer too."""
        window = self._window
        if window is not None:
            self._call(window, "destroy")
        self._closed()
