# Sleeper Fantasy Agent

An AI-assisted fantasy football toolkit built on the public Sleeper API. Gives
Claude 22 tools for draft prep, lineup optimization, waiver claims, trade
evaluation and weekly reporting, plus a CLI and a scheduler that pushes a brief
to your phone before waivers run.

**[Full usage guide →](docs/USAGE.md)** — every command, every flag, the draft
night playbook, and the measured quirks in Sleeper's data that this works around.

## The one constraint that shapes everything

The Sleeper API is **read-only**. There is no auth token because there is nothing
to authorize: no endpoint can set a lineup, submit a waiver claim or accept a
trade. So this is an advisor, not an autopilot. It tells you exactly what to do
and you tap the button in the Sleeper app. Any tool claiming to fully automate a
Sleeper team is either scraping the private mobile endpoints or lying to you.

## What makes the recommendations trustworthy

**Your scoring, not generic PPR.** Every projection is re-scored by
dot-producting the raw projected stat line against your league's own
`scoring_settings`. Half PPR, TE premium, 6-point passing TDs, return yardage,
IDP: all of it is handled by the same calculation, because keys that are not
scoring categories simply have no weight and contribute zero.

**Exact lineup optimization.** Setting a lineup is an assignment problem, and
greedy filling gets it wrong whenever flex slots overlap. This solves it exactly
with the Hungarian algorithm. Validated against brute force on 300 randomized
cases with zero mismatches — see `tests/test_hungarian.py`, and run it yourself
with `python cli.py selftest`.

**Season value summed from real weeks, not Sleeper's aggregate.** Sleeper
publishes a season total that is exact for skill positions and structurally
wrong for kickers and defenses: measured against Sleeper's own numbers it
understates every kicker by about 22 points and hands all 32 defenses a phantom
10-point shutout. Weekly lines are exact, so all season value is summed from
them. Byes and fantasy-playoff value fall out for free. Details in
[docs/USAGE.md](docs/USAGE.md#10-known-data-quirks).

**Drafting by simulation, not by best-available.** `python cli.py draft` plays
the rest of the draft out a few hundred times per candidate and ranks picks by
how the finished roster tends to score across the season. That subsumes
positional need, tier scarcity and player survival rather than approximating
each with a tuned bonus.

**Value over replacement everywhere.** Comparing a QB's 82 projected points to a
TE's 51 is meaningless in a 1-QB league, because the QB you would stream instead
also scores 74. Waiver targets, drop candidates and draft values are all scored
against a positional replacement level derived from your league's roster slots
and team count.

**Lineup impact over raw talent.** A waiver target is ranked by re-running the
optimizer with that player added and measuring the delta. A great handcuff RB
behind your own starter scores near zero, correctly.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env

# Prints every league you are in, with the IDs to paste into .env
python cli.py setup --username <your_sleeper_username>

# Populate the local cache: player index plus all 18 weeks of projections
python cli.py sync
```

Then confirm it read your league correctly, and that the numbers are sound:

```bash
python cli.py info
python cli.py health
```

## Draft day

A week out, study what each draft slot tends to produce:

```bash
python cli.py draft --plan
```

The night before, review where the model and the market disagree, then warm
everything so nothing is computed on the clock:

```bash
python cli.py draft --dissent --top 15
python cli.py draft --precompute
```

`--dissent` is the best hour you will spend. Sleeper's projections fail in
predictable, checkable ways — a rookie with no history, a receiver who just
inherited a bigger role, a backfield that resolved in August — and this lists
exactly where to look.

During the draft, keep one process alive. It polls the live pick feed, reprints
when a pick lands, and alarms when you are two picks out:

```bash
python cli.py draft --watch --slot 7
```

Each candidate is ranked by simulating the rest of the draft. Read `Regret`
(points behind the best option) against `Lasts` (chance he survives to your next
pick): a close second who is likely to last is the one to pass on. Pass `--slot`
only until you have made a pick — after that the slot is inferred.

Afterward:

```bash
python cli.py recap    # value captured per team, reaches and steals
```

## During the season

```bash
python cli.py lineup      # optimal lineup for this week
python cli.py startsit    # swaps versus what you currently have set
python cli.py waivers     # targets ranked by lineup gain, with FAAB bids
python cli.py matchup     # head to head with win probability
python cli.py targets     # managers whose bench surplus fits your holes
python cli.py byes        # weeks where three or more of your players are out
python cli.py player "Bijan Robinson"
python cli.py digest      # the full brief, all of the above in one report
```

Trade evaluation re-optimizes both rosters across the horizon and reports the
change in expected starting points for each side:

```bash
python cli.py trade --send "Trey McBride" --receive "Jonathan Taylor" --partner 3
```

`--partner` takes a roster_id and adds a read on whether they would accept.

## Connecting Claude

For local use, stdio is simplest. Add to your Claude Desktop or Claude Code MCP
config, using absolute paths:

```json
{
  "mcpServers": {
    "sleeper-fantasy": {
      "command": "/usr/bin/python3",
      "args": ["/path/to/sleeper-fantasy-agent/mcp_server.py"],
      "env": {
        "SLEEPER_USERNAME": "your_sleeper_username",
        "SLEEPER_LEAGUE_ID": "your_league_id",
        "DB_PATH": "/path/to/sleeper-fantasy-agent/data/sleeper.db"
      }
    }
  }
}
```

Restart the client, then ask normally:

- "Who should I start at flex this week?"
- "Best waiver adds, and what should I bid out of my remaining FAAB?"
- "Is trading McBride for Jonathan Taylor good for me? Would they accept?"
- "Who should I take with my next pick?" (runs the draft simulation)
- "I'm picking 7th. What does my draft usually look like from there?"
- "Where do your projections disagree with ADP most?"
- "Can I trust these numbers right now?" (runs the health check)
- "Give me my week brief."

The MCP SDK renamed `FastMCP` to `MCPServer` in 2.0. `mcp_server.py` imports
either, so it runs on both generations.

## Running on the NAS

```bash
docker compose up -d --build
docker compose logs -f fantasy-agent
```

The scheduler container syncs every six hours and pushes the brief on a schedule
built around the NFL week: Tuesday 7am before waivers are due, Wednesday 7am
once claims have processed, Sunday 11am ahead of the 1pm ET lock, and hourly
through the early slate to catch late scratches. Edit `scripts/crontab` to suit
your league's waiver day.

For notifications, set either `NTFY_TOPIC` (install the ntfy app, subscribe to an
unguessable topic name) or `DIGEST_WEBHOOK_URL` (anything that accepts
`{"title": ..., "text": ...}`).

The optional `fantasy-mcp` service is behind a compose profile and bound to
localhost. It is unauthenticated. If you want to reach it from your phone, put
it behind your Cloudflare Tunnel with Access in front of it rather than opening
the port.

## Running on Windows

The container schedule is Linux-only. On Windows, register the same jobs as
Scheduled Tasks:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\schedule_tasks.ps1
.\scripts\schedule_tasks.ps1 -Unregister   # to remove
```

If the project lives on a mapped network drive the script handles it — mapped
drives do not exist in the session a Scheduled Task runs in, so it resolves the
drive to its UNC root. Without that, tasks fail instantly with result `1` and
write no log.

## Layout

```
sleeper_agent/
  client.py       rate-limited read-only Sleeper client, retries and backoff
  store.py        SQLite cache with TTLs, stale-read fallback, decision log
  sync.py         pulls players, projections, actuals and ADP; data_health()
  league.py       league settings and the custom scoring engine
  projections.py  scored projections (memoized), rest-of-season, positional VOR
  valuation.py    season value summed from real weeks, byes, replacement levels
  lineup.py       Hungarian assignment optimizer, start/sit diffing
  draft.py        VBD board, tiers, ADP blending, model-vs-market disagreements
  draft_sim.py    Monte Carlo draft rollout, live watch mode, slot planning
  survival.py     P(available at my next pick), calibrated against the live draft
  waivers.py      lineup-impact ranking, FAAB sizing, drop candidates
  trades.py       two-sided trade evaluation, trade target discovery
  matchup.py      head to head, win probability, standings, bye planning
  digest.py       the weekly markdown brief and notification push
mcp_server.py     22 MCP tools
cli.py            the same functionality from a terminal
tests/            46 tests, standard library unittest
scripts/          crontab, Docker entrypoint, Windows Scheduled Tasks
sql/schema.sql    tables, indexes, decision log
docs/USAGE.md     the complete guide
```

Everything reads from SQLite, so analysis is fast and still works if Sleeper is
having a bad day. `sync` is the only thing that touches the network.

## Notes and limits

- Projections come from Sleeper's own undocumented projections endpoint. It is
  stable and free, but it is one source. Every caller degrades gracefully if it
  returns nothing, and a stale cached projection is preferred over no projection
  on a Sunday morning.
- Win probability uses a normal approximation on the margin with a hardcoded 26
  point standard deviation. That is the weakest number in the codebase: it does
  not vary with which players you are starting, and it is almost certainly too
  small, which means **reported win probabilities overstate your edges**. Good
  enough to separate a coin flip from a real edge, not good enough to bet on.
- Kicker and defense scoring has a documented trap in Sleeper's season
  aggregate. This tool sums real weekly lines instead, so `board` and every
  rest-of-season number are correct where most tools are not. See
  [docs/USAGE.md](docs/USAGE.md#10-known-data-quirks).
- Sleeper's own `pts_ppr` scores interceptions at +2. Anything ranking
  quarterbacks on that field overvalues turnover-prone passers by about 3 points
  per projected interception. This scores against your league's settings instead.
- FAAB suggestions are a percentage of your *remaining* budget, scaled by actual
  lineup impact and bid up when a player is trending hard league-wide.
- `recommendations` in SQLite logs every call the agent makes, with a timestamp.
  Grade it against results later, and tune the weights.
- Rate limiting is set to 300 requests per minute against Sleeper's stated
  ceiling of 1000. A normal session makes a handful of calls.
- Run `python cli.py selftest` to verify the solver and the scoring engine, and
  `python cli.py health` to check whether the cached data is currently sound.
