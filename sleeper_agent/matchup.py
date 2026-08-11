"""Head to head matchup analysis and standings context."""

from __future__ import annotations

import math

from .league import League, load_players
from .lineup import optimize
from .projections import week_projections


def _win_probability(my_pts: float, opp_pts: float, sigma: float = 26.0) -> float:
    """Normal approximation on the margin.

    Weekly fantasy scores have a standard deviation around 18 to 20 points per
    team, so the margin between two teams runs near 26. Good enough to separate
    a coin flip from a real edge, which is all this needs to do.
    """
    if sigma <= 0:
        return 50.0
    margin = my_pts - opp_pts
    return round(100 * 0.5 * (1 + math.erf(margin / (sigma * math.sqrt(2)))), 1)


def matchup_preview(league: League, roster: dict, week: int) -> dict:
    matchups = league.matchups(week)
    my_rid = roster.get("roster_id")
    mine = next((m for m in matchups if m.get("roster_id") == my_rid), None)
    if not mine:
        return {"week": week, "error": "no matchup found, this may be a bye or off week"}

    mid = mine.get("matchup_id")
    opp = next(
        (m for m in matchups if m.get("matchup_id") == mid and m.get("roster_id") != my_rid),
        None,
    )
    if not opp:
        return {"week": week, "error": "no opponent assigned for this week"}

    opp_roster = next(
        (r for r in league.rosters() if r.get("roster_id") == opp.get("roster_id")), {}
    )

    my_opt = optimize(league, [p for p in (roster.get("players") or []) if p], week)
    opp_opt = optimize(league, [p for p in (opp_roster.get("players") or []) if p], week)

    win_pct = _win_probability(my_opt.projected_total, opp_opt.projected_total)

    return {
        "week": week,
        "me": league.roster_name(my_rid),
        "opponent": league.roster_name(opp.get("roster_id")),
        "my_projected": my_opt.projected_total,
        "opponent_projected": opp_opt.projected_total,
        "margin": round(my_opt.projected_total - opp_opt.projected_total, 2),
        "win_probability_pct": win_pct,
        "verdict": _verdict(win_pct),
        "my_lineup": my_opt.as_dict()["starters"],
        "opponent_lineup": opp_opt.as_dict()["starters"],
        "positional_edges": _positional_edges(my_opt, opp_opt),
    }


def _verdict(win_pct: float) -> str:
    if win_pct >= 70:
        return "comfortable favorite, do not over-manage this lineup"
    if win_pct >= 55:
        return "slight favorite, play your projections straight"
    if win_pct >= 45:
        return "coin flip, this is where a good flex call decides the week"
    if win_pct >= 30:
        return "underdog, prioritize ceiling over floor in flex spots"
    return "heavy underdog, swing for variance and start the boom-bust options"


def _positional_edges(my_opt, opp_opt) -> list[dict]:
    def group(opt):
        out: dict[str, float] = {}
        for a in opt.assignments:
            out[a.slot] = out.get(a.slot, 0.0) + a.points
        return out

    mine, theirs = group(my_opt), group(opp_opt)
    slots = sorted(set(mine) | set(theirs))
    return [
        {
            "slot": s,
            "me": round(mine.get(s, 0), 1),
            "opponent": round(theirs.get(s, 0), 1),
            "edge": round(mine.get(s, 0) - theirs.get(s, 0), 1),
        }
        for s in slots
    ]


def standings(league: League) -> list[dict]:
    names = league.owner_names()
    rows = []
    for r in league.rosters():
        s = r.get("settings") or {}
        rows.append(
            {
                "roster_id": r.get("roster_id"),
                "team": names.get(r.get("owner_id"), f"Roster {r.get('roster_id')}"),
                "wins": s.get("wins", 0),
                "losses": s.get("losses", 0),
                "ties": s.get("ties", 0),
                "points_for": round(
                    float(s.get("fpts", 0)) + float(s.get("fpts_decimal", 0)) / 100, 2
                ),
                "points_against": round(
                    float(s.get("fpts_against", 0))
                    + float(s.get("fpts_against_decimal", 0)) / 100,
                    2,
                ),
                "waiver_budget_used": s.get("waiver_budget_used", 0),
            }
        )
    rows.sort(key=lambda r: (-r["wins"], -r["points_for"]))
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    return rows


def bye_week_report(league: League, roster: dict, through_week: int = 14) -> dict:
    """Which upcoming weeks leave you short at a position."""
    my_ids = [p for p in (roster.get("players") or []) if p]
    players = load_players(my_ids)
    trouble = []
    for week in range(1, through_week + 1):
        proj = week_projections(league, week)
        if not proj:
            continue
        on_bye = [
            players[pid].label()
            for pid in my_ids
            if pid in players and (pid not in proj or not proj[pid].has_game)
        ]
        if len(on_bye) >= 3:
            trouble.append({"week": week, "players_out": on_bye, "count": len(on_bye)})
    return {
        "weeks_with_three_or_more_out": trouble,
        "note": "plan waiver claims one to two weeks ahead of these",
    }
