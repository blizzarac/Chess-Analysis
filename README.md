# Chess.com Improvement Report

A self-hosted website that takes a chess.com username, downloads the player's complete game
archive, runs the games through Stockfish and turns the result into a concrete improvement plan.

It answers the questions a coach would ask after looking at your games:

- **Where do the points go?** Results by time class, colour, opponent strength, hour of day,
  session length and reaction to losses (tilt).
- **How accurate are you, and when?** Engine-graded moves (best / excellent / good / inaccuracy /
  mistake / blunder), accuracy and centipawn loss per phase, per colour, per time class, per move
  number and over time. Conversion of winning positions and saves of lost ones.
- **What do you play?** Every opening with score, accuracy, the engine's verdict after ten
  moves and the move where you typically first go wrong, a repertoire map of your first five
  moves, and where you leave a compact book of standard lines (and how those games went).
- **Why were the bad moves bad?** Each mistake is checked on the board: hung a piece, missed a
  mate, walked into a fork, threw away a win, rushed, in time trouble, missed the opponent's blunder.
- **Where does the clock go?** Time per phase, time-trouble error rate, move quality by thinking
  time, timeouts.
- **Endgames:** which types you reach, how often you convert winning ones and hold level ones.
- **Insights & training plan:** ranked findings with a recommendation each, and a four-point plan
  with drills.
- **Puzzles from your own games:** positions where you missed a clearly better move, verified
  with a second three-line engine pass so that equally good alternatives are accepted and vague
  positions are dropped. Solved and failed positions are scheduled with spaced repetition.
- **Progress:** each report is snapshotted; the next one shows what moved (score, accuracy,
  blunders, conversion, ratings).
- **Game viewer:** board, evaluation bar and graph, move list with classifications, the better move
  drawn as an arrow, critical moments.

## Requirements

- Python 3.10+
- [Stockfish](https://stockfishchess.org/download/) (any recent version). On Debian/Ubuntu:
  `sudo apt install stockfish`; on macOS: `brew install stockfish`.
  Without Stockfish the site still works, but only the statistics that don't need an engine
  (results, openings, clock, habits) are produced.

## Run

```bash
pip install -r requirements.txt
python -m chess_analysis            # http://127.0.0.1:8000
```

Environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `STOCKFISH_PATH` | auto-detected | Path to the Stockfish binary |
| `ENGINE_WORKERS` | CPU count − 1 | Parallel engine processes |
| `ENGINE_HASH_MB` | 64 | Hash table per engine process |
| `CHESS_DATA_DIR` | `data/` | Where the SQLite cache lives |
| `PORT` / `HOST` | 8000 / 127.0.0.1 | Server bind address |
| `CHESSCOM_USER_AGENT` | project string | chess.com asks for an identifying User-Agent |
| `CHESSCOM_MOCK_DIR` | unset | Serve chess.com responses from JSON files (offline demo / tests) |

Enter a username, optionally open **Options** (engine depth, how many recent games get engine
analysis, which time classes, how many months of history) and press **Analyze**. Downloads and
engine results are cached in SQLite, so re-running only fetches new months and analyses new games.

Analysis cost: at depth 14 a position takes roughly 50–100 ms on one core, so 100 games take a
few minutes on a laptop. "Quick" (depth 10) is fine for a first look; "Deep" (depth 18) is for a
smaller set of games.

## Offline demo

```bash
CHESSCOM_MOCK_DIR=tests/fixtures/mock python -m chess_analysis
```

then analyse the user `testplayer`. The fixture set is 70 generated games with clocks, openings and
plenty of blunders. Regenerate it with `python tests/make_fixtures.py` (needs Stockfish).

## Tests

```bash
python -m pytest -q
```

## How the numbers are computed

- **Win probability** uses the Lichess curve `50 + 50 * (2 / (1 + exp(-0.00368208 * cp)) - 1)`,
  with evaluations clamped to ±10 pawns. **Move accuracy** is
  `103.1668 * exp(-0.04354 * (winBefore - winAfter)) - 3.1669`; **game accuracy** is the mean of
  the arithmetic and harmonic means of the move accuracies.
- **Classification** is by win probability lost: best (engine's move or ≤0.5%), excellent (<2%),
  good (<5%), inaccuracy (<10%), mistake (<20%), blunder (≥20%).
- **Phases:** the opening ends once at most two minor pieces are still on their home squares
  (never before move 6, never after move 15); the endgame starts when six or fewer pieces other
  than kings and pawns remain; the middlegame is in between.
- **Premoves:** moves played in under 0.3 seconds are counted as premoves and excluded from the
  "rushed" tag and the thinking-time statistics.
- **Opening book:** about 200 named lines (`chess_analysis/analysis/book.py`). chess.com's own
  label is used when present; the book supplies the name otherwise and detects the first
  departure from theory and who made it.
- **Time trouble:** under 10% of the starting clock (5% with an increment ≥5s), at least 5 seconds.
- **Tactical tags** replay the engine's principal variation on the board to see whether material
  is lost, a mate is missed or allowed, or the opponent's reply forks two pieces.
- **Tilt:** games starting (per the PGN start time) within 20 minutes of a loss versus all others.

Every insight requires a minimum sample (usually 8+ games or 60+ moves) before it is shown.

## Layout

```
chess_analysis/
  chesscom.py        chess.com public API client (serial requests, retries, mock mode)
  db.py              SQLite cache for games, engine results and reports
  pgn_parse.py       PGN + clock parsing into a flat game record
  engine.py          Stockfish worker pool
  jobs.py            download → analyse → report pipeline with progress
  main.py            FastAPI app and JSON API
  analysis/
    eval_utils.py    win%, accuracy, classification
    game_analysis.py per-game annotation: classes, phases, clocks, tactical tags
    report.py        aggregation into the report sections
    insights.py      findings and the training plan
    book.py          compact opening book (names, ECO, first deviation)
static/              single-page frontend (no build step, no framework)
tests/               pytest suite and the offline fixture generator
```

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/analyze` `{username, options}` | Start (or join) a job; returns the job |
| `GET /api/jobs/{id}` | Job status and progress |
| `POST /api/jobs/{id}/cancel` | Cancel a running job |
| `GET /api/report/{username}` | Latest report |
| `GET /api/players/{username}/games` | Paginated, filterable game list (`offset`, `limit`, `time_class`, `result`, `color`, `analyzed`, `q`) |
| `GET /api/players/{username}/history` | Snapshots of previous reports |
| `GET /api/games/{username}/{game_id}` | Full annotation for the game viewer |
| `GET /api/players` | Players with cached reports |
| `GET /api/status` | Engine availability and defaults |
