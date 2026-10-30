# Changelog

## 0.17.0 — unreleased

- **Opening study** (`chesscoach book study`): an opening book walked once,
  and the idea of every popular move explained by the coach, so the
  explorer teaches ideas, not lines. For each move the coach gets facts
  from code: the opening it reaches and that opening's idea; development,
  central squares gained, diagonals opened, castling, pieces attacked or
  defended (an "attacked" pawn the book leaves en prise is not called a
  threat when Stockfish says the move is sound); how often the book plays
  it, the other book moves and the book's main answers, with their names;
  Stockfish's verdict and first choice. The comment goes through the board
  check (one rewrite, then a false sentence is removed) and rules of its
  own: only the moves in the facts, no numbers, no "winning".
  The walk is bounded by share, line frequency and depth; `--dry-run` says
  how many moves and how long (performance.bin with the defaults: 486
  positions, 746 moves, about 3½ hours at 15 s a comment); it is resumable
  and keeps what is written. `chesscoach book show "e4 e5"` prints a
  position's moves and ideas
- **The opening explorer** shows the study: the opening's name and
  Stockfish's verdict, the idea of the move just played, and for each move
  the book's share, Stockfish's verdict after it, its idea (hover), and
  your games with it. Without a study it says how to make one

## 0.16.1 — unreleased

From DeepSeek's review of 0.15.0; every point was checked against the code
first.

- **One game, one row.** A game played and then reviewed was in Played.pgn
  and Reviewed.pgn, listed twice in Your games and counted twice by the
  opening explorer (a file added twice did the same). The list and the
  explorer now show each game once (the Reviewed.pgn copy when there is
  one); filtering by one file still shows all of that file. Remove takes
  out every copy and the kept review
- The "Reviewed" filter no longer leaks a database connection, or fails
  with very many reviews (it joins a reviews table instead of listing ids);
  the app and a running review no longer touch the index at the same time
- **The checker credits the move only with what it did.** "8...Bg4 opens
  the f-file" passed because 9.hxg6 fxg6 opened it later. A sentence that
  says the move itself opens a file or reduces the king's shield pawns is
  now checked on the position right after the move; "allows White to
  open", "will", "after ..." are still checked along the line. Eleven
  old baseline comments are now caught for exactly this ("9...dxc4 weakens
  Black's king position by reducing the number of shield pawns"). The
  README's example (that same false 8...Bg4 sentence) is replaced by a real,
  correct one, and the README, the package table and the config file no
  longer say every claim is checked
- **Saved PGNs keep the game's own notes.** Reviewed.pgn and Save PGN start
  from the game as opened, with its comments, marks and variations, and add
  the coach's after them, marked "Coach:" (the summary "Coach summary:").
  Exporting again replaces the coach's earlier notes instead of stacking
  them, and an engine line the annotator already gave is not added twice
- Reopening a reviewed game no longer shows each coach comment twice (once
  from the review, once from the PGN)
- The engine's better line in the PGN carries the evaluation of the
  position after its first move (a mate is one move nearer), not before
- The summary said "Instead of your errors, the engine had a fork (3. Qh5)";
  it now says "You missed a fork (3. Qh5)"
- Opening a game from Your games clears the game picker left from a
  multi-game file; a kept review that can't be read is ignored instead of
  stopping the game from opening, and old reviews' stored Ollama errors are
  moved out of the comments
- Practice progress is written whole and swapped in, and bad entries in
  the file are skipped instead of losing the rest

## 0.16.0 — unreleased

Two of Lucas Chess's analysis views, kept to what can be checked:

- **Analysis (a third view beside Moves and Annotations, after a review):**
  every move with the engine's best move, the pawns lost against it, its
  accuracy, and Stockfish's own win / draw / loss estimate for the side
  that moved (UCI_ShowWDL); all moves, White's or Black's; click a row to
  go to the move. The W/D/L is Stockfish's model of engine-against-engine
  play (a two-pawn edge reads as a near-certain win), and the column says so
- **Move types per side** at the top of the game summary, as in Lucas
  Chess: accuracy, best-move share, and counts of !! brilliant, ! good, !?
  interesting, best, acceptable, ?! ? ?? and book moves; then accuracy and
  best-move share in the opening, middlegame and endgame. The marks are
  defined by rules (classify.py): ! is the only good move (the next best
  keeps 20+ points less winning chance) that a club player wouldn't play
  by instinct; !! is such a move that is also a sacrifice the line pays
  off; !? is a sacrifice that keeps the evaluation without being the
  engine's first choice; both need a game still to decide (10-90%). The
  marks show on the moves and go into the PGN as $3 / $1 / $5
- Not taken from Lucas Chess: the Elo performance (a match-the-engine
  score: 7 moves against Monkey earn "3293") and the indexes (domination,
  complexity, narrowness...: hand-tuned formulas over piece counts and
  mobility, with nothing a player can act on)

## 0.15.1 — unreleased

From reviewing Abasov - Dotzer in 0.15.0:

- **The wrong king turned red.** While the board followed a review, a check
  lit up White's king whoever was in check (50.Bf4+ showed the h4 king):
  the board was told "check" but not who was to move. It is now told
  which king, everywhere a move is shown
- **A review could sit on one position for minutes with no sign of life.**
  - Stockfish now stops at the depth or after "Most seconds per position"
    (Settings, default 10), whichever comes first. Most positions reach the
    depth in well under a second; a few endgames explode at high depth
    (here 51.Kg5 took 24 s at depth 30 with Stockfish 17.1)
  - The board shows each position as its search starts, not after, and
    the progress line counts the seconds once a step takes more than four
    ("Analysing position 101 of 102 · 37s")
  - The steps between the analysis and the comments, which said nothing
    before, are named: loading the model, starting Maia, finding the
    critical moments
  - Every review writes where its time went to chesscoach.log (next to the
    settings file), with any position that took over 3 seconds, so a slow
    review on your machine can be traced

## 0.15.0 — unreleased

Your games, standard PGN out, and two ideas from Lucas Chess.

- **Your games (new Games tab).** A folder of ordinary PGN files any chess
  program opens, with an index (SQLite) to search them:
  - Played.pgn: every game you play is kept as it ends (or when you start
    another before it ends, as "*"); taken back and played on, it is kept
    once, in its last version
  - Reviewed.pgn: every reviewed game as an annotated PGN; a re-review
    replaces it
  - Add PGN file… copies a file in (the original stays where it is)
  - Search by player, event or opening; filter by file, result, your games
    (as White / Black) and reviewed; sort by any column; Open (or double-
    click) puts the game in Review; Remove takes one game out of its file
  - Games without ECO/Opening headers are named from the openings table
  - The index follows files edited elsewhere, and can be deleted: it is
    rebuilt from the PGN files
  - The folder and autosave are in Settings (the folder defaults to "games"
    next to the settings file)
- **A reviewed game opens with its review.** Every review is kept whole, so
  opening that game again (from Your games or as a file) shows the review
  at once, without running Stockfish; "Review game" redoes it
- **Opening explorer** (Games tab): from your games' first 15 moves, the
  moves played in each position, how often and how they scored
  (White · Draw · Black bar), for everyone's games or yours as White or
  Black ("Your score" from your side). Click a move or play one on the
  board; the three most played are drawn as arrows
- **Save PGN… after a review** (and `chesscoach review` writes it beside
  the report): ?! / ? / ?? / ! as NAGs, [%eval] on every move, the coach's
  comments, the engine's better line as a variation on every error, ECO /
  Opening / Annotator headers, and the summary with accuracy as the game
  comment
- **Accuracy %** per side, by Lichess's published method (no Elo estimate
  from one game: Lucas Chess users themselves find that one unreliable)
- **Tactical motifs** (Lucas Chess tags them with Lichess's puzzle themes):
  for every error, what it allowed (the opponent's line) and what the
  engine had instead: fork, pin, skewer, discovered attack, double check,
  hanging piece, taking a defender, sacrifice, promotion, advanced pawn,
  mate, back-rank mate. Found by code, and only when the line shows the
  motif paying off. Shown under the comment, in the summary, in the PGN,
  and counted across your games ("your errors allowed a fork 3 times in
  two games")

## 0.14.0 — unreleased

- **The board follows a running review.** While Stockfish works, the board
  shows each position as it is analysed, with the eval bar, the move list
  (and the Annotations pane) on that move, and the evaluation graph
  drawing itself; the panel says "Stockfish is analysing 16.Nxa4: Black is
  much better. Position 32 of 79". Then, while the coach writes, the board
  goes to each critical moment ("The coach is writing about 5...c6, moment
  1 of 8"). Clicking a move or using the arrow keys takes the board back;
  "Follow" returns it to the analysis. The progress bar is wider and
  easier to see. The review's progress callback now carries the position
  and its score (a fifth argument, `info`)

## 0.13.1 — unreleased

From the first CI run (Actions had never run on this repository before):

- The page's tests failed wherever pywebview isn't installed (CI installs
  only the dev extra): "Open PGN" asked pywebview for its dialog constant
  even with the test's stand-in window. Without pywebview the kind's name
  is now passed through; a real window always has pywebview. Five browser
  tests, three of them older than 0.12, had been failing unseen
- A 0.13.0 test expected a config file named with --config to set the
  Stockfish path, but CI sets CHESSCOACH_STOCKFISH_PATH, which rightly wins
- CI: a "Run workflow" button, Ubuntu pinned to 24.04 (Stockfish 16, which
  the engine-dependent expectations were written against), one failing
  Python version no longer cancels the others, and the actions are on
  their Node 24 versions

## 0.13.0 — unreleased

From an outside review (DeepSeek) of 0.12.0. Every defect it named was
reproduced first, then fixed with a test.

- **Honest about checking.** The README promised that everything the
  model writes is checked; the checker reads only the claim shapes it
  knows. The README now says what is checked and what is not, and every
  comment in the app says "Facts about the pieces were checked against
  the board; judgements are the coach's opinion" (hover for the list).
  Three gaps closed: the king-shelter check now reads "the Black king"
  as well as "Black's king"; "opens the f-file" is checked (a pawn must
  leave the file within two plies); "forks the king and queen" must find
  a fork of those two kinds, and with squares named, two of them forked
  by the same piece. Two old baseline comments ("opens up the g-file"
  after 9.hxg6 fxg6) are now caught, correctly
- **Take back as Black** before your first move undid the engine's
  opening move and froze the board. It now never undoes the engine's
  first move, and after any takeback the engine moves if it is its turn
- **A review keeps its game.** Opening or switching games and saving
  settings are refused while a review runs (the buttons are disabled),
  and a result is only shown on the game it was started on
- **History can't lose a review.** Records are versioned, written
  atomically, and cleaned on reading (a malformed move or an unknown
  theme is dropped, not fatal); anything failing in the patterns step
  costs the patterns, never the finished review. Unexpected errors are
  written to chesscoach.log next to the config file (the windowed app has
  no console)
- **Checkmate keeps its winner.** Stockfish reports a mated position as
  "mate 0", which lost the side: White showed 0% after White had mated,
  and the graph ended at 50%. It is now +1/-1 for the winner, the
  convention the review already used for the mating move
- **A model error is never the coach's comment.** Errors are stored
  apart from comments (shown as "The coach could not write this
  comment: ..."), and errors stored as comments by older reviews are not
  practised as "The coach said in the review"
- **"Is my bishop safe?"** finds the bishop: your side's when the app
  knows it (the reviewed side, or your name), "White's"/"Black's" as
  said; with several, the answer covers each
- **A chesscoach.toml in the current folder is no longer read** unless
  named with --config: it sets engine paths, so one next to a downloaded
  PGN could run any program
- Settings: "Comments in annotated games" is now "Show comments of
  bilingual PGNs in", which is what it does. README and pyproject point
  at github.com/amryounis/chesscoach
- CI: Python 3.13, a Windows job (installs the GUI extra, imports the
  window code, runs the tests that need no engine), and a wheel job
  (installs the built wheel alone and checks the data files and the page)

## 0.12.2 — unreleased

From using 0.12.1:

- **Explain this position / Ask the coach no longer hide the comment.**
  The answer used to take a fixed 38% of the height below the comment,
  which squeezed the comment out of view (and left the splitter nothing to
  resize). The comment and the answers about a position now scroll
  together: the answer appears under the comment and is scrolled into
  view, and going to a move always opens on its comment. The splitter
  works again
- **The coach's comments are in the Annotations pane** once a game is
  reviewed, set apart from the annotator's (a teal bar headed "Coach ·
  Mistake ?"), with the review's mark on the move when the PGN gave none.
  The pane now exists for every game, annotated or not, and opens after a
  review
- **The evaluation graph is a line on a framed panel** instead of a white
  area that blended into the page; White/Black are named in a gutter, and
  short ticks mark the errors (inaccuracy, mistake, blunder colours)
- **Practice order:** one game's exercises now come in move order; "All
  your reviewed games" keeps the review queue (new and missed first) and
  says why each one is where it is ("New", "Missed last time", "Solved
  once")

## 0.12.1 — unreleased

- Fix: 0.12.0 kept the practice attempts (practice.json) in the history
  folder, and the history loader read it as a game record, so reviews and
  "All your reviewed games" failed with `KeyError: 'id'` after the first
  practice attempt. The file now lives beside the history folder (moved
  there automatically), and the loader skips anything that isn't a game.

## 0.12.0 — unreleased

From an outside review of 0.11.0: two places where the app could say
something inaccurate, and the first practice loop.

- **"Left theory" only on evidence** (`opening.theory_exit`). Without an
  opening book every ply was "not book", so the moment picker called move
  1 the move that left theory, while the move notes used the openings
  table and could name a different move. One rule now serves the moment
  picker, the coach's prompt, the move notes and the report: the book
  when it knows the game's first move; otherwise the openings table,
  worded as "left the named opening lines" (never "theory"); with neither,
  nothing is claimed
- **Play says who you are playing.** Without Maia, Play falls back to
  Stockfish, which can't go below 1320. The setup screen now names the
  opponent and its real strength as the slider moves ("Stockfish at 1320,
  its lowest setting, which is stronger than the 800 you chose"), the
  intro stops promising Maia, and the note stays above the game. "Maia
  didn't start" and "Maia isn't installed" are told apart
- **Practice** (new tab, `practice.py`): your inaccuracies, mistakes and
  blunders come back as positions. Make a move on the board; Stockfish
  judges it with the review's own thresholds, so another move that keeps
  your chances is accepted and said to be so, the game's move is
  recognised, and a losing one is labelled like a review would. Then the
  engine's move, its line and the coach's comment from the review.
  Exercises come from the game just reviewed or from every reviewed game
  (history records now keep the move, the engine's move and the comment;
  records saved before 0.12 have no engine move and are skipped).
  Attempts are kept in `practice.json` next to the history: missed ones
  come back first, blunders before mistakes before inaccuracies. A
  "Practise these moves" button sits after the critical moments
- **Ollama is checked before a review starts.** If it isn't running, or the
  narrator model isn't pulled, the review stops at once and says which, in
  plain words, instead of spending minutes on Stockfish and then writing
  "Failed to connect to Ollama" as every comment. If Ollama goes away in
  the middle, the review stops with the same message. Explain this
  position and Ask the coach give the same plain reason
- The comment box spans the panel, so its scrollbar lines up with the move
  list's (the text keeps its readable width)
- The version shows in the window title and next to the name in the top
  bar ("chesscoach v0.12.0"), so you can tell which build is running

## 0.11.0 — unreleased

Plans (after DecodeChess) and the new move notes in the coach's prompts.

- **The plan behind a quiet engine move** (`plans.py`): the idea it
  prepares (what the side would play with a free move), why the idea
  fails if played at once (winning chance, and the opponent's reply that
  spoils it when code can find it: a reply hitting the square the quiet
  move left), the quiet move first, and the line after it. On
  Kramnik-Leko 2001 it finds DecodeChess's answer: 21.Qa1 prepares Bxd5;
  at once, 21.Bxd5 exd5 22.Rxd5 Qb6 hits the queen on b2 (53% instead of
  68%). Searched at full depth (at 14 the engine misses it); about three
  extra searches per commented move, and only when the engine's move is
  quiet
- The coach gets the plan as verified facts, in words; its positions are
  added to the grounding check. The app shows an Idea / Problem /
  Solution / Outcome block under the comment and a blue arrow for the
  idea with "Show the better move"; the report shows the plan
- **The coach's prompts now use the phase 7g move notes** (a threat said
  once, recaptures, lines opened, pins, material after an exchange)
  instead of the older effects list. `prompt_notes = "effects"` (or
  CHESSCOACH_PROMPT_NOTES=effects) brings the old list back for a
  comparison run; `plans = false` turns plans off
- Grounding: a move in "What now" must be made by the side the sentence
  names ("White should ... and then retreat the king to f8" was Black's
  Kf8 in the engine's line, and got through), and "moving the queen to b3
  to support the knight" is checked even without the knight's square (the
  knight under attack is meant; the c3 knight blocks b3-f3)

## 0.10.0 — unreleased

Phase 7g: a richer vocabulary for the note under every move
(`events.py`), checked move by move against Chessmaster 10's
auto-annotation of Homer vs Fritz (`tests/test_events.py`). Each sentence
comes with the arrows that show it.

- Checks: checkmate, double check, discovered check ("28...e4+ gives a
  discovered check: the bishop on f6 now checks the king")
- A piece that can simply be taken: "It takes the pawn, but the pawn on
  d6 can take the bishop back: a bishop for a pawn" (9.Bxe5); "It puts
  the knight where … can take it". Pawns are left out (gambits)
- Lines opened by the move: "It opens the line for the bishop on f1,
  which now defends the pawn on c4 against the pawn on d5" (9.e3)
- Threatened pieces made safe: "It defends the pawn on b2, which the
  queen on b6 was attacking" (14.O-O-O)
- A threat is reported once, on the move it appears (the gambit pawn on
  c4 no longer gets "It IGNORES a threat" on five moves in a row)
- A lost defence only when the piece is left with no defender
- Recaptures; material after an exchange ends ("Material is level: a
  knight against a bishop", "Black is up a rook and a pawn"), never in
  the middle of one
- Pins made (to a rook, queen or king) and pins released
- Pawns: passed pawn created or pushed, one step from promoting,
  promotion, pawns isolated or doubled (after the exchange)
- After a review: "Black can now force mate in 8"
- Up to three notes per move, most telling first
- From Amr's report of 25 Oct (Homer vs Fritz, both sides):
  - Grounding: a claim that a move weakens a side's pawn shelter must
    show in that side's shield pawns along the move's line (10.Bxc4
    "weakens White's king position by removing a shield pawn" got
    through; the facts were about Black's shelter)
  - Grounding: "moving the queen to c2, which maintains defence of the
    a2 pawn" is checked (the b2 pawn is in the way)
  - No "another move is practically as good" for a checkmate or when a
    mate is on the board (39...Ne5# against 39...e2)
  - Summary with both sides: "11. hxg6 left White with an isolated or
    doubled pawn" (was "left you with")
  - Report: the theory moves carry their move numbers (5...Ne4)

## 0.9.0 — unreleased

Phase 7f: what code can say about every move (after Chessmaster 10's
auto-annotation), no model involved.

- **Opening names** on the move that reaches them, by position (so
  transpositions are named right), from the Lichess opening tables
  (public domain, 3,865 lines, `data/openings.json.gz`), with the ECO code
- **Opening ideas**: one or two general sentences for 60 openings and main
  variations (`data/opening_ideas.toml`), shown the first time an opening
  family appears. They state ideas, never facts about the position
- **Leaving theory**: "This move leaves known opening theory; theory
  continues with 6.Be3 (Sicilian Defense: Dragon Variation) or 6.Be2
  (… Classical Variation)". From your Polyglot book when one is set, most
  played first; otherwise from the named lines
- **A note under every move**, before and after a review: its most telling
  verified effects (a fork, a threat ignored, castling made possible, a
  piece left without a defender, …)
- **Summary**: engine agreement and average cost per move ("You played the
  engine's first choice in 5 of 10 moves (50%); on average a move cost 4.8
  points of winning chance"), mates missed and mates walked into; all
  traced and checked like the other summary lines
- Report: the opening, its idea and where the game left theory
- **Arrows for what each move does**, from the same code facts as the
  notes: red = attacks (the king, a piece worth more than the attacker,
  or an undefended one: 6...h6 on the bishop on g5), green = newly
  defends an attacked piece, dashed orange = a threat left standing, an
  orange ring = a piece left without a defender. A legend under the
  comment names the colours shown; the ↗ button (or A) turns them off
- A pawn's attack always gets an arrow (2.c4 on the pawn on d5)
- The moves pane is smaller by default and a bar under it can be dragged
  to share the column with the comments (remembered); narrower move
  columns, a lower evaluation graph
- The played move is marked from → to: a faint arrow, and stronger
  (amber) start and end squares

## 0.8.0 — unreleased

- Move effects name forks ("It is a fork: the pawn on e5 attacks the
  White knight on d4 and the White bishop on f4 at once": each target is
  the king, worth more than the attacker, or undefended, and the attacker
  is not simply lost) and castling made possible ("It clears the way for
  White to castle kingside"). Chessmaster 10 named that fork; we only
  listed the attacks

Phase 7a: the annotated-game reader (`pgn_reader.py`).

- A PGN file can hold many games: a picker lists them (✎ marks annotated
  ones); `select_game(i)` opens one
- **Annotations pane** beside the move grid: the annotator's comments,
  variations in the margin (and nested ones in brackets), move symbols
  (!, ?!) coloured, position symbols (±, ↑, =∞) with their meaning on
  hover. Click any move, in the game or a variation; the arrow keys follow
  a variation. The annotator's arrows and coloured squares (`%cal`,
  `%csl`) are drawn on the board
- **Two languages in one comment** (ChessBase bilingual annotations,
  English followed by German) are split; Settings → "Comments in
  annotated games": English, German or both. Checked on 25 games: of
  about 1,500 comments, none left with German words in the English part
- **Evaluation graph** above the moves: the annotator's engine (`%evp`)
  until you review the game, then this review's Stockfish evaluation.
  Hover for the move and White's winning chance; click to go there. The
  evaluation bar follows it
- `tests/data/abasov_annotated.pgn`: 25 games annotated by Nijat Abasov,
  used by the tests (and as answer keys in phase 8)

## 0.7.0 — unreleased

Phase 6: the facts on the board.

- **Facts behind this comment**: under every coach comment, the verified
  facts the coach was given, with the opponent's threat at the time of the
  move. While the panel is open the squares they name are circled on the
  board; pointing at one fact circles only its squares
- **Threat arrows**: a dashed orange arrow for what the opponent was
  threatening when the move was played. Shown with "Show the better move"
  (the position before the move), and after the move when the move did not
  deal with it. Each reviewed move stores the threat ({uci, text, ignored})
- Grounding: "What now: … to defend the bishop" after the opponent's reply
  has just captured that bishop (Amr's game, 6...Bxd4 7.Nxd4) is removed;
  the move check had passed it because the move itself was the engine's
- Move letters in sentences become figurines only inside moves ("Black"
  no longer showed a bishop symbol)

## 0.6.0 — unreleased

Phase 5: game summary and patterns, built from stored facts.

- **Themes** (`themes.py`): for each of your errors, code records what it
  got wrong, with the verified fact behind it — the threat it ignored, the
  job the moved piece gave up, a piece or pawn left hanging, king shelter,
  an isolated or doubled pawn, a passed pawn allowed, material, castling
  rights, a better capture or check missed. Taken from the same engine and
  board data as the comment (`line_diff`, Tier-2 concepts), never parsed
  from the model's text
- **Game summary** (`summary.py`), written by code, no model: how many
  errors of each kind, the biggest swing in winning chance, the themes
  that recur ("Three of your errors weakened your king's pawn shelter:
  8...Bg4, 9...dxc4 and 12...Kxf7"), where the errors came (opening,
  middlegame, endgame) and the good moves. Every claim carries its moves
  and facts; `verify()` rebuilds each claim from the stored moves (the
  phase 5 gate)
- **Patterns across games** (`history.py`): each review stores a small
  record in a `history` folder next to the config file; `chesscoach
  patterns [--last N] [--facts]` lists themes that came up in two or more
  of your games, naming the games and moves. With both sides reviewed,
  `player_name` decides which side is you. `keep_history = false` turns
  it off
- Desktop app: the review opens on the game summary, with the patterns
  across your games below it; each claim's moves link into the game and
  "Facts behind this" shows what it rests on
- Report and console: a Summary section with the facts behind each line
- After the first run on Amr's own games (6...Bxd4 and 55.b7 were both
  summarised as "lost material", an outcome rather than a cause):
  - New causes: the opponent's best reply takes the piece or pawn just
    moved ("White's best reply takes the bishop on d4: Nxd4"), and a
    winning position (80%+) let slip to about equal
  - "Material" is now worded as what it is ("ended up with less material
    than the best move would have"), shown only when no cause explains
    it, and never counted as a pattern across games
  - Pawn-structure details are dropped for a move that loses material
  - Patterns: "In your one reviewed game"; singular counts
- `compare_lines` is now built on `line_diff`, a structured list of
  differences (text unchanged)

## 0.5.1 — unreleased

Phase 4: the coach on any position (`coach.py`).

- **Explain this position**: one prompt built from verified facts — the
  verdict and material written by code, key facts (real threats, loose
  pieces, castling rights, equal best moves), the engine's three best
  moves in words, and every static fact — then the grounding check. No
  FEN reaches the model. Finished games get a code-written verdict only
- **Ask the coach**: a tool-calling agent (Ollama tool calling) that
  cannot see the board. Seven tools return verified facts in plain
  English: `position_facts`, `evaluate`, `best_moves`, `threats`,
  `legal_moves`, `play_line`, `compare_moves`, each on the position or
  after a list of moves from it; an illegal move comes back with the
  legal list. At most six tool calls per question. Every position a tool
  looked at is checked against by the grounding check, with one rewrite
  and sentence removal as in the review
- Desktop app: "Explain this position" and an "Ask the coach" box under
  the comment, for any move of the open game; answers are kept per move
  and say they were checked against the board
- Command line: `chesscoach explain --fen ... | --moves ...` and
  `chesscoach ask "question" --fen ... [--show-tools]`
- `chesscoach eval --mode explain` scores position-level answer-key items
  (comparable with `--mode raw`); `--mode ask` scores questions written
  into the keys (`[[key.ask]]`): "Is Black's bishop on c8 safe?", "Which
  rook should White give check with?", "Does Nxc6+ fork the rook on b7?",
  "Why is ...g5 better than ...Bg4?", "Should White take on d4 or f6?"
- Fixed: "1 pawn's worth" (was "1 pawn' worth")
- After the first qwen3 run (explain: 16/18 position facts against 4/24
  for qwen3 alone; ask: 6/21):
  - Moves are resolved like a person writes them: "Re1+" with two rooks
    able to go there returns both candidates in words instead of "not
    legal"; "d4" or "takes d4" lists the captures on d4. The model had
    told the player a legal move was illegal in 6 of 15 answers
  - A false claim in an answer is sent back as a correction with the
    question asked again, not as a request to rewrite (the model replied
    "You are correct. These points have been corrected."); a fresh
    answer wins ties; "However," left dangling by a removal is dropped;
    an answer is never empty
  - `ask --show-tools` prints each model call's time and token counts
- After the second run (ask: 5/21; the model picked the wrong tools,
  e.g. looked up the position after Rae1+ to answer "which rook?"):
  - **Ask now uses a router by default** (`ask_mode = "router"`): code
    reads the question and gathers what that kind of question needs, and
    the model only explains, in one call with no tools.
    - "X or Y?", "Which rook…?", "take on d4 or f6?", "Why is X better
      than Y?": every candidate evaluated, its effects in words, and how
      the worse ones differ from the best a few moves later
    - "Why not X?": X against the engine's first choice
    - "Is the bishop on c8 safe?": attackers and defenders by name
    - "After A B C…, does…?": the line move by move, then what the piece
      attacks and does NOT attack at the end
    - anything else: best moves, threats and all position facts
    - Code writes the part it can decide (which move is better, by how
      much; whether a piece is attacked or defended; "No: … does not
      attack the rook on b7"); grounding checks the rest. The agent stays
      available: `ask_mode = "agent"` or `chesscoach ask --agent`
  - `num_ctx = 4096`. The 181 s question was the model loading (986
    tokens in), not an overflowing context; prompts are under 2,000
    tokens, and a bigger window can push part of the model onto the CPU
  - Ask scores now check against the positions the answer was built from
- After the third run (ask: 18/21, no false claims):
  - Fixed: the grounding check removed TRUE sentences about candidate
    moves ("Bxd4 removes the threat to the knight on c3", "Nxd4 leaves the
    pawn on e5 undefended") because it judged them on the position before
    either move. A sentence naming one candidate is now judged after that
    move; one naming several or none fails only if it fails for every
    candidate. This is why e5-loose scored 0/3
  - New check: "gives up its attack on the rook on f8" fails when that
    piece was never attacked
  - Eval reports which facts were only in the code-written sentence
    ("by code: equal"), so the score shows what the model itself said
- After the fourth run (ask: 18/21; e5-loose still 0/3, never stated):
  - Comparisons get a KEY FACTS section first: for each move, what it
    leaves undefended or attacked ("Nxd4: the knight no longer defends the
    pawn on e5 … NO defenders"). The fact was there before, third in a
    list of effects, and the model skipped it
  - Fixed: the code-written first sentence appeared twice when the
    re-answer restated it
  - Fixed: "does not involve giving up an attack on the rook on f8" was
    removed as a claim that it did
  - Explain: equal best moves are written by code, as in the review
  - Scoring: "White is up a knight; Black is up a bishop" is checked per
    piece, not as net material; explain and ask runs are not checked as
    if the key's move had been played

## 0.4.0 — unreleased

Phase 3: engine-verified concepts and the relevance filter
(`concepts.py`). About one second per commented moment at depth 14.

- Real threats: pass the move to the other side and search. A threat must
  be a capture, check, promotion or mate, and that move must add at least
  5 points of winning chance over the next-best use of the free move, so
  the value of simply moving twice is not reported as a threat (Nxc6 after
  9.Nxd4 is not one; hxg6 before 8...Bg4 is)
- Ignored threats: a threat from before the move that is still the
  opponent's best move afterwards
- Piece roles: take the piece off the board and search; the loss beyond
  its own value, and the opponent's best move without it, is its job.
  Reported for the moved piece, and flagged when the move gave the job up
- Equal moves: within 3 points of winning chance the coach is told the
  moves are practically equal and not to invent a reason
- Key facts: of everything known about a moment, at most five, ranked
  (ignored threats, equal moves, abandoned roles, lost defenders, loose
  pieces the engine's line goes for, the main differences from the
  engine's choice) and put at the top of the prompt
- After the first run on qwen3: a piece role counts only if the piece
  covers the target square or blocks the line, and the opponent would not
  play that move with the piece there (the first version reported the
  bishop on c8 "holding" g6); "the engine's line goes for it" now means a
  capture on that square, not the piece moving away; equal moves are
  written into the comment by code, since the model left the key fact out
- Equal moves work both ways: also when the played move is the engine's
  first choice and the second is practically as good (18.Rhe1+ on some
  Stockfish builds)
- Grounding: a move cannot "protect" the other side's piece (caught:
  ...Bxh5 "would have protected the knight on f3", White's knight)
- `chesscoach eval --runs N`: each key N times, with how often each fact
  was stated and each false claim made. One run at temperature 0.3 is a
  sample: the same key has scored 6, 8 and 8 of 9 with nothing changed
- After five runs per key (25 annotations): the line comparison now marks
  differences that were already there before the move ("already so before
  the move: the player's move did not cause it"); the model read Black's
  doubled c-pawns as created by 12...Kxf7 in 4 of 5 runs. A loose piece of
  the mover's that the engine's line captures is written into the comment
  by code (the model stated it in 2 of 5 runs)
- Grounding: bishop-pair claims ("retains / gives up the bishop pair")
  judged at the end of that move's line; "introduces doubled pawns" counts
  as a "creates" claim; "leaves X undefended" is judged right after the
  move and by the right side's piece; curly apostrophes read correctly.
  Over the 25 texts it now flags exactly the three false claims found by
  reading them, and nothing else
- Grounding: bishop-pair claims checked in every section ("What now":
  "...Bxe5 keeps the bishop pair intact", then Qxg4); the piece a move
  captured spoken of as still in play ("Black's pawn on d4, which now
  ...", after 9.Nxd4)
- Five runs per key on 11 October: 55 of 60 items stated; the hxg6
  threat, the loose c4 pawn and the equal rook checks 5 of 5 each; the
  doubled-pawn reversal no longer reaches the text
- Grounding: "preserves / keeps the pawn on e5" is checked right after
  that move (caught: exf6 "preserves White's pawn on e5")
- The review report lists the key facts under each comment
- Settings: `concepts = true`, `concept_depth = 14`
- Answer keys: new items for the hxg6 threat (8...Bg4) and the loose
  c4 pawn (9...dxc4); the equal rook checks (18.Rhe1+) are now covered.
  Facts recall 14/14

## 0.3.0 — unreleased

Phase 2: the grounding check. "Don't calculate" is now enforced by code.

- `grounding.py` finds concrete claims in the model's text and verifies
  each on the board: pieces on squares, attacks (direct or through one
  piece), defences, "no longer defends", undefended pieces, pins (to the
  king or to a more valuable piece), forks, material ("material is
  equal", "White is up a piece") and legal-move counts
- A claim passes if it is true before or after the move or anywhere along
  the engine's lines; unrecognised claims pass
- Narration: false claims are sent back with the correction ("the king
  on f7 still defends the pawn on e7") for one rewrite; a claim that is
  still false is removed with its sentence. The code-written first
  sentence is never touched
- The app notes under a comment when a sentence was left out
- `grounding = false` in the config, or `chesscoach eval --no-grounding`,
  turns it off to measure what it catches
- `eval` scores every text with the grounding check (board errors), shows
  sentences removed, and checks free explanations against the positions
  along all engine lines
- Claims about what a move leads to are checked on that move's own line:
  "creates a passed pawn" and "removes the threat to the knight on c3"
  under "Better was" are checked after the engine's choice, elsewhere
  after the played move. Catches the passed-pawn reversal on 12...Kxf7
  and a fact moved from 9.Nxd4 to 9.exf6
- Pawn-structure consequences: passed, doubled and isolated pawns a move
  "creates", "allows" or "avoids", checked on that move's line; a
  "creates" claim about pawns that were already there fails too (caught:
  12...Kxf7 "creating doubled pawns on the c-file")
- "What now" must follow the engine's line: a described capture or move
  that is not among the engine's next moves fails
- Every annotation is put back into its fixed shape after the model, the
  rewrite and the check: the four headings in order, the label from code,
  the code-written first sentence, and text that lost its heading kept
  under "What happened". If "Better was" is removed on a bad move, a
  code-written sentence names the engine's move instead
- `eval` reports false claims caught in the first draft ("Caught"),
  whether the rewrite fixed them or the sentence was removed
- Fixed: "Taking on f6" was read as "king on f6"
- The 5 October qwen3 runs are kept as test data (four eval runs and a
  full review of the Fritz game, 27 annotations): the check flags exactly
  the false claims found by reading them, and nothing else

## 0.2.0 — unreleased

Phase 1 of the revised design: close the facts gaps the model comparison
exposed, and measure everything against answer keys.

### Facts layer (`facts.py`)
- Material named piece by piece ("White is up a bishop and a knight"),
  replacing the centipawn balance in prompts
- Castling rights for both sides, and whether castling is legal this move
- Undefended pieces, attacked or not (the loose bishop on c8)
- Legal-move count, with the list on request
- `play_line()`: plays a line move by move, describes each move, tracks a
  pawn that changes file on a capture, and stops at an illegal move with
  the legal ones listed
- `position_facts()`: all Tier 1 facts for a position in one block
- A move made out of check is no longer described as "ignoring a threat"
- Prompts for positions set up from a FEN now list the game so far
  correctly

### Answer-key harness (`chesscoach eval`)
- Keys in `answer_keys/*.toml`: the 18.Rhe1+ comparison position, three
  moments from Homer vs Fritz, and 9.Nxd4 from the test game
- Scores facts-layer recall (no model), model recall and false claims in
  two modes: `pipeline` (our annotation) and `raw` (the model given only
  the FEN and engine lines); `--text` scores a pasted reply
- Clean engine hash per position, so results don't depend on run order
- Keys skip themselves when the engine prefers another move or labels the
  move differently (12...Kxf7 sits on the Good/Inaccuracy boundary)
- Runs in CI with `--strict`

## 0.1.0 — unreleased

### Desktop app (`chesscoach gui`)
- Review tab: open a PGN, background review with progress and Stop, move
  list with figurines and Informant marks, critical-moment shortcuts, the
  coach's comment per move, "Show the better move" arrows, evaluation bar
- Play tab: training games against Maia at a chosen rating (falls back to
  Stockfish's strength limit), take back, resign, save PGN, review the game
- Settings dialog writing the user config file; light and dark themes
- `review_game()` now returns structured data; the CLI report is built on it
- Maia predictions use temperature 0 (most likely move, was sampled) and
  the player's rating (`player_elo`)

### Core

First packaged version, restructured from the `test_llm_coaching.py`
prototype (v10.1) with no change in behaviour.

- Game review from PGN: critical moments only (or every move)
- Labels from drop in winning chances; verdict wording from fixed bands
- Verified move facts: captures, lost defenders, ignored threats,
  counter-attacks; comparison with the engine's line sorted by which
  move each difference favours
- Opening-book skipping (Polyglot)
- Maia used to tell hard-to-find moves from natural ones
- Output check (wrong label, banned overstatement, invented pieces,
  move notation) with one automatic retry
- Settings via config file, environment variables or flags
