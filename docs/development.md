# chesscoach: development

[Back to the README](../README.md)

## Requirements

For installing from the source (any system):


- Python 3.10+
- [Stockfish](https://stockfishchess.org/download/)
- [Ollama](https://ollama.com) with a model pulled, e.g. `ollama pull qwen3:14b`
  (fits a 12 GB GPU; smaller 7–8B models invent noticeably more), **or** a key
  for an online model (see [An online model instead of Ollama](guide.md#an-online-model-instead-of-ollama))
- Optional: [Maia 3](https://github.com/CSSLab/maia3) for human-move prediction
- Optional: a Polyglot opening book (`.bin`), so opening theory is skipped

## Install

```
git clone https://github.com/amryounis/chesscoach
cd chesscoach
pip install -e ".[gui]"      # or just  pip install -e .  for the command line only
pip install -e ".[gui,advanced]"   # with the advanced analyzer (uciboard), from GitHub
```

If you develop uciboard too, install your own copy of it instead
(`pip install -e ..\advanced-chess-analayzer`): chesscoach then opens that one.

## Configure

```
chesscoach config --init     # writes a commented config file
chesscoach config            # shows the settings in effect
```

Set at least `stockfish_path`. Settings can also come from environment
variables (`CHESSCOACH_STOCKFISH_PATH`, `CHESSCOACH_MODEL`, …) or flags
(`--stockfish`, `--book`, `--model`, `--depth`).

## Tests and lint

```
pip install -e ".[dev]"
pytest              # engine tests are skipped if Stockfish isn't found
ruff check src tests
```

The Windows installer is built by GitHub Actions (`.github/workflows/installer.yml`):
Actions → "Windows installer" → **Run workflow** (the installer is in the run's
artifacts), or push a tag `v<version>` to make a release with it. The files are in
`packaging/`: PyInstaller's `chesscoach.spec`, its start `launch.py`, and Inno
Setup's `chesscoach.iss`.

The README's screenshots (`docs/review.png`, `docs/explain.png`) are made by
`SHOTS=1 pytest tests/test_zz_readme_shots.py` (skipped otherwise).

Package layout (`src/chesscoach/`):

| Module | Responsibility |
|---|---|
| `config` | settings from defaults, config file, environment, flags |
| `engine` | Stockfish analysis, Maia prediction |
| `assessment` | win probability, move labels, verdict wording |
| `describe` | moves, effects and boards in plain English |
| `facts` | Tier 1 facts: named material, castling, undefended pieces, legal moves, following a line |
| `features` / `tactics` | positional and tactical facts; comparing two lines |
| `prompts` / `narrator` | prompt building, LLM calls, output check and retry |
| `grounding` | checks the claims it can parse (pieces, attacks, pins, forks, shelter, files, material) on the board; judgements are not checked |
| `concepts` | engine-verified threats, piece roles, equal moves, ranked key facts |
| `coach` | "Explain this position", and the "Ask the coach" agent with its fact tools |
| `opening` / `moments` | book detection, choosing what to comment on |
| `review` / `report` | whole-game review (structured data) and the markdown report |
| `play` | training games against Maia (or limited Stockfish) |
| `gui` | desktop app: `api.py` (Python side), `static/` (page), `app.py` (window) |
| `evaluate` | answer-key harness (`chesscoach eval`) |
| `accuracy` / `motifs` / `classify` | accuracy %, tactical motifs in engine lines, !! ! !? and the counts |
| `library` | your games: PGN files with an index, saved reviews, the opening explorer |
| `pgn_export` | the reviewed game as an annotated PGN |
| `practice` | your mistakes as exercises |
| `history` / `summary` | patterns across games; the game summary |
| `logfile` | chesscoach.log: errors and review timings |
| `cli` | the `chesscoach` command |
| `bundle` / `firstrun` | the installed program's Stockfish; the first-run setup's checks |
| `brief` | Explain this position: the coach's sheet, written by code, and what it draws |
| `setup_position` | setting up a position by hand: what is wrong with it |
| `problem` | Report a problem: what a tester sends |
