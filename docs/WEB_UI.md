# The dashboard

A local web UI covering everything the CLI does, so you never need a terminal
during the season.

```bash
python cli.py web
```

Your browser opens at **http://127.0.0.1:8770**. On Windows you can also just
double-click **`Dashboard.cmd`**.

---

## Contents

1. [Starting and stopping](#starting-and-stopping)
2. [The layout](#the-layout)
3. [Draft Room](#draft-room)
4. [Every week](#every-week)
5. [Research](#research)
6. [Data & health](#data--health)
7. [Reading the charts](#reading-the-charts)
8. [Keyboard shortcuts](#keyboard-shortcuts)
9. [How it is built](#how-it-is-built)
10. [Troubleshooting](#troubleshooting)

---

## Starting and stopping

| Command | What it does |
|---|---|
| `python cli.py web` | Start and open a browser |
| `python cli.py web --no-browser` | Start without opening a browser |
| `python cli.py web --port 8781` | Use a different port |
| `Dashboard.cmd` | Windows double-click launcher |

Ctrl-C in the terminal stops it. Closing the browser tab does not — the server
keeps running, which is deliberate: on draft night you want it warm.

> **The server is unauthenticated and binds to localhost.** Anyone who can reach
> the port can read your league data and trigger syncs. `--host 0.0.0.0` exposes
> it to your whole network; only do that behind something that authenticates.

**Keep it running on draft night.** The first request in a fresh process spends
a few seconds reading the projection cache off disk; every later request is
roughly a hundred times faster. A long-lived process is the difference between
a 6-second answer and an instant one on a 90-second clock.

---

## The layout

```text
┌────────────────┬──────────────────────────────────────────────┐
│ Sleeper Agent  │  Draft Room       League · 2026 · pre-draft   │
│ League A…      ├──────────────────────────────────────────────┤
│                │  [ warnings, if the data needs your attention]│
│ OVERVIEW       │  ┌────────┬────────┬────────┬────────┐        │
│  ◆ Dashboard   │  │ Status │ Slot   │ Until  │ Picks  │  tiles │
│ DRAFT          │  └────────┴────────┴────────┴────────┘        │
│  ▲ Draft Room  │  [ filter row — week, slot, live toggle ]      │
│ EVERY WEEK     │  ┌──────────────────────────────────────┐      │
│  ▤ Lineup      │  │ card: chart + table view toggle      │      │
│  ＋ Waivers     │  └──────────────────────────────────────┘      │
│  ⚔ Matchup     │                                                │
│  ⇄ Trades      │                                                │
│ RESEARCH       │                                                │
│  ◎ Players     │                                                │
│  ☰ League      │                                                │
│  ▦ Weekly brief│                                                │
│ SYSTEM         │                                                │
│  ◇ Data        │                                                │
│ [↻ Sync] [☀]   │                                                │
└────────────────┴──────────────────────────────────────────────┘
```

**The Dashboard changes with the season.** Before the draft it shows a prep
checklist and the biggest model-vs-market gaps. During the season it shows your
matchup and whether points are sitting on your bench.

Every view is deep-linkable — `#/draft?tab=dissent`, `#/players`,
`#/draft?tab=recommend&slot=7&auto=1` (that last one runs the simulation on
load). Bookmark the one you want on Sunday morning.

**Light and dark** both ship as deliberately chosen colors, not an inverted
filter. The toggle is at the bottom of the sidebar and is remembered.

---

## Draft Room

The most important screen, with five tabs.

### Recommend — the simulator

Press **Simulate the rest of the draft**. For each pick you could make now, it
forces that pick, plays all remaining rounds out a few hundred times with
opponents drafting to a noisy ADP, and scores the roster you end up with by the
starting lineups it would actually produce across the season — real byes, with
weeks 15–17 weighted 1.5×.

You get a plain-language recommendation, a range chart, and a table:

| Column | Meaning |
|---|---|
| **Expected** | Weighted season points from the finished roster |
| **Regret** | Points behind the best option. Small = it barely matters |
| **Lasts** | Probability he survives to your **next** pick |
| **Typical next rounds** | The position sequence that usually follows |

**Read Regret against Lasts. That pairing is the whole point.** Two players 5
points apart where one has `Lasts 5%` and the other `Lasts 90%`: take the first
one now, because you can probably still have the second next turn.

The dot is the expected outcome; the bar spans the 10th to 90th percentile
across simulations, so you can see when a pick is a coin flip rather than a
clear edge.

Players who will almost certainly be gone before your turn are dropped, and the
count is reported. Asked from slot 12, it will not offer you the consensus 1.01.

**Results survive a page refresh.** If you reload mid-draft the last simulation
is restored, and it is invalidated automatically once more picks happen.

### Go live

The **Go live** button opens a server-sent event stream that polls the draft and
pushes updates to the page. When a pick lands the header updates and the
simulation re-runs automatically. You get a toast when you are two picks away.

One poller serves every open tab, so having the dashboard open on a laptop and a
phone does not double the requests to Sleeper.

### Your draft slot

Commissioners usually publish the draft order at the last minute, so the slot
selector matters. Three sources, in descending order of trust, and the UI tells
you which one it used:

- **draft_order** — the commissioner set it
- **inferred** — derived from a pick you already made
- **assumed** — you picked it from the dropdown

### Value board

The full cheat sheet, sortable on every column. `VBD` is value over replacement
derived from your league's own roster slots; `Blended` pulls that 35% toward the
market. `Wk15-17` is projected fantasy-playoff points. Click any row to open the
player.

### Model vs market

The best pre-draft hour you can spend. Two lists: players we rate well above
ADP (possible value) and players the market rates well above us (possible trap,
or news the projections missed).

Kickers and defenses are excluded and the reason is stated on the card. Value
over replacement ranks the top ones around pick 60–110 against a market that
takes them at 120–190, and the market is right — VBD says nothing about how
*predictable* a projection is, and K/DEF projections have almost no signal.

### Slot planner

Simulates a full draft from all 12 slots. The useful column is **typical
opening**: the position sequence that tends to come back to you from each slot,
so you arrive with a shape in mind. Scores across slots are close by design —
snake drafts are roughly fair — so do not read much into small differences.

### Recap

After the draft: value captured per draft slot, with your slot highlighted.

---

## Every week

**Lineup** — your optimal starters with injury and bye adjustments, what you
currently have set, and the exact swaps worth making. The tile that matters is
*points on the bench*.

**Waivers** — every free agent scored by how much he would raise your **starting
lineup**, not by talent, plus a suggested bid as a share of your remaining FAAB.
A brilliant handcuff behind your own starter correctly scores near zero. Drop
candidates are measured against the wire, not against your starters.

**Matchup** — win probability, the projected margin, and a diverging chart of
where the matchup is won and lost slot by slot. The card is explicit that the
win-probability model uses a fixed spread and does not vary with which players
you start, so treat extremes with suspicion.

**Trades** — search for players on each side, optionally pick a partner, and
evaluate. Both rosters are re-optimized week by week. With a partner selected
you also get their side, because a trade both teams gain from is the only kind
that gets accepted.

---

## Research

**Players** — search any NFL player; add up to four to compare. The weekly
projection chart breaks at byes rather than dipping to zero, and shades the
fantasy playoffs.

**League** — standings, points for, your bye-week calendar (darker = more
players idle, orange outline marks the playoffs), and league-wide trending adds.

**Weekly brief** — the full digest as formatted markdown, with a button to push
it to your phone via ntfy.

---

## Data & health

Answers "can I trust these numbers right now". Week coverage, a per-position
scoring audit against Sleeper's own numbers, row counts, warnings, and your full
league configuration.

The QB row will always show a ~2.6 point difference. That is expected and
explained inline: Sleeper's `pts_ppr` scores interceptions at +2 while your
league scores them at −1, so their number runs `3 × pass_int` high. Ours is the
correct one.

**Sync data** in the sidebar refreshes everything from Sleeper. It runs as a
background job with live progress, so a 35-second full sync looks like progress
rather than a hang.

---

## Reading the charts

Every chart follows the same rules, so they read as one system:

- **A legend whenever there is more than one color**, and never for a single
  series. Position colors are always accompanied by a legend and by the text
  label, so color is never the only way to tell things apart.
- **A table view on every chart.** The toggle sits under each one. This is not
  decoration: three of the palette's light-mode colors sit below 3:1 contrast
  against the surface, and a readable table is the required relief.
- **Direct labels on the marks that matter**, never a number on every point.
- **Hover anywhere in a row**, not just on the mark — hit targets are the full
  row, and keyboard focus shows the same tooltip.
- **Diverging blue↔red only for genuine polarity** (you vs them), with a neutral
  midpoint. Sequential blue for magnitude. Status colors are reserved for
  good/warning/critical and never reused as a series.

The palette is the validated default from the visualization guidance, run
through its checker in both light and dark: all checks pass on the adjacent
pairlist (worst colorblind ΔE 9.1 light / 8.4 dark).

---

## Keyboard shortcuts

Single keys, active whenever you are not typing in a field:

| Key | View | Key | View |
|---|---|---|---|
| `D` | Dashboard | `T` | Trades |
| `R` | Draft Room | `P` | Players |
| `L` | Lineup | `G` | League |
| `W` | Waivers | `B` | Weekly brief |
| `M` | Matchup | `H` | Data & health |

---

## How it is built

No framework, no build step, no `npm install`. The backend is Python's
`ThreadingHTTPServer`; the frontend is ES modules, vanilla DOM and hand-written
SVG. `pip install -r requirements.txt` remains the entire setup.

That is a deliberate choice rather than minimalism for its own sake. A
single-user dashboard on localhost does not need a framework, and every
dependency added here is one more thing that can break on a Sunday morning in
December — which is exactly when this has to work.

```text
webapp/
  server.py            routing, static files, background jobs, SSE
  api.py               JSON adapters over the analysis modules
  static/
    index.html
    css/app.css        design system: palette, layout, components
    js/api.js          fetch, job polling, SSE with reconnect
    js/charts.js       SVG primitives with the viz rules baked in
    js/ui.js           tables, tiles, cards, toasts, typeahead, markdown
    js/views-draft.js  dashboard + draft room
    js/views-season.js lineup, waivers, matchup, trades, players, league, data
    js/app.js          router and shell
```

**Slow work runs as a background job.** A sync or a simulation returns a job id
immediately; the browser polls and shows real progress. **Live draft updates use
server-sent events** — one long response, no extra protocol, and one shared
poller across all open tabs.

**Normal-but-empty states are modeled, not treated as errors.** An empty roster
before the draft, an unpublished draft order, a season with no games played —
each returns a structured payload with a reason and a suggested next action, and
renders as a useful dead end rather than a spinner or a 500.

### API

Every screen is a thin layer over a documented JSON endpoint, so you can script
against it or point another tool at it.

| Method | Endpoint | Notes |
|---|---|---|
| GET | `/api/bootstrap` | Everything the shell needs on first paint |
| GET | `/api/health` | Coverage, scoring audit, warnings |
| GET | `/api/board` | `?position=&top=` |
| GET | `/api/draft/state` | `?slot=` |
| GET | `/api/draft/dissent` | Model vs market |
| GET | `/api/draft/recap` | |
| GET | `/api/draft/simulate/last` | Last completed simulation |
| GET | `/api/lineup`, `/api/startsit`, `/api/matchup` | `?week=` |
| GET | `/api/standings`, `/api/byes`, `/api/activity`, `/api/trending` | |
| GET | `/api/players/search` | `?q=` |
| GET | `/api/player`, `/api/compare` | `?id=` / `?ids=a,b,c` |
| GET | `/api/trade/targets` | `?week=` |
| GET | `/api/events/draft` | Server-sent events |
| POST | `/api/sync` | job |
| POST | `/api/draft/simulate`, `/api/draft/plan` | job |
| POST | `/api/waivers`, `/api/digest` | job |
| POST | `/api/trade/evaluate` | `{send:[], receive:[], partner, week}` |
| POST | `/api/digest/notify` | Push to ntfy |
| GET | `/api/jobs` | `?id=` — poll a job |

---

## Troubleshooting

**Port already in use** — `python cli.py web --port 8781`.

**"Cannot reach the server"** — the process stopped. Restart it; the browser
recovers on reload.

**First page load takes several seconds** — cold SQLite page reads, worse over a
network share. Every request after that is fast. Leave it running.

**A view says your roster is empty** — expected before the draft. The button on
that screen takes you to the Draft Room.

**The simulation says "no draft slot known"** — pick your slot in the Draft Room
dropdown, or use the Slot planner to study all twelve.

**Charts look wrong or colors are off** — open the table view under any chart;
every value is there too.

**Nothing updates during a live draft** — check that **Go live** is toggled on.
The feed reconnects automatically if it drops.
