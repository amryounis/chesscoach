# PyInstaller: chesscoach as one folder (dist/chesscoach/chesscoach.exe and
# _internal/), without a console window. Built by
# .github/workflows/installer.yml on Windows:  pyinstaller packaging/chesscoach.spec
# The installer (chesscoach.iss) then adds Stockfish beside it in stockfish/.
import importlib.util

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

datas = collect_data_files("chesscoach")            # the page, the opening book, answer keys
hidden = collect_submodules("chesscoach")
binaries = []
# pywebview and, on Windows, the .NET bridge it uses for Edge WebView2; the
# advanced analyzer (uciboard) and its page, when installed.
for pkg in ("webview", "clr_loader", "pythonnet", "uciboard"):
    if importlib.util.find_spec(pkg) is not None:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hidden += h

a = Analysis(
    ["launch.py"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "pytest", "playwright", "torch"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="chesscoach",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="chesscoach", upx=False)
