"""Season-long player value, derived by summing real weekly projections.

Why this module exists
----------------------

Sleeper publishes a season aggregate at `week = 0`, and it is tempting to treat
it as "the season projection". It is not safe to score. Measured against
Sleeper's own `pts_ppr` on the cached 2026 rows:

    position   mean signed error of league.score() on the week-0 row
    QB/RB/WR/TE           ~0.00      (exact)
    K                   -22.61       (up to -40 on a single player)
    DEF                 +10.00       (all 32 teams)

Two separate causes, both structural rather than fixable by renaming keys:

  * The kicker aggregate emits `fgm_50p` and `fgmiss_50p`, which are not
    scoring categories in a league that pays by distance bucket
    (`fgm_50_59`, `fgm_60p`), and it omits the 0-19 / 20-29 / 30-39 made-FG
    buckets entirely. Those points simply have nothing to multiply.
  * The defense aggregate carries `pts_allow_0: 1.0`. In a weekly line that is
    a bucket *flag* meaning "this defense allowed zero points this week", and
    multiplying it by the league's 10.0 is correct. In a season aggregate the
    same flag means nothing, so every defense collects a phantom shutout, and
    the actual 17 games' worth of points-allowed scoring is absent.

Weekly lines have neither problem: they score exactly at every position. So the
rule this module enforces is that **week 0 is an ADP carrier, not a scoring
source**, and every season-long number is the sum of real weekly projections.

That also buys three things the pro-rated aggregate could never provide: real
bye weeks, real fantasy-playoff (weeks 15-17) value, and per-week detail that
the lineup optimizer and the draft simulator can actually consume.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, Literal

from .config import settings
from .league import League, load_players
from .projections import season_projections, week_projections

log = logging.getLogger(__name__)

# Fantasy weeks, not NFL weeks. Week 18 is real football but no fantasy league
# plays it, so it never counts toward value.
REGULAR = tuple(range(1, 15))
PLAYOFFS = tuple(range(15, 18))
FULL = tuple(range(1, 18))

# Positions whose week-0 aggregate is known-broken (see the module docstring).
# For these we refuse to guess rather than return a confidently wrong number.
NO_AGGREGATE_FALLBACK = {"K", "DEF"}

# Below this many cached weeks, summing is not representative of a season and we
# fall back to pro-rating the aggregate for offense.
MIN_WEEKS_FOR_SUM = 10


@dataclass
class SeasonValue:
    player_id: str
    position: str
    points: float
    games: int
    ppg: float
    bye_weeks: list[int] = field(default_factory=list)
    playoff_points: float = 0.0
    weekly: dict[int, float] = field(default_factory=dict)
    derived_from: Literal["weekly", "aggregate"] = "weekly"

    def as_dict(self) -> dict:
        return {
            "player_id": self.player_id,
            "position": self.position,
            "points": round(self.points, 1),
            "games": self.games,
            "ppg": round(self.ppg, 2),
            "bye_weeks": self.bye_weeks,
            "playoff_points": round(self.playoff_points, 1),
            "derived_from": self.derived_from,
        }


_BYE_CACHE: dict[tuple[str, str], dict[str, int]] = {}


def team_bye_weeks(league: League, weeks: Iterable[int] = FULL) -> dict[str, int]:
    """NFL team -> its bye week, derived from who actually plays each week.

    Per-player bye detection does not work uniformly: on a bye, offensive
    players get a projection row with no opponent, but defenses get no row at
    all. Deriving the schedule at the team level catches both, and it is the
    only way a DEF ever reports a bye.
    """
    key = (league.league_id, league.season)
    hit = _BYE_CACHE.get(key)
    if hit is not None:
        return hit

    weeks = tuple(weeks)
    playing: dict[int, set[str]] = {}
    for w in weeks:
        proj = week_projections(league, w)
        if not proj:
            continue
        playing[w] = {p.team for p in proj.values() if p.has_game and p.team}

    everyone: set[str] = set()
    for teams in playing.values():
        everyone |= teams

    out: dict[str, int] = {}
    for team in everyone:
        off = [w for w, teams in playing.items() if team not in teams]
        if len(off) == 1:
            out[team] = off[0]
        elif off:
            # More than one gap means incomplete data, not two byes. Take the
            # earliest and let coverage() explain the rest.
            out[team] = min(off)
    _BYE_CACHE[key] = out
    return out


def clear_bye_cache() -> None:
    _BYE_CACHE.clear()


def coverage(league: League, weeks: Iterable[int] = FULL) -> dict:
    """Which weeks we can actually compute from, so callers can be honest."""
    weeks = tuple(weeks)
    present, missing = [], []
    for w in weeks:
        (present if week_projections(league, w) else missing).append(w)
    return {
        "weeks_requested": list(weeks),
        "weeks_present": present,
        "weeks_missing": missing,
        "complete": not missing,
    }


def season_value(
    league: League,
    weeks: Iterable[int] = FULL,
    player_ids: Iterable[str] | None = None,
    fallback_to_aggregate: bool = True,
) -> dict[str, SeasonValue]:
    """Sum real weekly projections into season-long value.

    Only weeks where the player's team actually has a game are counted, so byes
    fall out for free rather than being modelled.
    """
    weeks = tuple(weeks)
    wanted = set(player_ids) if player_ids is not None else None
    players = load_players()

    weekly_maps: dict[int, dict] = {}
    for w in weeks:
        proj = week_projections(league, w)
        if proj:
            weekly_maps[w] = proj

    have = len(weekly_maps)
    if have < len(weeks):
        log.warning(
            "season_value: %s of %s weeks cached (missing %s); run `cli.py sync`",
            have,
            len(weeks),
            [w for w in weeks if w not in weekly_maps],
        )

    thin = have < MIN_WEEKS_FOR_SUM
    aggregate = season_projections(league) if (thin and fallback_to_aggregate) else {}
    byes_by_team = team_bye_weeks(league, weeks)

    out: dict[str, SeasonValue] = {}
    seen = {pid for proj in weekly_maps.values() for pid in proj}
    if wanted is not None:
        seen &= wanted

    for pid in seen:
        player = players.get(pid)
        position = player.position if player else ""
        pts = 0.0
        playoff_pts = 0.0
        games = 0
        per_week: dict[int, float] = {}

        bye = byes_by_team.get(player.team) if player and player.team else None
        byes: list[int] = [bye] if bye is not None else []

        for w in weeks:
            proj = weekly_maps.get(w)
            if proj is None:
                continue
            entry = proj.get(pid)
            if entry is None or not entry.has_game:
                continue
            games += 1
            per_week[w] = entry.points
            pts += entry.points
            if w in PLAYOFFS:
                playoff_pts += entry.points

        derived: Literal["weekly", "aggregate"] = "weekly"
        if thin and fallback_to_aggregate:
            if position in NO_AGGREGATE_FALLBACK:
                # Refuse to pro-rate a known-broken aggregate. A missing number
                # is recoverable; a wrong one silently poisons the draft board.
                continue
            agg = aggregate.get(pid)
            if agg is not None:
                missing_weeks = len(weeks) - have
                pts += (agg.points / settings.regular_season_weeks) * missing_weeks
                derived = "aggregate"

        out[pid] = SeasonValue(
            player_id=pid,
            position=position,
            points=round(pts, 2),
            games=games,
            ppg=round(pts / games, 2) if games else 0.0,
            bye_weeks=byes,
            playoff_points=round(playoff_pts, 2),
            weekly=per_week,
            derived_from=derived,
        )
    return out


def season_points(
    league: League, weeks: Iterable[int] = FULL, player_ids: Iterable[str] | None = None
) -> dict[str, float]:
    """Just the totals, for callers that do not need the detail."""
    return {pid: sv.points for pid, sv in season_value(league, weeks, player_ids).items()}


# ------------------------------------------------------- replacement level


ReplacementMode = Literal["startable", "waiver"]

# How deep the freely-available pool runs at each position. Used only for the
# "waiver" baseline: the question there is "what could I have for nothing?",
# not "what does a league-average starter score?".
WAIVER_DEPTH = {"QB": 3, "RB": 6, "WR": 8, "TE": 3, "K": 2, "DEF": 2}


def replacement_baseline(
    league: League,
    values: dict[str, float],
    positions: dict[str, str],
    mode: ReplacementMode,
) -> dict[str, float]:
    """Points a position's replacement-level player is worth, by position.

    Two legitimately different questions, which the codebase previously
    conflated by having one hardcoded depth table serve both:

      * `startable` -- what a league-average *starter* at this position scores.
        Derived from the league's own roster slots times team count, so in a 12
        team league with RB/RB/FLEX/FLEX the RB baseline lands around RB35. This
        is the right denominator for draft value and for trades, where you are
        comparing against what everyone else will be starting.

      * `waiver` -- what you could pick up for free right now. Derived from the
        Nth best player in whatever pool the caller passed in. This is the right
        denominator for waiver claims and drop candidates, where the real
        alternative is the wire, not a league-average starter.

    Callers must choose. That is the point: the previous default silently
    applied a waiver-shaped answer to draft-shaped questions.
    """
    grouped: dict[str, list[float]] = {}
    for pid, val in values.items():
        pos = positions.get(pid)
        if pos:
            grouped.setdefault(pos, []).append(val)

    if mode == "startable":
        from .draft import replacement_levels

        depth = replacement_levels(league)
    elif mode == "waiver":
        depth = dict(WAIVER_DEPTH)
    else:  # pragma: no cover - guarded by the Literal
        raise ValueError(f"unknown replacement mode {mode!r}")

    baseline: dict[str, float] = {}
    for pos, vals in grouped.items():
        vals.sort(reverse=True)
        idx = min(depth.get(pos, 6), len(vals)) - 1
        baseline[pos] = vals[idx] if idx >= 0 else 0.0
    return baseline
