# Sleeper Fantasy Agent

An AI-assisted fantasy football toolkit built on the public Sleeper API. Gives
Claude 18 tools for draft prep, lineup optimization, waiver claims, trade
evaluation and weekly reporting, plus a CLI and a scheduler that pushes a brief
to your phone before waivers run.

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
cases with zero mismatches.

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

# Populate the local cache (~15 MB player index plus projections)
python cli.py sync --full
```

Then confirm it read your league correctly:

```bash
python cli.py info
```

## Draft day

Build the value board the night before. You do not want to be waiting on network
calls with 90 seconds on the clock.

```bash
python cli.py sync --full
python cli.py board --top 60              # full board
python cli.py board --position RB --top 30 # one position
```

During the draft, this reads the live pick feed and filters to who is actually
still available:

```bash
watch -n 20 'python cli.py draft --top 12'
```

It reports picks until your turn comes back around (snake-aware), your
positional needs so far, and a tier-scarcity bonus that flags when you are about
to lose the last player in a tier.

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
- "Is trading McBride for Jonathan Taylor good for me?"
- "Who is the best available player in my draft right now?"
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

## Layout

```
sleeper_agent/
  client.py       rate-limited read-only Sleeper client, retries and backoff
  store.py        SQLite cache with TTLs, stale-read fallback, decision log
  sync.py         pulls players, projections, actuals and ADP into SQLite
  league.py       league settings and the custom scoring engine
  projections.py  scored projections, rest-of-season, positional VOR
  lineup.py       Hungarian assignment optimizer, start/sit diffing
  draft.py        VBD board, tier detection, live draft state
  waivers.py      lineup-impact ranking, FAAB sizing, drop candidates
  trades.py       two-sided trade evaluation, trade target discovery
  matchup.py      head to head, win probability, standings, bye planning
  digest.py       the weekly markdown brief and notification push
mcp_server.py     18 MCP tools
cli.py            the same functionality from a terminal
sql/schema.sql    tables, indexes, decision log
```

Everything reads from SQLite, so analysis is fast and still works if Sleeper is
having a bad day. `sync` is the only thing that touches the network.

## Notes and limits

- Projections come from Sleeper's own undocumented projections endpoint. It is
  stable and free, but it is one source. Every caller degrades gracefully if it
  returns nothing, and a stale cached projection is preferred over no projection
  on a Sunday morning.
- Win probability uses a normal approximation on the margin with a 26 point
  standard deviation. It is calibrated well enough to separate a coin flip from
  a real edge, which is all it needs to do.
- FAAB suggestions are a percentage of your *remaining* budget, scaled by actual
  lineup impact and bid up when a player is trending hard league-wide.
- `recommendations` in SQLite logs every call the agent makes, with a timestamp.
  Grade it against results later, and tune the weights.
- Rate limiting is set to 300 requests per minute against Sleeper's stated
  ceiling of 1000. A normal session makes a handful of calls.
