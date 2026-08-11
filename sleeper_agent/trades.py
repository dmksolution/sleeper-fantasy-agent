"""Trade evaluation and trade target discovery.

A trade is not fair or unfair in the abstract. It is good or bad for *your*
starting lineup between now and the playoffs. So the evaluator simulates both
rosters before and after, re-optimizing the lineup each week across the horizon,
and reports the change in expected starting points for each side.
"""

from __future__ import annotations

from .config import settings
from .league import League, load_players
from .lineup import optimize
from .projections import rest_of_season, week_projections
from .store import log_recommendation


def _starting_points_over_horizon(
    league: League, player_ids: list[str], start_week: int, horizon: int
) -> float:
    total = 0.0
    end = min(start_week + horizon, settings.regular_season_weeks + 1)
    for week in range(start_week, end):
        if not week_projections(league, week):
            continue
        total += optimize(league, player_ids, week).projected_total
    return round(total, 2)


def resolve_player(query: str) -> str | None:
    """Look up a player_id from a name fragment."""
    from .store import connect

    q = query.strip().lower().replace(".", "").replace("'", "")
    with connect() as conn:
        row = conn.execute(
            "SELECT player_id FROM players WHERE search_name = ? AND active = 1 LIMIT 1",
            (q,),
        ).fetchone()
        if row:
            return row["player_id"]
        row = conn.execute(
            "SELECT player_id FROM players WHERE search_name LIKE ? AND active = 1"
            " ORDER BY LENGTH(full_name) LIMIT 1",
            (f"%{q}%",),
        ).fetchone()
    return row["player_id"] if row else None


def evaluate_trade(
    league: League,
    my_roster: dict,
    send_ids: list[str],
    receive_ids: list[str],
    week: int,
    partner_roster_id: int | None = None,
    horizon: int | None = None,
    log: bool = False,
) -> dict:
    horizon = horizon or settings.ros_horizon_weeks
    my_ids = [p for p in (my_roster.get("players") or []) if p]

    send = [p for p in send_ids if p in my_ids]
    unknown = [p for p in send_ids if p not in my_ids]

    my_after = [p for p in my_ids if p not in send] + list(receive_ids)

    before = _starting_points_over_horizon(league, my_ids, week, horizon)
    after = _starting_points_over_horizon(league, my_after, week, horizon)

    players = load_players(send_ids + receive_ids)
    ros = rest_of_season(league, send_ids + receive_ids, week, horizon)

    result = {
        "week": week,
        "horizon_weeks": horizon,
        "you_send": [
            {"player": players[p].label() if p in players else p, "ros_points": ros.get(p, 0)}
            for p in send_ids
        ],
        "you_receive": [
            {"player": players[p].label() if p in players else p, "ros_points": ros.get(p, 0)}
            for p in receive_ids
        ],
        "raw_points_swing": round(
            sum(ros.get(p, 0) for p in receive_ids) - sum(ros.get(p, 0) for p in send_ids), 2
        ),
        "starting_lineup_before": before,
        "starting_lineup_after": after,
        "net_starting_points": round(after - before, 2),
        "roster_slots_change": len(receive_ids) - len(send_ids),
    }
    result["verdict"] = _trade_verdict(result["net_starting_points"], horizon)
    if unknown:
        result["warning"] = f"not on your roster: {unknown}"

    if partner_roster_id is not None:
        partner = next(
            (r for r in league.rosters() if r.get("roster_id") == partner_roster_id), None
        )
        if partner:
            p_ids = [p for p in (partner.get("players") or []) if p]
            p_after = [p for p in p_ids if p not in receive_ids] + list(send_ids)
            p_before_pts = _starting_points_over_horizon(league, p_ids, week, horizon)
            p_after_pts = _starting_points_over_horizon(league, p_after, week, horizon)
            result["partner"] = {
                "team": league.roster_name(partner_roster_id),
                "net_starting_points": round(p_after_pts - p_before_pts, 2),
            }
            result["likely_accepted"] = (
                result["partner"]["net_starting_points"] > 0
            )

    if log:
        log_recommendation(league.league_id, league.season, week, "trade", None, result)
    return result


def _trade_verdict(net: float, horizon: int) -> str:
    per_week = net / max(horizon, 1)
    if per_week >= 3:
        return "clear win, accept"
    if per_week >= 1:
        return "modest win, worth doing"
    if per_week > -1:
        return "roughly neutral, decide on roster construction and bye weeks"
    if per_week > -3:
        return "modest loss, ask for more"
    return "clear loss, decline"


def find_trade_targets(league: League, my_roster: dict, week: int, top_n: int = 5) -> dict:
    """Find teams whose surplus matches your need.

    Surplus means good players who are not cracking that team's starting lineup.
    Those are the players their manager is most willing to move.
    """
    my_ids = [p for p in (my_roster.get("players") or []) if p]
    my_rid = my_roster.get("roster_id")
    my_optimal = optimize(league, my_ids, week)

    my_weak_slots = sorted(
        [(a.slot, a.points) for a in my_optimal.assignments], key=lambda t: t[1]
    )[:3]
    weak_slot_names = [s for s, _ in my_weak_slots]

    opportunities = []
    for roster in league.rosters():
        rid = roster.get("roster_id")
        if rid == my_rid:
            continue
        their_ids = [p for p in (roster.get("players") or []) if p]
        if not their_ids:
            continue
        their_opt = optimize(league, their_ids, week)
        starters = {a.player.player_id for a in their_opt.assignments if a.player}
        surplus_ids = [p for p in their_ids if p not in starters]
        if not surplus_ids:
            continue

        ros = rest_of_season(league, surplus_ids, week)
        players = load_players(surplus_ids)
        buy_low = []
        for pid in sorted(surplus_ids, key=lambda p: -ros.get(p, 0))[:4]:
            player = players.get(pid)
            if not player:
                continue
            gain = optimize(league, my_ids + [pid], week).projected_total - my_optimal.projected_total
            if gain <= 0.2:
                continue
            buy_low.append(
                {
                    "player": player.label(),
                    "player_id": pid,
                    "their_bench_value": ros.get(pid, 0),
                    "your_weekly_gain": round(gain, 2),
                }
            )
        if buy_low:
            opportunities.append(
                {
                    "team": league.roster_name(rid),
                    "roster_id": rid,
                    "buy_low_candidates": buy_low,
                    "best_gain": max(b["your_weekly_gain"] for b in buy_low),
                }
            )

    opportunities.sort(key=lambda o: -o["best_gain"])
    return {
        "week": week,
        "your_weakest_slots": [
            {"slot": s, "projected": round(p, 1)} for s, p in my_weak_slots
        ],
        "guidance": f"target upgrades at {', '.join(weak_slot_names)}",
        "opportunities": opportunities[:top_n],
    }
