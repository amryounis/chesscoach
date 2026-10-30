"""The `chesscoach` command."""

from __future__ import annotations

import argparse
import sys
from dataclasses import fields

from . import __version__, config


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", help="path to a config file (TOML)")
    p.add_argument("--stockfish", dest="stockfish_path", help="Stockfish executable")
    p.add_argument("--book", dest="book_path", help="Polyglot opening book (.bin)")
    p.add_argument("--maia", dest="maia_cmd", help="Maia UCI command")
    p.add_argument("--depth", type=int, help="Stockfish depth")
    p.add_argument("-m", "--model", dest="models", action="append",
                   help="Ollama model; repeat to compare models")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="chesscoach",
        description="Offline chess coach: Stockfish analyses, Maia predicts "
                    "human moves, a local LLM narrates.")
    ap.add_argument("--version", action="version", version=f"chesscoach {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    r = sub.add_parser("review", help="annotate the critical moments of a game")
    r.add_argument("pgn", help="PGN file (first game is used)")
    r.add_argument("--side", choices=["white", "black", "both"], default="both",
                   help="whose moves to annotate (default: both)")
    r.add_argument("--every-move", action="store_true",
                   help="annotate every non-book move, not just critical moments")
    r.add_argument("--verbose", action="store_true", help="print every prompt")
    _common(r)

    d = sub.add_parser("demo", help="annotate two fixed test positions")
    _common(d)

    g = sub.add_parser("gui", help="open the desktop app")
    g.add_argument("--debug", action="store_true", help="enable the web inspector")
    _common(g)

    x = sub.add_parser("explain", help="explain a position in plain language")
    x.add_argument("--fen", default="", help="the position (default: the start)")
    x.add_argument("--moves", default="", help="or: the moves from the start, in SAN")
    _common(x)

    q = sub.add_parser("ask", help="ask the coach a question about a position")
    q.add_argument("question", help='e.g. "Why not Nd2?" or "Is my bishop safe?"')
    q.add_argument("--fen", default="", help="the position (default: the start)")
    q.add_argument("--moves", default="", help="or: the moves from the start, in SAN")
    q.add_argument("--show-tools", action="store_true", help="print each tool call")
    q.add_argument("--agent", action="store_true",
                   help="let the model choose its own tool calls (default: the router)")
    _common(q)

    e = sub.add_parser("eval", help="score the facts layer and models against answer keys")
    e.add_argument("--key", dest="key_ids", action="append",
                   help="only this key id (repeatable); default: all")
    e.add_argument("--keys", dest="key_files", action="append",
                   help="answer-key TOML file (repeatable); default: the bundled keys")
    e.add_argument("--mode", dest="modes", action="append",
                   choices=["facts", "pipeline", "raw", "explain", "ask"],
                   help="what to score (repeatable). Default: facts, plus "
                        "pipeline when a model is given")
    e.add_argument("--text", help="score this text file against --key instead of "
                                  "running a model (e.g. a pasted reply)")
    e.add_argument("--report", help="write a markdown report with every text")
    e.add_argument("--strict", action="store_true",
                   help="exit 1 on any facts miss or false claim (for CI)")
    e.add_argument("--list", action="store_true", help="list the keys and exit")
    e.add_argument("--runs", type=int, default=1,
                   help="run each model N times per key and report how often each "
                        "fact was stated (one run is a sample, not a score)")
    e.add_argument("--no-grounding", action="store_true",
                   help="run the pipeline without the grounding check, to "
                        "measure what the check catches")
    _common(e)

    pt = sub.add_parser("patterns", help="recurring themes across your reviewed games")
    pt.add_argument("--last", type=int, default=10,
                    help="how many of your most recent reviewed games (default 10)")
    pt.add_argument("--facts", action="store_true",
                    help="show the stored facts behind each pattern")
    _common(pt)

    bk = sub.add_parser("book", help="study an opening book: the idea of every popular move")
    bsub = bk.add_subparsers(dest="book_command", required=True)
    st = bsub.add_parser("study", help="walk the book once, explaining every popular move "
                                       "(resumable; run it overnight)")
    st.add_argument("bookfile", nargs="?", default="",
                    help="Polyglot .bin book (default: book_path from the settings)")
    st.add_argument("--plies", type=int, default=20, help="how deep, in half-moves (default 20)")
    st.add_argument("--min-share", type=float, default=0.05,
                    help="follow a move only if the book plays it this often here (default 0.05)")
    st.add_argument("--min-line", type=float, default=0.002,
                    help="follow a line only while it stays this common overall (default 0.002)")
    st.add_argument("--max-moves", type=int, default=4, help="at most this many moves per position")
    st.add_argument("--study-depth", type=int, default=16, help="Stockfish depth (default 16)")
    st.add_argument("--secs", type=float, default=15.0,
                    help="seconds per comment on your machine, for the estimate (default 15)")
    st.add_argument("--dry-run", action="store_true",
                    help="only say how many moves and roughly how long")
    st.add_argument("--redo", action="store_true", help="write again the ideas already written")
    _common(st)
    sh = bsub.add_parser("show", help="what the study says about a position")
    sh.add_argument("moves", nargs="?", default="", help='moves from the start, e.g. "e4 e5 Nf3"')
    _common(sh)

    c = sub.add_parser("config", help="show settings, or create a config file")
    c.add_argument("--init", action="store_true",
                   help="write a commented config file to the user config folder")
    _common(c)
    return ap


def _hm(hours: float) -> str:
    h, m = int(hours), int(round((hours - int(hours)) * 60))
    return f"{h}h{m:02d}" if h else f"{m} min"


def _book(args, model: str) -> int:
    import chess
    from . import book_study as bs
    if args.book_command == "show":
        b = chess.Board()
        for san in args.moves.split():
            b.push_san(san)
        st = bs.at(b)
        if st is None:
            print("Not in the study. Run `chesscoach book study` first (or play fewer moves).")
            return 1
        print(f"{st['eco']} {st['name']}".strip() or "(no opening name)",
              f"· Stockfish: {st['verdict']}" if st["verdict"] else "")
        for m in st["moves"]:
            print(f"\n{m['san']}  {round(100 * m['share'])}% of the book"
                  + (f" · after it: {m['verdict']}" if m["verdict"] else ""))
            if m["idea"]:
                print(f"  {m['idea']}")
        return 0
    book = args.bookfile or config.settings.book_path
    p = bs.plan(book, args.plies, args.min_share, args.min_line, args.max_moves)
    est = bs.estimate(p, secs_per_comment=args.secs)
    done = bs.summary()
    print(f"Book: {book}")
    print(f"{est['positions']} positions, {est['moves']} moves to explain "
          f"(depth {args.plies} half-moves, a move followed when the book plays it "
          f"{round(100 * args.min_share)}%+ and its line stays above {args.min_line:.1%}).")
    print("Moves per half-move: " + ", ".join(f"{k + 1}: {v}" for k, v in est["by_ply"].items()))
    print(f"Roughly {_hm(est['hours'])} at {args.secs:.0f} s per comment"
          + (f"; {done['ideas']} ideas already written are kept." if done["ideas"] else "."))
    if args.dry_run:
        return 0
    print("Stop with Ctrl+C at any time; run the same command again to continue.\n")

    def progress(done, total, what, left):
        print(f"[{done:>4}/{total}] {what:<60} ~{_hm(left / 3600)} left", flush=True)
    try:
        res = bs.run(book, model, args.plies, args.min_share, args.min_line, args.max_moves,
                     depth=args.study_depth, redo=args.redo, progress=progress)
    except KeyboardInterrupt:
        print("\nStopped. What was written is kept; run the same command to continue.")
        return 0
    s = bs.summary()
    print(f"\nDone: {res['written']} ideas written this run; the study holds {s['ideas']} of "
          f"{s['moves']} moves. See them in the app's opening explorer, or "
          f"`chesscoach book show \"e4 e5\"`.")
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config.settings = config.load_settings(
            args.config, stockfish_path=args.stockfish_path,
            book_path=args.book_path, maia_cmd=args.maia_cmd, depth=args.depth)
        models = args.models or [config.settings.model]

        if args.command == "config":
            if args.init:
                path = config.write_template(
                    stockfish=config.settings.stockfish_path,
                    book=config.settings.book_path)
                print(f"Created {path}\nEdit it, then run `chesscoach config` to check.")
                return 0
            print(f"Settings from: {config.settings.source}")
            print(f"User config file: {config.user_config_path()}")
            for f in fields(config.Settings):
                if f.name != "source":
                    print(f"  {f.name:<15} {getattr(config.settings, f.name)!r}")
            return 0

        if args.command in ("explain", "ask"):
            from .coach import ask, board_from, explain, open_engine
            board, history = board_from(args.fen, args.moves)
            engine = open_engine()
            try:
                if args.command == "explain":
                    a = explain(board, models[0], engine, history)
                else:
                    a = ask(board, args.question, models[0], engine, history,
                            mode="agent" if args.agent else None)
            finally:
                engine.quit()
            if a.error:
                raise ValueError(a.error)
            if args.command == "ask" and args.show_tools:
                if a.route:
                    print(f"  [route] {a.route}")
                for name, targs in a.tool_calls:
                    print(f"  [tool] {name} {targs}")
                for secs, n_in, n_out in a.calls:
                    print(f"  [model] {secs}s, {n_in} tokens in, {n_out} out")
            print(a.text)
            if a.removed:
                print("\n(checked against the board; left out as not true: "
                      + "; ".join(a.removed) + ")")
            print(f"\n({a.secs:.1f}s)")
            return 0
        if args.command == "patterns":
            from . import history
            recs = history.load()
            if not recs:
                print(f"No reviewed games stored yet in {history.history_dir()}. "
                      "Review a game with --side white or --side black (or set "
                      "player_name) and it is stored automatically.")
                return 0
            claims = history.patterns(recs, last=args.last)
            for c in claims:
                print(f"- {c.text}")
                if args.facts:
                    for f in c.facts:
                        print(f"    · {f}")
            problems = history.verify_patterns(recs, claims)
            if problems:
                print("\nCould not trace: " + "; ".join(problems))
                return 1
            return 0
        if args.command == "book":
            return _book(args, models[0])
        if args.command == "eval":
            from .evaluate import main_eval
            return main_eval(args, args.models or [])
        if args.command == "review":
            from .review import annotate_game
            annotate_game(args.pgn, args.side, models, args.verbose, args.every_move)
        elif args.command == "gui":
            from .gui.app import launch
            launch(debug=args.debug)
        elif args.command == "demo":
            from .demo import run_fixed_tests
            run_fixed_tests(models)
        return 0
    except (config.ConfigError, ValueError, FileNotFoundError) as e:
        print(f"chesscoach: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
