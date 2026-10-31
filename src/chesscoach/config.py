"""
Settings: where the engines and book live, which model narrates.

Resolution order (later wins):
  1. built-in defaults
  2. config file (TOML): --config PATH if given, else the user config file
       (Windows: %APPDATA%\\chesscoach\\config.toml,
        elsewhere: ~/.config/chesscoach/config.toml).
       A chesscoach.toml in the current folder is NOT read on its own: it
       sets the engine paths, so one lying next to a downloaded PGN could
       run any program. Use --config ./chesscoach.toml for a local one.
  3. environment variables  CHESSCOACH_<NAME>  (e.g. CHESSCOACH_STOCKFISH_PATH)
  4. command-line flags

Run `chesscoach config` to see what is in effect, and
`chesscoach config --init` to create a commented config file.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Optional

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


class ConfigError(Exception):
    """A required setting is missing or points nowhere."""


@dataclass
class Settings:
    stockfish_path: str = ""          # Stockfish executable
    maia_cmd: str = "maia3-5m"        # Maia 3 UCI command (pip install maia3)
    player_elo: int = 1500            # your rating: Maia predicts at this level
    player_name: str = "Player"       # your name in saved games
    model: str = "qwen3:14b"          # Ollama model that narrates
    depth: int = 18                   # Stockfish search depth
    max_seconds: float = 10.0         # cap per position: stop there even if the depth isn't reached
    multipv: int = 3                  # lines analysed per position
    book_path: str = ""               # Polyglot opening book (.bin)
    temperature: float = 0.3          # lower = less invention
    num_predict: int = 800            # max tokens per annotation
    keep_alive: str = "30m"           # keep the model loaded between calls
    num_ctx: int = 4096               # model context window (tokens)
    comment_language: str = "en"      # which comments to show from bilingual annotated PGNs
                                      # (ChessBase en/de): "en", "de" or "both"
    prompt_notes: str = "events"      # what the coach is told a move does: "events" (7g) or "effects" (old)
    plans: bool = True                # explain quiet engine moves: idea, problem, solution
    keep_history: bool = True         # store each review's facts for patterns across games
    library_dir: str = ""             # your games (PGN files + index); "" = "games" beside the config
    autosave: bool = True             # keep every played and reviewed game in the library
    ask_mode: str = "router"          # "router": code picks the facts; "agent": model calls tools
    grounding: bool = True            # verify the model's claims on the board
    concepts: bool = True             # engine-verified threats, piece roles, key facts
    concept_depth: int = 14           # depth of those extra searches
    blunder_alert: int = 20           # Play: ask "are you sure?" before a move losing this many
                                      # points of winning chance (30 blunders, 20 mistakes, 10 inaccuracies; 0 off)
    source: str = "defaults"          # where the settings came from (info only)

    def require_stockfish(self) -> None:
        if not self.stockfish_path:
            raise ConfigError(
                "Stockfish path is not set. Put stockfish_path in the config "
                "file (run `chesscoach config --init`) or set "
                "CHESSCOACH_STOCKFISH_PATH.")
        if not os.path.exists(self.stockfish_path):
            raise ConfigError(f"Stockfish not found at {self.stockfish_path}")


def user_config_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "chesscoach" / "config.toml"


def find_config_file(explicit: Optional[str] = None) -> Optional[Path]:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise ConfigError(f"Config file not found: {p}")
        return p
    p = user_config_path()
    return p if p.exists() else None


def _coerce(name: str, value):
    kind = {f.name: f.type for f in fields(Settings)}[name]
    if kind in ("bool", bool):
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if kind in ("int", int):
        return int(value)
    if kind in ("float", float):
        return float(value)
    return str(value)


def load_settings(config_file: Optional[str] = None, **overrides) -> Settings:
    """Build settings from defaults, config file, environment and overrides."""
    s = Settings()
    s.stockfish_path = shutil.which("stockfish") or ""
    names = [f.name for f in fields(Settings) if f.name != "source"]

    path = find_config_file(config_file)
    if path:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        unknown = set(data) - set(names)
        if unknown:
            raise ConfigError(f"Unknown setting(s) in {path}: {', '.join(sorted(unknown))}")
        for k, v in data.items():
            setattr(s, k, _coerce(k, v))
        s.source = str(path)

    for k in names:
        env = os.environ.get(f"CHESSCOACH_{k.upper()}")
        if env:
            setattr(s, k, _coerce(k, env))
            s.source += f" + env {k}"

    for k, v in overrides.items():
        if v is not None:
            setattr(s, k, _coerce(k, v))
    return s


TEMPLATE = '''# chesscoach settings. Paths may use forward slashes on Windows too.

# Stockfish executable (required)
stockfish_path = "{stockfish}"

# Maia 3 command for human-move prediction (optional; pip install maia3)
maia_cmd = "maia3-5m"

# Your approximate rating. Maia predicts what a player of this strength
# would play, which decides whether a good move was "natural" or a real find.
player_elo = 1500

# Your name, used in games you play against the coach
player_name = "Player"

# Polyglot opening book, e.g. GMopenings.bin from Lucas Chess (optional)
book_path = "{book}"

# Ollama model that writes the annotations
model = "qwen3:14b"

# Stockfish depth and number of lines per position
depth = 18
multipv = 3

# Narration: lower temperature = less invention
temperature = 0.3
num_predict = 800
keep_alive = "30m"
# Context window. Prompts are under 2,000 tokens; a larger window costs
# GPU memory and can push part of the model onto the CPU. Use the same
# value everywhere so the model is not reloaded between calls.
num_ctx = 4096

# Ask the coach: "router" = code works out which facts the question needs
# and the model only writes the answer (reliable); "agent" = the model
# chooses its own tool calls (needs a model that is good at tool use).
ask_mode = "router"

# Store the facts of each review (labels, winning chances, what each error
# got wrong) in a "history" folder in your user config folder (next to the
# default config file), for patterns across your games: `chesscoach patterns`.
keep_history = true

# Check the claims the model makes about the board that code can parse
# (pieces on squares, attacks, defences, pins, forks, king shelter, files,
# material). A claim that fails gets one retry; if it still fails, its
# sentence is removed. Judgements ("strong", "active") are not checked.
grounding = true

# Engine-verified concepts for each commented moment: real threats (what
# the opponent would do with a free move), what the moved piece was holding
# together, and a short list of the facts that matter most. Costs a few
# extra searches per moment at concept_depth.
concepts = true
concept_depth = 14

# Play: before a move that throws away this many points of winning chance,
# ask "are you sure?" (without saying why). 30 = blunders only, 20 =
# mistakes too, 10 = inaccuracies too, 0 = off.
blunder_alert = 20
'''


def write_template(path: Optional[Path] = None, stockfish: str = "",
                   book: str = "") -> Path:
    path = path or user_config_path()
    if path.exists():
        raise ConfigError(f"{path} already exists — edit it instead.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE.format(stockfish=stockfish.replace("\\", "/"),
                                    book=book.replace("\\", "/")), encoding="utf-8")
    return path


def save_settings(s: Settings, path: Optional[Path] = None) -> Path:
    """Write all settings to the user config file (used by the GUI)."""
    path = path or user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# chesscoach settings (written by chesscoach)"]
    for f in fields(Settings):
        if f.name == "source":
            continue
        v = getattr(s, f.name)
        if isinstance(v, bool):
            v = "true" if v else "false"
        elif isinstance(v, str):
            v = '"' + v.replace("\\", "/").replace('"', '\\"') + '"'
        lines.append(f"{f.name} = {v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# The settings in effect. The CLI replaces this after reading flags;
# library users can call config.settings = load_settings(...) themselves.
settings = Settings()
