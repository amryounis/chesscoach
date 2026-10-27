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


def _safe(fn):
    """Turn exceptions into {"error": message} for the page."""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (config.ConfigError, ValueError, FileNotFoundError) as e:
            return {"error": str(e)}
        except Exception as e:  # unexpected: keep the details for debugging
            traceback.print_exc()
            return {"error": f"{type(e).__name__}: {e}"}
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def _dialog_kind(kind: str):
    """pywebview 6 uses FileDialog.OPEN/SAVE; 5.x used OPEN_DIALOG/SAVE_DIALOG."""
    import webview
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
    try:
        history.save(review, config.settings.player_name)
        return [c.to_dict() for c in history.patterns(history.load())]
    except OSError:
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
        self._drills: dict = {}               # practice: id -> drill, from the last listing
        self._practice_sf = None
        self._practice_lock = threading.Lock()

    # -- settings ----------------------------------------------------------

    @_safe
    def get_settings(self) -> dict:
        s = asdict(config.settings)
        s["config_file"] = str(config.user_config_path())
        s["version"] = __version__
        return s

    @_safe
    def save_settings(self, values: dict) -> dict:
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
        self._game, self._review = games[int(index)], None
        out = game_summary(self._game)
        out["index"] = int(index)
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
        return self._coach(lambda e: coach.ask(board, question[:300], model, e, history))

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
        game = self._game

        def progress(stage, done, total, message):
            self._set_status(state="running", stage=stage, done=done,
                             total=total, message=message)

        def run():
            try:
                r = review_game(game, side, [config.settings.model], every_move,
                                progress=progress, should_stop=self._cancel.is_set)
                self._review = r
                result = r.to_dict()
                result["patterns"] = _patterns_after(r)
                self._set_status(state="finished", result=result)
            except Cancelled:
                self._set_status(state="cancelled", message="Review stopped.")
            except Exception as e:
                traceback.print_exc()
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
            self._play.close()
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
        return self._play_state(None)

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

    def _play_state(self, reply: Optional[str], played: str = "") -> dict:
        g = self._play
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
                "result": g.result(), "opponent": g.engine_name,
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
            self._play.close()
            self._play = None
