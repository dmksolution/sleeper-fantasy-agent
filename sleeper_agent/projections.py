"""Read projections out of SQLite and score them with league settings."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from .config import settings
from .league import League
from .store import connect

log = logging.getLogger(__name__)


@dataclass
class Projection:
    player_id: str
    week: int
    points: float
    opponent: str | None
    team: str | None
    stats: dict

    @property
    def has_game(self) -> bool:
        """False means a bye week or an unscheduled player."""
        return bool(self.opponent) or bool(self.stats.get("gp"))


# Parsing one week costs a 3,300 row scan plus a json.loads and a dot product
# per row -- about 150 ms. A single digest asks for it a couple of hundred times
# and the draft simulator asks for it tens of thousands of times, so it has to
# be memoized. Deliberately not functools.lru_cache: League is unhashable, and
# an lru_cache would hand every caller the same mutable dict under a key that
# ignores which league asked.
_PROJ_CACHE: dict[tuple[str, str, int], dict[str, Projection]] = {}


def clear_projection_cache() -> None:
    """Drop memoized projections. Called by sync_all() after new rows land."""
    _PROJ_CACHE.clear()


def week_projections(
    league: League, week: int, *, refresh: bool = False
) -> dict[str, Projection]:
    key = (league.league_id, league.season, week)
    if not refresh:
        hit = _PROJ_CACHE.get(key)
        if hit is not None:
            return hit

    with connect() as conn:
        rows = conn.execute(
            "SELECT player_id, team, opponent, stats FROM projections"
            " WHERE season = ? AND week = ?",
            (league.season, week),
        ).fetchall()
    out: dict[str, Projection] = {}
    for r in rows:
        stats = json.loads(r["stats"])
        out[r["player_id"]] = Projection(
            player_id=r["player_id"],
            week=week,
            points=league.score(stats),
            opponent=r["opponent"],
            team=r["team"],
            stats=stats,
        )
    _PROJ_CACHE[key] = out
    return out


def season_projections(league: League) -> dict[str, Projection]:
    return week_projections(league, 0)


def points_for(league: League, player_ids: list[str], week: int) -> dict[str, float]:
    proj = week_projections(league, week)
    return {pid: (proj[pid].points if pid in proj else 0.0) for pid in player_ids}


def rest_of_season(
    league: League, player_ids: list[str], start_week: int, horizon: int | None = None
) -> dict[str, float]:
    """Sum weekly projections across the horizon.

    Byes fall out for free: a player with no game that week contributes nothing,
    which is exactly right and is why this no longer pro-rates a season
    aggregate. That fallback used to fire for most of the horizon (only four
    weeks were ever cached) and it implicitly assumed everyone plays every week.
    """
    horizon = horizon or settings.ros_horizon_weeks
    end_week = min(start_week + horizon, settings.regular_season_weeks + 1)
    weeks = list(range(start_week, end_week))

    totals = {pid: 0.0 for pid in player_ids}
    missing = []
    for w in weeks:
        proj = week_projections(league, w)
        if not proj:
            missing.append(w)
            continue
        for pid in player_ids:
            entry = proj.get(pid)
            if entry is not None and entry.has_game:
                totals[pid] += entry.points

    if missing:
        log.warning(
            "rest_of_season: weeks %s are not cached, so this total covers only"
            " %s of %s weeks. Run `cli.py sync`.",
            missing,
            len(weeks) - len(missing),
            len(weeks),
        )

    return {pid: round(v, 2) for pid, v in totals.items()}


def weeks_available(league: League, weeks: list[int]) -> list[int]:
    """Subset of `weeks` we actually hold projections for."""
    return [w for w in weeks if week_projections(league, w)]


def positional_vor(
    league: League,
    values: dict[str, float],
    positions: dict[str, str],
    baseline: dict[str, float],
) -> dict[str, float]:
    """Convert raw point totals into value over replacement, by position.

    Comparing a QB's 80 projected points to a TE's 45 is meaningless in a 1-QB
    league: the QB you would stream instead also scores 70.

    `baseline` is the replacement level per position, in points, and is
    required. Build it with `valuation.replacement_baseline()` and pick the mode
    deliberately -- "startable" for draft and trade questions, "waiver" for
    waiver and drop questions. This used to default to a hardcoded depth table
    that no caller overrode, which meant draft advice and waiver advice were
    quietly measured against different definitions of replacement.
    """
    return {
        pid: round(val - baseline.get(positions.get(pid, ""), 0.0), 2)
        for pid, val in values.items()
    }


def consistency(league: League, player_id: str, through_week: int) -> dict:
    """Actual scoring volatility so far. Useful for start/sit on close calls."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT week, stats FROM actuals WHERE season = ? AND player_id = ?"
            " AND week < ? ORDER BY week",
            (league.season, player_id, through_week),
        ).fetchall()
    scores = [league.score(json.loads(r["stats"])) for r in rows]
    scores = [s for s in scores if s is not None]
    if not scores:
        return {"games": 0}
    mean = sum(scores) / len(scores)
    variance = sum((s - mean) ** 2 for s in scores) / len(scores)
    return {
        "games": len(scores),
        "mean": round(mean, 2),
        "stdev": round(variance ** 0.5, 2),
        "floor": round(min(scores), 2),
        "ceiling": round(max(scores), 2),
        "scores": [round(s, 2) for s in scores],
    }
