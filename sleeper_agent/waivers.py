"""Waiver wire and free agency recommendations.

The core question is not "who is the best available player" but "who improves my
starting lineup, and by how much". A great backup RB behind an elite starter is
worth less to you than a mediocre TE if TE is where you are bleeding points. So
every candidate is valued by re-running the lineup optimizer with that player
added and measuring the delta.
"""

from __future__ import annotations

from dataclasses import dataclass

from .client import client
from .config import settings
from .league import League, load_players
from .lineup import optimize
from .projections import positional_vor, rest_of_season, week_projections
from .valuation import replacement_baseline, season_value
from .store import log_recommendation

# Positions worth picking up in a standard league.
STREAMABLE = {"QB", "RB", "WR", "TE", "K", "DEF"}


@dataclass
class WaiverTarget:
    player_id: str
    name: str
    position: str
    team: str | None
    next_week_points: float
    ros_points: float
    ros_vor: float
    lineup_gain: float
    trend_adds: int
    opponent: str | None
    reason: str
    suggested_faab_pct: float = 0.0
    suggested_faab: int = 0

    def as_dict(self) -> dict:
        return {
            "player_id": self.player_id,
            "player": f"{self.name} ({self.position}-{self.team or 'FA'})",
            "position": self.position,
            "next_week_points": round(self.next_week_points, 1),
            "ros_points": round(self.ros_points, 1),
            "value_over_replacement": round(self.ros_vor, 1),
            "starting_lineup_gain": round(self.lineup_gain, 2),
            "trending_adds_24h": self.trend_adds,
            "opponent": self.opponent,
            "suggested_faab": f"{self.suggested_faab} ({self.suggested_faab_pct:.0f}%)",
            "reason": self.reason,
        }


def free_agents(league: League, positions: set[str] = STREAMABLE) -> list[str]:
    rostered = league.rostered_player_ids()
    # Enumerate from real season value rather than the week-0 aggregate, whose
    # kicker and defense rows are structurally wrong (see `valuation`).
    candidate_ids = [pid for pid in season_value(league) if pid not in rostered]
    players = load_players(candidate_ids)
    return [
        pid
        for pid in candidate_ids
        if (p := players.get(pid))
        and p.position in positions
        and p.team
        and not p.is_out
    ]


def trending_map(lookback_hours: int = 48, limit: int = 100) -> dict[str, int]:
    return {
        row["player_id"]: row.get("count", 0)
        for row in client.trending("add", lookback_hours=lookback_hours, limit=limit)
    }


def recommend_waivers(
    league: League,
    roster: dict,
    week: int,
    top_n: int = 10,
    candidate_pool: int = 90,
    log: bool = False,
) -> dict:
    """Rank free agents by how much they would improve your starting lineup."""
    my_ids = [p for p in (roster.get("players") or []) if p]
    baseline = optimize(league, my_ids, week).projected_total
    baseline_ros = sum(rest_of_season(league, my_ids, week).values())

    fa_ids = free_agents(league)
    if not fa_ids:
        return {"week": week, "targets": [], "note": "no free agent projections cached yet"}

    # Prefilter on value over replacement at each position, not raw points. A
    # backup QB outscores every streaming TE in raw terms and is worth far less.
    fa_ros = rest_of_season(league, fa_ids, week)
    fa_players = load_players(fa_ids)
    fa_pos = {pid: p.position for pid, p in fa_players.items()}
    # "waiver" mode: the alternative to this claim is another free agent, not a
    # league-average starter.
    fa_vor = positional_vor(
        league, fa_ros, fa_pos, replacement_baseline(league, fa_ros, fa_pos, "waiver")
    )
    shortlist = sorted(fa_ids, key=lambda pid: -fa_vor.get(pid, 0))[:candidate_pool]

    players = fa_players
    next_week = week_projections(league, week)
    trends = trending_map()

    targets: list[WaiverTarget] = []
    for pid in shortlist:
        player = players.get(pid)
        if not player:
            continue
        gain = optimize(league, my_ids + [pid], week).projected_total - baseline
        proj = next_week.get(pid)
        ros = fa_ros.get(pid, 0.0)
        vor = fa_vor.get(pid, 0.0)

        reasons = []
        if gain > 0.5:
            reasons.append(f"upgrades your week {week} lineup by {gain:.1f} pts")
        if trends.get(pid, 0) > 5000:
            reasons.append(f"{trends[pid]:,} adds league-wide in 48h")
        if vor > 15:
            reasons.append(
                f"{vor:.0f} pts above a replacement {player.position} over {settings.ros_horizon_weeks} weeks"
            )
        if player.is_questionable:
            reasons.append("carries an injury tag, check status before claiming")

        targets.append(
            WaiverTarget(
                player_id=pid,
                name=player.name,
                position=player.position,
                team=player.team,
                next_week_points=proj.points if proj else 0.0,
                ros_points=ros,
                ros_vor=vor,
                lineup_gain=gain,
                trend_adds=trends.get(pid, 0),
                opponent=proj.opponent if proj else None,
                reason="; ".join(reasons) or "depth and bye week insurance",
            )
        )

    # Rank on immediate lineup impact first, positional value over replacement
    # as the tiebreak. Raw point totals are never comparable across positions.
    targets.sort(key=lambda t: -(t.lineup_gain * 3 + t.ros_vor * 0.25))
    top = targets[:top_n]

    budget = league.faab_budget
    spent = _faab_spent(league, roster)
    remaining = max(budget - spent, 0) if budget else 0
    for t in top:
        t.suggested_faab_pct = _faab_pct(t, week, league)
        t.suggested_faab = int(round(remaining * t.suggested_faab_pct / 100)) if remaining else 0

    result = {
        "week": week,
        "waiver_type": league.waiver_type,
        "faab_remaining": remaining if budget else None,
        "current_projected_lineup": round(baseline, 2),
        "targets": [t.as_dict() for t in top],
        "drop_candidates": drop_candidates(league, roster, week),
    }
    if log:
        log_recommendation(league.league_id, league.season, week, "waiver", None, result)
    return result


def _faab_pct(target: WaiverTarget, week: int, league: League) -> float:
    """Bid as a share of remaining budget, scaled to real lineup impact."""
    gain = target.lineup_gain
    if gain >= 6:
        base = 35.0
    elif gain >= 4:
        base = 22.0
    elif gain >= 2.5:
        base = 12.0
    elif gain >= 1:
        base = 5.0
    else:
        base = 1.0
    # Competition premium: heavy league-wide interest means you must bid up.
    if target.trend_adds > 30000:
        base *= 1.5
    elif target.trend_adds > 10000:
        base *= 1.25
    # Late season means hoarding budget has less value.
    if week >= league.playoff_week_start - 3:
        base *= 1.3
    return round(min(base, 60.0), 1)


def _faab_spent(league: League, roster: dict) -> int:
    """FAAB already committed this season.

    Sleeper tracks this on the roster, so read it. The fallback below walks 17
    weeks of transactions, which costs 17 round trips on the hot path and
    conflates two different things: `waiver_budget` entries are FAAB *traded* to
    another team, while `settings.waiver_bid` is what a claim actually cost.
    """
    reported = (roster.get("settings") or {}).get("waiver_budget_used")
    if reported is not None:
        return int(reported or 0)

    rid = roster.get("roster_id")
    spent = 0
    for w in range(1, settings.regular_season_weeks + 1):
        for txn in league.transactions(w):
            if txn.get("status") != "complete":
                continue
            for bid in txn.get("waiver_budget") or []:
                if bid.get("sender") == rid:
                    spent += int(bid.get("amount") or 0)
            settings_blob = txn.get("settings") or {}
            if rid in (txn.get("roster_ids") or []) and settings_blob.get("waiver_bid"):
                spent += int(settings_blob["waiver_bid"])
    return spent


def drop_candidates(league: League, roster: dict, week: int, top_n: int = 4) -> list[dict]:
    """Who on your roster is least likely to ever crack the lineup."""
    my_ids = [p for p in (roster.get("players") or []) if p]
    optimal = optimize(league, my_ids, week)
    starter_ids = {a.player.player_id for a in optimal.assignments if a.player}

    ros = rest_of_season(league, my_ids, week)
    players = load_players(my_ids)
    taxi = set(roster.get("taxi") or [])
    reserve = set(roster.get("reserve") or [])

    # Score bench players against what is freely available at their position,
    # so a fourth WR is not judged against your starting QB.
    fa_ids = free_agents(league)
    fa_players = load_players(fa_ids)
    fa_ros = rest_of_season(league, fa_ids, week) if fa_ids else {}
    combined = {**fa_ros, **ros}
    combined_pos = {
        **{pid: p.position for pid, p in fa_players.items()},
        **{pid: p.position for pid, p in players.items()},
    }
    # Replacement level comes from the free agent pool ALONE. Deriving it from
    # the pool unioned with your own roster made your own players raise the bar
    # they were then measured against, so a deep bench looked droppable purely
    # because it was deep.
    fa_pos = {pid: p.position for pid, p in fa_players.items()}
    baseline = replacement_baseline(league, fa_ros, fa_pos, "waiver")
    vor = positional_vor(league, combined, combined_pos, baseline)

    rows = []
    for pid in my_ids:
        if pid in starter_ids or pid in taxi or pid in reserve:
            continue
        player = players.get(pid)
        if not player:
            continue
        rows.append(
            {
                "player_id": pid,
                "player": player.label(),
                "position": player.position,
                "ros_points": ros.get(pid, 0.0),
                "value_over_replacement": vor.get(pid, 0.0),
                "note": "injured, roster spot is dead weight"
                if player.is_out
                else "replaceable from the wire",
            }
        )
    rows.sort(key=lambda r: r["value_over_replacement"])
    return rows[:top_n]
