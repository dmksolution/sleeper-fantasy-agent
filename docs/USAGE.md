# Sleeper Fantasy Agent — complete usage guide

A read-only advisory toolkit for a Sleeper fantasy football league. It caches
Sleeper's data locally, re-scores every projection against *your* league's
actual scoring settings, and answers the questions that decide a season: who to
draft, who to start, who to claim, what to bid, and whether a trade is worth
taking.

It is an **advisor, not an autopilot**. The Sleeper API is read-only — there is
no write endpoint and no token, because there is nothing to authorize. The tool
tells you what to do; you tap the button in the app. Anything claiming full
Sleeper automation is scraping private mobile endpoints.

**Contents**

1. [Quick start](#1-quick-start)
2. [How it works](#2-how-it-works)
3. [Configuration](#3-configuration)
4. [Command reference](#4-command-reference)
5. [Using it from Claude (MCP)](#5-using-it-from-claude-mcp)
6. [Draft night playbook](#6-draft-night-playbook)
7. [The weekly in-season loop](#7-the-weekly-in-season-loop)
8. [Scheduling and notifications](#8-scheduling-and-notifications)
9. [How the analysis actually works](#9-how-the-analysis-actually-works)
10. [Known data quirks](#10-known-data-quirks)
11. [Testing and health checks](#11-testing-and-health-checks)
12. [Troubleshooting](#12-troubleshooting)
13. [Architecture](#13-architecture)
14. [What is deliberately not built](#14-what-is-deliberately-not-built)

---

## 1. Quick start

```bash
pip install -r requirements.txt
cp .env.example .env

# Find your league IDs and paste them into .env
python cli.py setup --username <your_sleeper_username>

# Pull the full season into the local cache (~35s, all 18 weeks)
python cli.py sync

# Confirm it read your league correctly and the numbers are trustworthy
python cli.py info
python cli.py health
```

Then, depending on where you are in the season:

```bash
python cli.py draft --plan          # before the draft: study every slot
python cli.py draft --dissent       # before the draft: model vs market
python cli.py draft --watch         # during the draft
python cli.py digest                # during the season: the full weekly brief
```

**Requirements:** Python 3.12+. Three dependencies (`requests`, `truststore`,
`mcp`). Everything else is standard library, including the tests.

---

## 2. How it works

```
Sleeper API  ──sync──>  SQLite cache  ──>  analysis modules  ──>  CLI
(read-only)             (data/sleeper.db)                     └─>  MCP server
                                                              └─>  scheduled jobs → phone
```

Every analysis command reads from the local SQLite cache, never the network. That
makes everything fast, keeps you inside Sleeper's rate limits, and means the
tool still works when Sleeper is slow — which it will be at 8:05pm on draft
night. `sync` is the only command that talks to the network in normal operation.

Three ideas do most of the work:

**Your scoring, not generic PPR.** Projections arrive as raw stat lines
(`rec: 5.2, rush_yd: 41.3, ...`). Those get dot-producted against your league's
own `scoring_settings`, so a 6-point passing TD league, a TE premium, first-down
bonuses, or IDP scoring all fall out of the same calculation with nothing
hardcoded.

**Exact lineup optimization.** Setting a lineup is an assignment problem, and
greedy "best QB, then best RB" filling is wrong whenever flex slots overlap. The
Hungarian algorithm solves it exactly. With two FLEX slots this changes real
answers.

**Value measured as lineup impact.** A waiver claim is not ranked by talent, it
is ranked by how much it raises your *starting lineup*. A brilliant handcuff
behind your own starter correctly scores near zero.

---

## 3. Configuration

All configuration is environment variables, read from `.env` in the project
root. Real environment variables win over `.env` values.

| Variable | Default | What it does |
|---|---|---|
| `SLEEPER_USERNAME` | — | Identifies which roster in the league is yours |
| `SLEEPER_LEAGUE_ID` | *required* | The league to analyze |
| `SLEEPER_DRAFT_ID` | auto | Optional; found automatically from the league |
| `SLEEPER_SEASON` | current | Optional; blank means whatever Sleeper says is current |
| `DB_PATH` | `./data/sleeper.db` | SQLite location |
| `PLAYER_CACHE_HOURS` | `24` | Sleeper asks that the 15 MB player index be pulled at most daily |
| `PROJECTION_CACHE_HOURS` | `6` | Projections move through the week |
| `LEAGUE_CACHE_MINUTES` | `15` | League, rosters, matchups |
| `ROS_HORIZON_WEEKS` | `6` | How far "rest of season" looks ahead |
| `REGULAR_SEASON_WEEKS` | `17` | Fantasy regular season length |
| `NFL_WEEKS` | `18` | How many weeks of projections to cache |
| `DIGEST_WEBHOOK_URL` | — | Any endpoint accepting `{"title":…, "text":…}` |
| `NTFY_TOPIC` | — | ntfy.sh topic your phone subscribes to |
| `NTFY_SERVER` | `https://ntfy.sh` | Self-host if you prefer |
| `MCP_TRANSPORT` | `stdio` | `http` for the remote container profile |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8848` | HTTP transport binding |

**No API keys exist**, for anything. The Sleeper API is unauthenticated.

> **ntfy note:** anyone who knows your topic name can read your notifications.
> Use a random string, not `dan-fantasy`.

---

## 4. Command reference

Global flag: `--league <id>` overrides `SLEEPER_LEAGUE_ID` for one invocation.

### Setup and data

#### `setup --username <name> [--season YYYY]`
Lists every Sleeper league for that username with the IDs to paste into `.env`.
The only command that works before `.env` is configured.

#### `sync [--full] [--weeks N] [--season-wide]`
Refreshes the local cache. **Pulls all 18 weeks by default** during the
preseason or whenever fewer than 14 weeks are cached.

- `--full` — ignore TTLs and refetch everything
- `--weeks N` — only pull N weeks ahead, for a quick in-season refresh
- `--season-wide` — force all 18 weeks

Full season sync takes ~35 seconds. Caching every week is what makes bye weeks,
playoff-week value, and full-horizon trade math work at all.

#### `info`
League settings summary: teams, scoring format, starting slots, bench size,
waiver type, FAAB budget, playoff week.

#### `health`
**Run this when a number looks surprising.** Reports which projection weeks are
cached, a per-position scoring audit against Sleeper's own numbers, row counts,
data age, and explicit warnings.

#### `selftest`
Runs the full test suite (46 tests, ~30s), including a 300-case brute-force
validation of the lineup solver.

### Draft

#### `board [--position POS] [--top N] [--json]`
The pre-draft cheat sheet, sorted by value over replacement. Replacement level
is derived from your league's own roster slots × team count, not a generic
assumption. Includes tiers (detected by gap analysis), ADP, bye week, and
projected fantasy-playoff points.

```bash
python cli.py board --top 50
python cli.py board --position RB --top 30
```

#### `draft` — the main draft tool
By default this **simulates the rest of the draft** and ranks the picks you
could make now by how the finished roster tends to score.

```bash
python cli.py draft --slot 7            # simulated recommendation
python cli.py draft --plan              # all 12 slots, before the draft
python cli.py draft --dissent           # model vs market disagreements
python cli.py draft --watch --slot 7    # live, polls and reprints
python cli.py draft --precompute        # warm everything the night before
python cli.py draft --fast              # instant heuristic, no simulation
```

| Flag | Default | Meaning |
|---|---|---|
| `--slot N` | inferred | Your draft slot, if the order is not published yet |
| `--top N` | `8` | How many candidates to rank |
| `--trials N` | `200` | Rollouts per candidate (more = steadier, slower) |
| `--plan` | | Expected outcome and typical opening from every slot |
| `--dissent` | | Where our projections most disagree with ADP |
| `--watch` | | Poll the live draft, reprint on every pick |
| `--precompute` | | Warm the board and caches before draft night |
| `--fast` | | Skip the simulation, use the instant heuristic |
| `--interval S` | `5.0` | `--watch` poll seconds |
| `--draft-id ID` | auto | Override the draft |
| `--json` | | Machine-readable output |

Reading the output:

```
Player                          Pos      ADP    Score  Regret  Lasts  Plan
Christian McCaffrey (RB-SF)     RB       5.0   2617.5     0.0   0.00  RB-RB-WR-TE
Puka Nacua (WR-LAR)             WR       4.9   2608.0     9.5   0.00  WR-RB-RB-TE
```

- **Score** — expected weighted roster points over the whole season if you take
  him and the draft plays out normally
- **Regret** — points behind the best option. Small regret means it barely matters
- **Lasts** — probability he survives to your *next* pick
- **Plan** — the position sequence that typically follows

**The decision rule:** compare Regret against Lasts. A close second choice who
is very likely to last is the one to pass on. Two players 5 points apart where
one has `Lasts 0.05` and the other `Lasts 0.90`? Take the first.

#### `recap [--draft-id ID]`
After the draft: value captured per draft slot, and every pick's reach or steal
against ADP.

### Weekly

#### `lineup [--week N] [--json]`
Your optimal starting lineup, with injury and bye adjustments applied, plus the
bench sorted by projection.

#### `startsit [--week N] [--notify] [--threshold PTS]`
Compares the lineup you actually have set in Sleeper against the optimal one.
Reports `points_left_on_bench`, who to start, and who to sit with reasons.

- `--notify` — push to your phone, but only if the swap is worth it
- `--threshold` (default `1.5`) — minimum bench points before it interrupts you

#### `waivers [--week N] [--top N] [--json]`
Ranks free agents by **how much they would improve your starting lineup**, not
by talent. Includes a suggested FAAB bid as a percentage of your *remaining*
budget.

#### `matchup [--week N]`
This week's head-to-head: projected totals, win probability, slot-by-slot
positional edges, and a strategic read.

#### `standings`
W/L/T, points for and against, waiver budget used.

#### `byes [--through N]`
Weeks where you have three or more players unavailable. Plan claims one to two
weeks ahead of these.

#### `digest [--week N] [--notify] [--json]`
**The one command that matters most.** Syncs, then produces a full markdown
brief: matchup, optimal lineup, waiver targets with bids, league activity since
last time, trade angles, bye planning, and standings.

### Trades and players

#### `trade --send "A,B" --receive "C" [--partner ROSTER_ID] [--week N]`
Evaluates a trade by re-optimizing your lineup week by week, before and after,
across the horizon. With `--partner` it also computes the other side, so you can
tell whether they would plausibly accept.

```bash
python cli.py trade --send "Puka Nacua" --receive "Bijan Robinson" --partner 4
```

#### `targets [--week N] [--top N]`
Finds other teams' bench surplus that matches your weakest starting slots.

#### `player <name> [--week N]`
Single player outlook: this week's projection, rest-of-season total, and
scoring consistency once actuals exist.

---

## 5. Using it from Claude (MCP)

The MCP server exposes all 22 tools to Claude Desktop or Claude Code, which is
the most natural way to use this — you ask in English and Claude picks the tools
and explains the answer.

```json
{
  "mcpServers": {
    "sleeper-fantasy": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/sleeper-fantasy-agent/mcp_server.py"],
      "env": {
        "SLEEPER_USERNAME": "your_sleeper_username",
        "SLEEPER_LEAGUE_ID": "your_league_id",
        "DB_PATH": "/absolute/path/to/sleeper-fantasy-agent/data/sleeper.db"
      }
    }
  }
}
```

On Windows use `\\` or forward slashes, and point `command` at
`.venv\Scripts\python.exe`.

**The 22 tools**

| Tool | What it answers |
|---|---|
| `sync_data` | Refresh the cache |
| `data_health` | Can these numbers be trusted right now? |
| `find_my_leagues` | Which leagues am I in? |
| `league_info` | League settings |
| `draft_board` | The value board |
| `draft_simulate` | **Who should I take, by simulating the draft out** |
| `draft_recommendation` | Fast heuristic pick suggestion |
| `draft_plan` | What does each draft slot tend to produce? |
| `draft_disagreements` | Where do we disagree with the market? |
| `draft_results` | Post-draft recap |
| `optimal_lineup` | Best lineup this week |
| `start_sit` | Swaps against my current lineup |
| `waiver_targets` | Best claims and what to bid |
| `matchup` | This week's head-to-head |
| `league_standings` | Standings |
| `recent_activity` | What changed league-wide |
| `bye_weeks` | Bye trouble spots |
| `evaluate_trade` | Is this trade good? |
| `trade_targets` | Who should I trade with? |
| `player_outlook` | One player's outlook |
| `trending_players` | League-wide adds and drops |
| `weekly_brief` | The full digest as markdown |

Things worth asking:

- *"Who should I take with my next pick?"*
- *"I'm picking 7th. What does my draft usually look like from there?"*
- *"Where do your projections disagree with ADP most? Which of those should I actually believe?"*
- *"Who should I start at flex this week and why?"*
- *"Best waiver adds, and what should I bid out of my remaining FAAB?"*
- *"Is trading Puka Nacua for Bijan Robinson good for me? Would they accept?"*
- *"Can I trust these numbers right now?"* → `data_health`

---

## 6. Draft night playbook

**A week before**

```bash
python cli.py sync
python cli.py draft --plan
```

Study the `typical_opening` column. It tells you the position sequence that
tends to come back to you from each slot, so you arrive with a shape in mind
rather than improvising.

**The night before**

```bash
python cli.py draft --dissent --top 15
python cli.py draft --precompute
```

`--dissent` is the highest value-per-hour thing in the whole toolkit. It lists
the players where the projections and the market most disagree. Sleeper's
projections fail in predictable, human-checkable ways — a rookie with no
history, a WR2 who just inherited the WR1 role, a backfield committee that
resolved in August. Ten minutes of news reading against that list turns each one
into either a value pick or a trap avoided.

`--precompute` warms the board and the projection cache so nothing is computed
while a clock is running.

**During the draft**

```bash
python cli.py draft --watch --slot 7
```

Keep this in a terminal. It polls every five seconds, reprints when a pick
lands, and alarms when you are two picks away. Keeping one process alive matters:
the board is built once, so every refresh after the first is instant.

If the commissioner never set the draft order, pass `--slot`. Once you have made
one pick the slot is inferred automatically and `--slot` is no longer needed.

**After**

```bash
python cli.py recap
```

---

## 7. The weekly in-season loop

| When | Command | Why then |
|---|---|---|
| Tuesday morning | `digest --notify` | Before waiver claims are due — the one that matters most |
| Wednesday morning | `digest --notify` | Did the claims land, what changed league-wide |
| Sunday 11am | `digest --notify` | 90 minutes before the 1pm ET lock |
| Sunday noon & 1pm | `startsit --notify` | Late scratches and inactives |
| Monday morning | `sync --full` | Post-game reset |

All of this is registered automatically by the scheduler script below.

---

## 8. Scheduling and notifications

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File scripts\schedule_tasks.ps1
```

Registers seven Scheduled Tasks under `\SleeperFantasyAgent\` matching the
schedule above. Output goes to `logs/`.

```powershell
Get-ScheduledTask -TaskPath '\SleeperFantasyAgent\'                        # verify
Start-ScheduledTask -TaskPath '\SleeperFantasyAgent\' -TaskName 'DigestTuesday'  # test now
.\scripts\schedule_tasks.ps1 -Unregister                                   # remove
.\scripts\schedule_tasks.ps1 -NoNotify                                     # log only
```

> If the project lives on a **mapped network drive**, the script handles it. Mapped
> drives do not exist in the session a Scheduled Task runs in, so it resolves the
> drive to its UNC root and uses `pushd`. Without that, tasks fail instantly with
> result `1` and write no log at all.

### Docker (Linux)

```bash
docker compose up -d --build
```

Runs the same schedule via cron (`scripts/crontab`). An optional MCP service is
available behind the `remote` profile on `127.0.0.1:8848` — **unauthenticated**,
so do not expose it.

### Notifications

Set `NTFY_TOPIC` in `.env` and subscribe in the ntfy app or at
`https://ntfy.sh/<topic>`. Test it:

```bash
python -c "from sleeper_agent.digest import notify; print(notify('test'))"
```

`{'ntfy': 200}` means delivered. A generic JSON webhook works too via
`DIGEST_WEBHOOK_URL`.

---

## 9. How the analysis actually works

### Custom scoring
Every projection is a raw stat line dot-producted against your league's
`scoring_settings`. Keys that are not scoring categories (`adp_ppr`, `gp`,
`pos_rank_ppr`) simply have no weight and contribute zero. Nothing is hardcoded,
so TE premium, first-down bonuses, and IDP all work automatically.

### Season value
Season-long value is the **sum of real weekly projections**, never Sleeper's
season aggregate. Only weeks where the player's team actually has a game count,
so byes fall out for free rather than being modelled. See
[known data quirks](#10-known-data-quirks) for why the aggregate is unusable.

### Replacement level, two kinds
Value over replacement needs a definition of "replacement", and there are two
legitimate ones. The tool makes callers choose:

- **startable** — what a league-average *starter* at this position scores,
  derived from your roster slots × team count. In a 12-team league with
  RB/RB/FLEX/FLEX that lands around RB35. Used for the draft board and trades.
- **waiver** — what you could pick up free right now, the Nth best unrostered
  player. Used for waiver claims and drop candidates.

### Lineup optimization
An exact Hungarian assignment over slots × players, with illegal pairings
forbidden. Injury adjustments: OUT/IR/PUP/SUS → 0, DOUBTFUL → ×0.35,
QUESTIONABLE → ×0.88, bye → 0.

### Waiver ranking
For each candidate, the optimizer is re-run with that player added and the
difference in starting lineup points is measured. That is the ranking signal.
FAAB is sized on lineup gain, adjusted for league-wide trending adds and
playoff urgency, and capped at 60% of your remaining budget.

### Trade evaluation
Both rosters are re-optimized week by week across the horizon, before and after.
The verdict is points per week *actually evaluated* — if some weeks are not
cached, it says so rather than diluting the result.

### Draft simulation
For each candidate: force that pick, play all 15 rounds out with opponents
drafting to a noisy ADP, then score the finished roster by the starting lineups
it would produce across the season, with weeks 15–17 weighted 1.5×. Repeat a few
hundred times and compare means.

This *subsumes* three things older tools bolt on as separate tuned bonuses:
positional need (a second QB improves no lineup), tier scarcity (the rollout
sees the drop), and survival (opponents actually take players).

Simulated managers obey real constraints — position caps, no kicker or defense
before round 13, no backup QB before round 10 — because without them the
simulation produces absurd rosters and every ranking built on it is noise.

### Survival probability
`P(he lasts until my next pick)` from a truncated normal on ADP. Sleeper
publishes no ADP standard deviation, so it is assumed as `max(2.0, 0.20 × ADP)`
and then **calibrated against the live draft**: after 15 picks the observed
median deviation from ADP is compared to the assumed one and the spread is
rescaled. A league of ADP followers and a league of homers stop getting
identical advice.

---

## 10. Known data quirks

These are real properties of Sleeper's data, found by measurement. The tool
works around all of them; they are documented so nobody "fixes" the workarounds.

### The week-0 season aggregate is unusable for K and DEF
Scoring Sleeper's season aggregate against real league settings, measured across
every cached row:

| Position | Mean error vs Sleeper's own `pts_ppr` |
|---|---|
| QB / RB / WR / TE | ~0.00 (exact) |
| **K** | **−22.61** (Brandon Aubrey: 76.0 vs 116.0) |
| **DEF** | **+10.00** (all 32 teams) |

Two structural causes:
- The kicker aggregate emits `fgm_50p`, which is not a scoring category in a
  league that pays by distance bucket (`fgm_50_59`, `fgm_60p`), and omits the
  0–19/20–29/30–39 made-FG buckets entirely. Some kickers are worse still: Trey
  Smack's aggregate carries *only* an `fgm_40_49` bucket.
- The defense aggregate carries `pts_allow_0: 1.0`. In a weekly line that is a
  bucket *flag* meaning "allowed zero points this week" and multiplying by 10 is
  correct. In an aggregate it means nothing, so every defense collects a phantom
  shutout — and the real 17 games of points-allowed scoring is absent.

Weekly lines have neither problem and score exactly. **The rule the code
enforces: week 0 is an ADP carrier, not a scoring source.**

### Sleeper's `pts_ppr` scores interceptions at +2
Their QB numbers run exactly `3.0 × pass_int` above a correct dot product
against this league's settings (which score `pass_int` at −1). Verified by least
squares over 442 QB week-rows: R² = 0.99934 on `pass_int` alone, coefficient
3.025, with every other candidate stat falling out at zero.

`League.score()` is the correct one. The practical consequence: **any tool that
ranks quarterbacks on Sleeper's `pts_ppr` overvalues turnover-prone passers.**
This one does not. There is a test pinning the bias so nobody "corrects"
`score()` to match.

### Defenses get no row at all on a bye
Offensive players get a projection row with no opponent; defenses get nothing.
Per-player bye detection therefore reported every defense as never having a bye.
Bye weeks are derived at the **team** level instead, from who actually plays each
week. All 32 teams resolve correctly.

### The league's `draft_rounds` disagrees with the draft
League settings report `draft_rounds: 3`; the draft object reports `rounds: 15`.
The draft object is authoritative. The tool uses it and warns loudly on any
mismatch, because silently truncating to 3 rounds would break every rollout.

### Historical projections are not available
`/projections/nfl/{past_season}/{week}` returns HTTP 400 for every prior season.
Weekly **actuals** are available (~2,100 players/week), so historical variance
can be measured, but projection accuracy cannot be backtested against past
seasons.

---

## 11. Testing and health checks

```bash
python cli.py selftest                       # or:
python -m unittest discover -s tests -v
```

46 tests, standard library only:

- **`test_hungarian.py`** — 300 randomized rosters checked against exhaustive
  brute force, plus the two-flex case that defeats greedy filling
- **`test_scoring.py`** — every cached projection re-scored against Sleeper's own
  number as an oracle, with the QB interception bias asserted rather than
  loosely tolerated
- **`test_valuation.py`** — season value equals the sum of the weekly numbers the
  optimizer uses; K/DEF regressions; every team has exactly one bye
- **`test_draft_sim.py`** — 25 replayed drafts asserting every finished roster is
  legal and looks like football, plus survival-model and snake-order math

`python cli.py health` is the runtime counterpart: week coverage, per-position
scoring audit, row counts, staleness, and explicit warnings.

---

## 12. Troubleshooting

**`SLEEPER_LEAGUE_ID is not set`** — run `python cli.py setup --username <you>`
and paste the values into `.env`.

**`Could not find your roster in this league`** — `SLEEPER_USERNAME` is missing
or does not match. Check `python cli.py setup`.

**Rest-of-season totals look low / byes are missing** — the cache is
incomplete. Run `python cli.py sync` and check `python cli.py health` reports
`coverage_complete: true`.

**`CERTIFICATE_VERIFY_FAILED`** — antivirus or a corporate proxy is intercepting
HTTPS. `truststore` is already a dependency and injects the OS certificate store
at import; make sure it installed.

**Scheduled tasks fail with result 1 and no log** — the project is on a mapped
network drive. Re-run `scripts\schedule_tasks.ps1`, which resolves the drive to
UNC. Verify with `Get-ScheduledTaskInfo`.

**First command in a new process takes several seconds** — cold SQLite page
reads, and noticeably worse over a network share. Subsequent calls in the same
process are ~100× faster, which is why `draft --watch` keeps one process alive
and `draft --precompute` exists.

**The digest says "no DIGEST_WEBHOOK_URL or NTFY_TOPIC configured"** — set
`NTFY_TOPIC` in `.env`.

**Draft advice offers players who will never reach me** — pass `--slot`. Without
a known slot the tool cannot tell which picks are yours.

---

## 13. Architecture

```
cli.py                 17 subcommands
mcp_server.py          22 MCP tools over stdio or streamable-http
sleeper_agent/
  client.py            Sleeper API: rate limited, retries, 404→None
  config.py            env-driven Settings
  store.py             SQLite, TTL cache, shared read connection
  sync.py              network → SQLite; data_health()
  league.py            League context + the scoring dot product
  projections.py       weekly projections, memoized; VOR
  valuation.py         season value from summed weeks; replacement levels; byes
  lineup.py            Hungarian solver; optimize_points() primitive
  draft.py             value board, tiers, ADP blending, disagreements
  draft_sim.py         Monte Carlo rollout, --watch, --precompute, --plan
  survival.py          P(available at my next pick), live calibrated
  waivers.py           lineup-delta ranking, FAAB sizing, drop candidates
  trades.py            re-optimization based trade evaluation
  matchup.py           win probability, standings, byes
  digest.py            the weekly brief + notifications
sql/schema.sql         8 tables
tests/                 46 stdlib unittest tests
scripts/               crontab, entrypoint.sh, schedule_tasks.ps1
```

**Performance notes.** `week_projections()` and the player table are memoized
per process; `optimize()` is ~0.1 ms warm and the bare assignment solve is 46 µs,
which is what makes a few thousand draft rollouts feasible. `sync_all()` clears
every cache when new rows land.

---

## 14. What is deliberately not built

Being explicit about the boundaries, because each of these was considered and
rejected for a reason:

- **Automated roster moves.** Impossible. The Sleeper API is read-only.
- **`nfl_data_py` / nflverse.** Would drag pandas and pyarrow (~300 MB) into a
  standard-library project. Snap share and target share are inputs to a
  projection model you should not build — Sleeper already incorporates them.
- **A backtest harness for the tuned constants.** Historical projections return
  HTTP 400, so there is no counterfactual to fit against. The draft simulator
  *deletes* tuned constants instead of fitting them, which is strictly better.
- **Multi-source projection blending.** No free second source exists. ADP is the
  second opinion, and the blend already uses it.
- **A separate strength-of-schedule layer.** Weekly projections are already
  matchup-aware; a separate layer would double-count.
- **Keeper and dynasty valuation.** `max_keepers: 1` with no previous league
  means there is no keeper decision this season.
- **A web dashboard.** The MCP server is the interface, and Claude explains a
  recommendation better than a table would.

### Not yet built, but designed for

The foundations deliberately support two further phases:

- **A variance model** built from historical actuals, replacing the hardcoded
  σ = 26 in win probability with a real per-lineup number, and enabling a
  win-probability-maximizing lineup mode (start the boom-bust player when you
  are an underdog, the high-floor one when you are favored — derived, not
  hardcoded). Expect σ_margin ≈ 36–42, meaning **current win probabilities
  overstate your edges**: a +10 projected margin reports 65% but is closer to 59%.
- **A season Monte Carlo** producing playoff and championship odds, so every
  recommendation can be scored in Δchampionship-% instead of Δpoints. This
  requires the published schedule, which does not exist until after the draft.
