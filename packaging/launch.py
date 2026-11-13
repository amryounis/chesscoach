"""
The installed program's start: what PyInstaller freezes into chesscoach.exe.

CHESSCOACH_SELFTEST=<file>: instead of opening the window, check that the
frozen program has what it needs (its modules, the page, the opening book,
the bundled Stockfish answering) and write "ok" or the failure to <file>.
The installer build runs this before packing (.github/workflows/installer.yml).
"""

import os
import sys
import traceback


def selftest(out: str) -> None:
    try:
        import chess.engine
        import ollama  # noqa: F401
        import webview  # noqa: F401

        from chesscoach import __version__, bundle, opening
        from chesscoach.gui import api, app  # noqa: F401
        from chesscoach.gui.advanced import available

        assert bundle.frozen(), "not frozen"
        if os.name == "nt":
            import webview.platforms.winforms  # noqa: F401  (the window: .NET and WebView2)
        assert (app.STATIC / "index.html").is_file(), "the page is missing"
        assert opening._table() and opening._ideas(), "the opening book is missing"
        sf = bundle.bundled_stockfish()
        assert sf, "no bundled Stockfish"
        with chess.engine.SimpleEngine.popen_uci(sf) as eng:
            info = eng.analyse(chess.Board(), chess.engine.Limit(depth=8))
            assert info.get("score") is not None, "Stockfish gave no score"
        msg = f"ok chesscoach {__version__}; stockfish {os.path.basename(sf)}; " \
              f"advanced analyzer {'yes' if available() else 'no'}"
    except BaseException:
        msg = "FAILED\n" + traceback.format_exc()
    with open(out, "w", encoding="utf-8") as f:
        f.write(msg + "\n")


if __name__ == "__main__":
    if os.environ.get("CHESSCOACH_SELFTEST"):
        selftest(os.environ["CHESSCOACH_SELFTEST"])
        sys.exit(0)
    from chesscoach.gui.app import main
    main()
