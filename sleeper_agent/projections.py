"""Read projections out of SQLite and score them with league settings."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .config import settings
from .league import League
from .store import connect


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


def week_projections(league: League, week: int) -> dict[str, Projection]:
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

    Falls back to a pro-rated slice of the season aggregate for any week that has
    not been published yet, which is normal in the preseason.
    """
    horizon = horizon or settings.ros_horizon_weeks
    end_week = min(start_week + horizon, settings.regular_season_weeks + 1)
    weeks = list(range(start_week, end_week))

    totals = {pid: 0.0 for pid in player_ids}
    missing_weeks = 0
    for w in weeks:
        proj = week_projections(league, w)
        if not proj:
            missing_weeks += 1
            continue
        for pid in player_ids:
            if pid in proj:
                totals[pid] += proj[pid].points

    if missing_weeks:
        season = season_projections(league)
        per_week = settings.regular_season_weeks
        for pid in player_ids:
            if pid in season:
                totals[pid] += (season[pid].points / per_week) * missing_weeks

    return {pid: round(v, 2) for pid, v in totals.items()}


def positional_vor(
    league: League,
    values: dict[str, float],
    positions: dict[str, str],
    depth: dict[str, int] | None = None,
) -> dict[str, float]:
    """Convert raw point totals into value over replacement, by position.

    Comparing a QB's 80 projected points to a TE's 45 is meaningless in a 1-QB
    league: the QB you would stream instead also scores 70. Replacement level is
    the Nth best player available at that position, where N reflects how deep
    the position runs on waivers.
    """
    default_depth = {"QB": 4, "RB": 8, "WR": 10, "TE": 4, "K": 3, "DEF": 4}
    depth = {**default_depth, **(depth or {})}

    grouped: dict[str, list[float]] = {}
    for pid, val in values.items():
        pos = positions.get(pid)
        if pos:
            grouped.setdefault(pos, []).append(val)

    baseline: dict[str, float] = {}
    for pos, vals in grouped.items():
        vals.sort(reverse=True)
        idx = min(depth.get(pos, 6), len(vals)) - 1
        baseline[pos] = vals[idx] if idx >= 0 else 0.0

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
