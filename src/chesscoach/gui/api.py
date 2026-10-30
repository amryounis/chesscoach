"""
The bridge between the page (JavaScript) and chesscoach (Python).

pywebview exposes every public method of `Api` to the page as
`window.pywebview.api.<name>(...)`, returning a Promise. Methods return
plain dicts; on failure they return {"error": "..."} instead of raising, so
the page can show the message. Internal state lives in `_` attributes,
which pywebview does not expose.
"""

from __future__ import annotations

import os
import threading
import traceback
from dataclasses import asdict, fields
from typing import Optional

import chess
import chess.engine
import chess.pgn

from .. import __version__, config
from ..play import PlayGame, preview_opponent, random_color
from ..review import Cancelled, review_game, write_markdown


def log_error(where: str) -> None:
    """The current exception's traceback, printed and appended to
    chesscoach.log beside the config file: the windowed app
    (chesscoach-app) has no console, so the log is where it can be found."""
    from ..logfile import append
    text = traceback.format_exc()
    print(text, end="")
    append(where, text)


def _safe(fn):
    """Turn exceptions into {"error": message} for the page."""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (config.ConfigError, ValueError, FileNotFoundError) as e:
            return {"error": str(e)}
        except Exception as e:  # unexpected: keep the details for debugging
            log_error(fn.__name__)
            return {"error": f"{type(e).__name__}: {e} (details in chesscoach.log "
                             "next to the config file)"}
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def _dialog_kind(kind: str):
    """pywebview 6 uses FileDialog.OPEN/SAVE; 5.x used OPEN_DIALOG/SAVE_DIALOG.
    Without pywebview (the page in a test browser, with a stand-in window)
    the kind's name is passed through: only a real window needs the
    constant, and a real window means pywebview is there."""
    try:
        import webview
    except ImportError:
        return kind
    fd = getattr(webview, "FileDialog", None)
    if fd is not None:
        return getattr(fd, kind)
    return getattr(webview, f"{kind}_DIALOG")


def game_summary(game: chess.pgn.Game) -> dict:
    """Moves of a game in the same shape the review fills in later, with
    what code can say before any engine runs: opening names and ideas,
    where the game left theory, and a note on what each move does."""
    from ..describe import move_notes, move_shapes
    from ..opening import opening_notes
    board = game.board()
    moves, boards, plain = [], [board.copy()], []
    for i, m in enumerate(game.mainline_moves()):
        n, san = board.fullmove_number, board.san(m)
        white = board.turn == chess.WHITE
        before = board.fen()
        notes, shapes = move_notes(board, m), move_shapes(board, m)
        board.push(m)
        boards.append(board.copy())
        plain.append(m)
        moves.append({"ply": i, "san": san, "uci": m.uci(),
                      "move_label": f"{n}. {san}" if white else f"{n}...{san}",
                      "color": "white" if white else "black",
                      "fen_before": before, "fen_after": board.fen(), "notes": notes,
                      "shapes": shapes})
    for mv, extra in zip(moves, opening_notes(boards, plain)):
        mv.update(extra)
    from ..pgn_reader import game_tree
    # Always the tree: the Annotations pane shows the annotator's notes when
    # the PGN has them and the coach's once the game is reviewed.
    return {"headers": dict(game.headers), "start_fen": game.board().fen(),
            "moves": moves, "tree": game_tree(game)}


def _patterns_after(review) -> list:
    """Store the review in the history, then the patterns across your games."""
    if not config.settings.keep_history:
        return []
    from .. import history
    # Anything going wrong here costs the patterns, never the review: the
    # review is finished and the player should see it.
    try:
        history.save(review, config.settings.player_name)
        return [c.to_dict() for c in history.patterns(history.load())]
    except Exception:
        log_error("patterns across games")
        return []


class Api:
    def __init__(self):
        self._window = None                 # set by the app after the window exists
        self._game: Optional[chess.pgn.Game] = None
        self._games: list = []                # every game in the file opened last
        self._game_path: str = ""
        self._review = None
        self._job: Optional[threading.Thread] = None
        self._cancel = threading.Event()
        self._status: dict = {"state": "idle"}
        self._lock = threading.Lock()
        self._play: Optional[PlayGame] = None
        self._coach_lock = threading.Lock()
        self._game_key = 0                    # changes with every game opened
        self._play_saved = ("", 0, "")        # (id, moves, result) of the played game as last saved
        self._drills: dict = {}               # practice: id -> drill, from the last listing
        self._practice_sf = None
        self._practice_lock = threading.Lock()

    def _reviewing(self) -> bool:
        return bool(self._job and self._job.is_alive())

    def _not_while_reviewing(self, what: str) -> None:
        """A review reads the open game and the settings until it ends;
        changing either under it put the result on the wrong game or mixed
        depths. So they wait."""
        if self._reviewing():
            raise ValueError(f"A review is running. Stop it, or wait for it to finish, "
                             f"before {what}.")

    # -- settings ----------------------------------------------------------

    @_safe
    def get_settings(self) -> dict:
        s = asdict(config.settings)
        s["config_file"] = str(config.user_config_path())
        s["version"] = __version__
        return s

    @_safe
    def save_settings(self, values: dict) -> dict:
        self._not_while_reviewing("changing the settings")
        s = config.settings
        for f in fields(config.Settings):
            if f.name in values and f.name != "source":
                setattr(s, f.name, config._coerce(f.name, values[f.name]))
        path = config.save_settings(s)
        s.source = str(path)
        return self.get_settings()

    # -- loading games -----------------------------------------------------

    @_safe
    def open_pgn_dialog(self) -> dict:
        self._not_while_reviewing("opening another game")
        paths = self._window.create_file_dialog(
            _dialog_kind("OPEN"), file_types=("PGN files (*.pgn)", "All files (*.*)"))
        if not paths:
            return {"cancelled": True}
        return self.load_pgn_file(paths[0])

    @_safe
    def load_pgn_file(self, path: str) -> dict:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        result = self.load_pgn_text(text)
        if "error" not in result:
            self._game_path = path
        return result

    @_safe
    def load_pgn_text(self, text: str) -> dict:
        self._not_while_reviewing("opening another game")
        from ..pgn_reader import game_list, read_games
        games = [g for g in read_games(text) if any(True for _ in g.mainline_moves())]
        if not games:
            raise ValueError("No moves found in that PGN.")
        if len(games) == 1 and games[0].errors:
            raise ValueError(f"The PGN has an error: {games[0].errors[0]}")
        self._games, self._game_path = games, ""
        out = self.select_game(0)
        out["games"] = game_list(games) if len(games) > 1 else []
        return out

    @_safe
    def select_game(self, index: int) -> dict:
        """Open game `index` of the file opened last (a file can hold many)."""
        games = getattr(self, "_games", None) or []
        if not 0 <= int(index) < len(games):
            raise ValueError("No such game in this file.")
        self._not_while_reviewing("switching games")
        self._game, self._review = games[int(index)], None
        self._game_key += 1
        out = game_summary(self._game)
        out["index"] = int(index)
        out["key"] = self._game_key
        # A game reviewed before opens with its review: no need to run
        # Stockfish again (Review again redoes it).
        saved = self._restore_review(self._game)
        if saved is not None:
            out["review"] = saved
        return out

    def _restore_review(self, game) -> Optional[dict]:
        from .. import library
        from ..review import GameReview, MoveReview
        from ..practice import looks_like_error
        # A kept review that can't be read is ignored: the game still opens.
        try:
            data = library.saved_review(game)
            if not isinstance(data, dict) or not isinstance(data.get("moves"), list) \
                    or not all(isinstance(m, dict) for m in data["moves"]):
                return None
            mf = {f.name for f in fields(MoveReview)}
            gf = {f.name for f in fields(GameReview)}
            for m in data["moves"]:
                # reviews from before 0.13 stored an Ollama error as the comment
                ann = m.get("annotations") if isinstance(m.get("annotations"), dict) else {}
                for model, text in list(ann.items()):
                    if isinstance(text, str) and looks_like_error(text):
                        m.setdefault("errors", {})[model] = ann.pop(model)
            moves = [MoveReview(**{k: v for k, v in m.items() if k in mf}) for m in data["moves"]]
            self._review = GameReview(**{k: v for k, v in data.items() if k in gf and k != "moves"},
                                      moves=moves)
        except Exception:
            log_error("restoring a kept review")
            return None
        return data

    # -- the library: your games ---------------------------------------------

    @_safe
    def library_list(self, filters: Optional[dict] = None) -> dict:
        from .. import library
        f = filters or {}
        out = library.query(text=f.get("text", ""), file=f.get("file", ""),
                            result=f.get("result", ""), mine=f.get("mine", ""),
                            reviewed=bool(f.get("reviewed")), sort=f.get("sort", "date"),
                            desc=f.get("desc", True))
        out["files"] = library.pgn_files()
        out["folder"] = str(library.library_dir())
        out["me"] = config.settings.player_name
        return out

    @_safe
    def library_open(self, game_row: int) -> dict:
        from .. import library
        self._not_while_reviewing("opening another game")
        game, name = library.load(int(game_row))
        self._games, self._game_path = [game], str(library.library_dir() / name)
        out = self.select_game(0)
        out["games"] = []                    # one game: no picker left over from a file opened before
        return out

    @_safe
    def library_add_dialog(self) -> dict:
        from .. import library
        paths = self._window.create_file_dialog(
            _dialog_kind("OPEN"), file_types=("PGN files (*.pgn)", "All files (*.*)"))
        if not paths:
            return {"cancelled": True}
        name, n = library.add_file(paths[0] if not isinstance(paths, str) else paths)
        return {"file": name, "games": n}

    @_safe
    def library_remove(self, game_row: int) -> dict:
        from .. import library
        library.remove_game(int(game_row))
        return {"ok": True}

    @_safe
    def library_explore(self, line: Optional[list] = None, mine: str = "", file: str = "") -> dict:
        """The opening explorer after `line` (UCI moves from the start)."""
        from .. import library
        b, sans = chess.Board(), []
        for u in line or []:
            m = chess.Move.from_uci(u)
            if m not in b.legal_moves:
                raise ValueError(f"Illegal move in the line: {u}")
            sans.append(b.san(m))
            b.push(m)
        out = library.explore(b, mine=mine, file=file)
        dests: dict = {}
        for m in b.legal_moves:
            dests.setdefault(chess.square_name(m.from_square), []).append(chess.square_name(m.to_square))
        num = []
        for i, s in enumerate(sans):
            num.append(f"{i // 2 + 1}. {s}" if i % 2 == 0 else s)
        out.update(fen=b.fen(), line=" ".join(num), turn="white" if b.turn else "black",
                   dests=dests, me=config.settings.player_name, files=library.pgn_files())
        return out

    # -- the coach on any position ------------------------------------------

    def _position(self, ply: int):
        """(board, game-so-far) after move `ply` of the open game (-1: start)."""
        if self._game is None:
            raise ValueError("Open a game first.")
        board = self._game.board()
        moves = list(self._game.mainline_moves())[:max(0, int(ply) + 1)]
        start = board.copy()
        for m in moves:
            board.push(m)
        history = start.variation_san(moves) if moves else ""
        return board, history

    def _coach(self, fn, *args) -> dict:
        from .. import coach
        if not self._coach_lock.acquire(blocking=False):
            raise ValueError("The coach is still answering the last question.")
        try:
            engine = coach.open_engine()
            try:
                a = fn(*args, engine)
            finally:
                engine.quit()
        finally:
            self._coach_lock.release()
        if a.error:
            from ..narrator import check_ollama
            check_ollama(config.settings.model)    # the plain reason, when it's Ollama
            raise ValueError(a.error)
        return {"text": a.text, "removed": a.removed, "secs": round(a.secs, 1),
                "tools": [n for n, _ in a.tool_calls], "facts": a.facts}

    @_safe
    def explain_position(self, ply: int) -> dict:
        from .. import coach
        board, history = self._position(ply)
        model = config.settings.model
        return self._coach(lambda e: coach.explain(board, model, e, history))

    @_safe
    def ask_coach(self, ply: int, question: str) -> dict:
        from .. import coach
        question = (question or "").strip()
        if not question:
            raise ValueError("Type a question first.")
        board, history = self._position(ply)
        model = config.settings.model
        me = self._my_color()
        return self._coach(lambda e: coach.ask(board, question[:300], model, e, history, me=me))

    def _my_color(self) -> Optional[bool]:
        """The player's colour in the open game: the reviewed side, else the
        side with the player's name in the headers; None if unknown."""
        side = self._review.side if self._review is not None else ""
        if side in ("white", "black"):
            return side == "white"
        if self._game is None:
            return None
        name = (config.settings.player_name or "").strip().lower()
        for color, key in ((chess.WHITE, "White"), (chess.BLACK, "Black")):
            if name and self._game.headers.get(key, "").strip().lower() == name:
                return color
        return None

    # -- review ------------------------------------------------------------

    @_safe
    def start_review(self, side: str = "both", every_move: bool = False) -> dict:
        if self._game is None:
            raise ValueError("Open a game first.")
        if self._job and self._job.is_alive():
            raise ValueError("A review is already running.")
        config.settings.require_stockfish()
        from ..narrator import check_ollama
        check_ollama(config.settings.model)     # say so now, not after the engine pass
        self._cancel.clear()
        self._set_status(state="running", stage="engine", done=0, total=1,
                         message="Starting")
        game, key = self._game, self._game_key

        live: list = []                       # [cp, mate] per position analysed so far

        def progress(stage, done, total, message, info=None):
            info = info or {}
            if stage == "engine" and "verdict" in info:          # a finished position
                live.append([info.get("cp"), info.get("mate")])
            # The page follows along: the board shows the position being
            # analysed (or the moment being commented), the graph fills in.
            self._set_status(state="running", stage=stage, done=done, total=total,
                             message=message, ply=info.get("ply"),
                             verdict=info.get("verdict", ""), searching=bool(info.get("searching")),
                             live=list(live))

        def run():
            try:
                r = review_game(game, side, [config.settings.model], every_move,
                                progress=progress, should_stop=self._cancel.is_set)
                if self._game_key != key:             # cannot happen while switching is
                    raise Cancelled()                  # blocked; never show it on another game
                self._review = r
                result = r.to_dict()
                result["key"] = key
                result["patterns"] = _patterns_after(r)
                if config.settings.autosave:
                    from .. import library
                    try:
                        library.save_review(r, game)
                        result["saved_to"] = library.REVIEWED
                    except Exception:
                        log_error("saving the review to the library")
                self._set_status(state="finished", result=result)
            except Cancelled:
                self._set_status(state="cancelled", message="Review stopped.")
            except Exception as e:
                log_error("review")
                self._set_status(state="error", message=str(e))

        self._job = threading.Thread(target=run, daemon=True)
        self._job.start()
        return {"ok": True}

    def review_status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def cancel_review(self) -> dict:
        self._cancel.set()
        return {"ok": True}

    @_safe
    def save_report_dialog(self) -> dict:
        if self._review is None:
            raise ValueError("Run a review first.")
        base = os.path.splitext(os.path.basename(self._game_path or "game.pgn"))[0]
        path = self._window.create_file_dialog(
            _dialog_kind("SAVE"), save_filename=f"{base}_annotated.md")
        if not path:
            return {"cancelled": True}
        path = path if isinstance(path, str) else path[0]
        write_markdown(self._review, path)
        return {"saved": path}

    @_safe
    def save_annotated_pgn_dialog(self) -> dict:
        """The reviewed game as an annotated PGN any chess program opens."""
        from ..pgn_export import annotated_pgn
        if self._review is None:
            raise ValueError("Run a review first.")
        base = os.path.splitext(os.path.basename(self._game_path or "game.pgn"))[0]
        path = self._window.create_file_dialog(
            _dialog_kind("SAVE"), save_filename=f"{base}_annotated.pgn",
            file_types=("PGN files (*.pgn)", "All files (*.*)"))
        if not path:
            return {"cancelled": True}
        path = path if isinstance(path, str) else path[0]
        with open(path, "w", encoding="utf-8") as f:
            f.write(annotated_pgn(self._review, self._game))
        return {"saved": path}

    def _set_status(self, **status):
        with self._lock:
            self._status = status

    # -- practice ----------------------------------------------------------

    @_safe
    def practice_drills(self, scope: str = "game") -> dict:
        """Your mistakes as drills: from the game just reviewed ("game") or
        from every reviewed game in the history ("all"), most-needed first."""
        from .. import history, practice
        if scope == "all":
            drills = practice.drills_from_history(history.load())
            empty = ("No mistakes to practise yet. Review some games (with your name "
                     "set in Settings, or one side chosen) and they will collect here.")
        else:
            if self._review is None:
                raise ValueError("Review a game first, or practise all reviewed games.")
            drills = practice.drills_from_review(self._review)
            empty = "No inaccuracies, mistakes or blunders in this game for the reviewed side."
        prog = practice.load_progress()
        # One game: its errors in move order, like replaying it. All games:
        # a review queue (new and missed first), each with the reason shown.
        if scope == "all":
            drills = practice.order(drills, prog)
        else:
            drills = sorted(drills, key=lambda d: d["ply"])
        self._drills = {d["id"]: d for d in drills}
        for d in drills:
            d["progress"] = prog.get(d["id"], {})
            d["queue"] = practice.queue_tag(d["progress"]) if scope == "all" else ""
            dests: dict = {}
            for m in chess.Board(d["fen"]).legal_moves:
                dests.setdefault(chess.square_name(m.from_square), []).append(
                    chess.square_name(m.to_square))
            d["dests"] = dests
        return {"scope": scope, "drills": drills, "empty": "" if drills else empty}

    def _judge(self, drill_id: str, uci: Optional[str]) -> dict:
        from .. import practice
        drill = self._drills.get(drill_id)
        if drill is None:
            raise ValueError("That exercise is no longer loaded.")
        config.settings.require_stockfish()
        with self._practice_lock:
            if self._practice_sf is None:
                self._practice_sf = chess.engine.SimpleEngine.popen_uci(
                    config.settings.stockfish_path)
            res = practice.judge(drill, uci, engine=self._practice_sf)
        res["progress"] = practice.record_attempt(drill_id, res["verdict"])
        return res

    @_safe
    def practice_answer(self, drill_id: str, uci: str) -> dict:
        return self._judge(drill_id, uci)

    @_safe
    def practice_reveal(self, drill_id: str) -> dict:
        return self._judge(drill_id, None)

    # -- play --------------------------------------------------------------

    @_safe
    def play_opponent(self, elo: int = 1500) -> dict:
        """Who the player would face at this rating (shown before starting)."""
        return preview_opponent(int(elo))

    @_safe
    def new_game(self, color: str = "white", elo: int = 1500) -> dict:
        if self._play:
            self._autosave_play()               # an unfinished game is kept too ("*")
            self._play.close()
        self._play_saved = ("", 0, "")
        c = {"white": chess.WHITE, "black": chess.BLACK}.get(color) or random_color()
        self._play = PlayGame(c, int(elo))
        reply = self._play.engine_move()          # engine opens if player is Black
        return self._play_state(reply)

    @_safe
    def play_move(self, uci: str) -> dict:
        if not self._play:
            raise ValueError("Start a game first.")
        san = self._play.player_move(uci)
        reply = self._play.engine_move()
        return self._play_state(reply, played=san)

    @_safe
    def takeback(self) -> dict:
        if not self._play:
            raise ValueError("Start a game first.")
        self._play.takeback()
        reply = self._play.engine_move()          # never leave the board waiting on the engine
        return self._play_state(reply)

    @_safe
    def resign(self) -> dict:
        if not self._play:
            raise ValueError("Start a game first.")
        self._play.resign()
        return self._play_state(None)

    @_safe
    def review_played_game(self) -> dict:
        """Load the finished training game into the review tab."""
        if not self._play or not self._play.board.move_stack:
            raise ValueError("Play some moves first.")
        summary = self.load_pgn_text(self._play.pgn())
        summary["side"] = "white" if self._play.player_color == chess.WHITE else "black"
        return summary

    @_safe
    def save_pgn_dialog(self) -> dict:
        if not self._play:
            raise ValueError("Start a game first.")
        path = self._window.create_file_dialog(
            _dialog_kind("SAVE"), save_filename="training_game.pgn")
        if not path:
            return {"cancelled": True}
        path = path if isinstance(path, str) else path[0]
        with open(path, "w", encoding="utf-8") as f:
            f.write(self._play.pgn() + "\n")
        return {"saved": path}

    def _autosave_play(self) -> str:
        """Keep the played game in the library (Played.pgn) once it has a
        move of the player's; again only if it changed since."""
        g = self._play
        if not (g and config.settings.autosave):
            return ""
        own = len(g.board.move_stack) - (0 if g.player_color == chess.WHITE else 1)
        state = (len(g.board.move_stack), g.result())
        if own < 1 or state == self._play_saved[1:]:
            return ""
        from .. import library
        try:
            gid = library.save_played(g.to_pgn_game(), replaces=self._play_saved[0])
        except Exception:
            log_error("saving the played game")
            return ""
        self._play_saved = (gid, *state)
        return library.PLAYED

    def _play_state(self, reply: Optional[str], played: str = "") -> dict:
        g = self._play
        saved = self._autosave_play() if g.over else ""
        b = g.board
        last = b.peek().uci() if b.move_stack else ""
        moves, tmp = [], chess.Board()
        for m in b.move_stack:
            n, san, white = tmp.fullmove_number, tmp.san(m), tmp.turn == chess.WHITE
            tmp.push(m)
            moves.append({"san": san, "color": "white" if white else "black",
                          "move_label": f"{n}. {san}" if white else f"{n}...{san}"})
        return {"fen": b.fen(), "last_move": last, "moves": moves,
                "orientation": "white" if g.player_color == chess.WHITE else "black",
                "turn": "white" if b.turn == chess.WHITE else "black",
                "dests": {} if g.over or b.turn != g.player_color else g.legal_dests(),
                "check": b.is_check(), "over": g.over, "status": g.status(),
                "result": g.result(), "opponent": g.engine_name, "saved_to": saved,
                "opponent_info": g.opponent,
                "played": played, "reply": reply or ""}

    @_safe
    def needs_promotion(self, from_sq: str, to_sq: str) -> bool:
        return bool(self._play and self._play.needs_promotion(from_sq, to_sq))

    # -- lifecycle ---------------------------------------------------------

    def shutdown(self) -> None:
        self._cancel.set()
        if self._practice_sf is not None:
            try:
                self._practice_sf.quit()
            except Exception:
                pass
            self._practice_sf = None
        if self._play:
            self._autosave_play()
            self._play.close()
            self._play = None
