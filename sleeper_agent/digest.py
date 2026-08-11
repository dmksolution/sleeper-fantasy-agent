"""The scheduled weekly report.

Run this from cron. It syncs, then assembles one markdown brief covering the
matchup, lineup changes, waiver targets, league activity and bye week planning,
and pushes it to a webhook or ntfy topic.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import requests

from .config import settings
from .league import League, load_players
from .lineup import start_sit_advice
from .matchup import bye_week_report, matchup_preview, standings
from .store import previous_roster, snapshot_rosters, utcnow
from .sync import current_week, sync_all
from .trades import find_trade_targets
from .waivers import recommend_waivers


def _fmt_pts(v) -> str:
    return f"{v:.1f}" if isinstance(v, (int, float)) else str(v)


def build_digest(league_id: str | None = None, week: int | None = None) -> str:
    sync_all()
    league = League(league_id)
    week = week or current_week()
    roster = league.my_roster()
    if not roster:
        return (
            "Could not identify your roster. Set SLEEPER_USERNAME in .env to the "
            "username you use in Sleeper."
        )

    lines: list[str] = []
    now = datetime.now(timezone.utc).strftime("%A %b %d, %Y")
    lines.append(f"# {league.name} — Week {week} Brief")
    lines.append(f"_{now}_\n")

    # ---------------------------------------------------------- matchup
    preview = matchup_preview(league, roster, week)
    if "error" not in preview:
        lines.append("## Matchup")
        lines.append(
            f"**{preview['me']}** {_fmt_pts(preview['my_projected'])} "
            f"vs **{preview['opponent']}** {_fmt_pts(preview['opponent_projected'])}"
        )
        lines.append(
            f"Win probability **{preview['win_probability_pct']}%**. {preview['verdict']}\n"
        )
        edges = sorted(preview["positional_edges"], key=lambda e: e["edge"])
        if edges:
            worst = edges[0]
            best = edges[-1]
            lines.append(
                f"Biggest edge: {best['slot']} (+{best['edge']}). "
                f"Biggest hole: {worst['slot']} ({worst['edge']}).\n"
            )

    # ------------------------------------------------------- start / sit
    advice = start_sit_advice(league, roster, week)
    lines.append("## Lineup")
    if advice["points_left_on_bench"] > 0.5:
        lines.append(
            f"You are leaving **{_fmt_pts(advice['points_left_on_bench'])} points** "
            f"on the bench as currently set.\n"
        )
        for s in advice["start"]:
            lines.append(f"- **Start** {s['player']} in {s['slot']} ({_fmt_pts(s['points'])})")
        for s in advice["sit"]:
            reason = f" — {s['reason']}" if s["reason"] else ""
            lines.append(f"- **Sit** {s['player']} ({_fmt_pts(s['points'])}){reason}")
        lines.append("")
    else:
        lines.append("Lineup is already optimal against current projections.\n")

    lines.append("| Slot | Player | Proj | Opp |")
    lines.append("|---|---|---|---|")
    for s in advice["optimal_lineup"]["starters"]:
        lines.append(
            f"| {s['slot']} | {s['player']} | {_fmt_pts(s['points'])} | {s['opponent'] or '-'} |"
        )
    lines.append("")

    # ---------------------------------------------------------- waivers
    waivers = recommend_waivers(league, roster, week, top_n=6)
    lines.append("## Waiver wire")
    if waivers.get("faab_remaining") is not None:
        lines.append(f"FAAB remaining: **${waivers['faab_remaining']}**\n")
    if waivers["targets"]:
        lines.append("| Player | Next wk | ROS | Lineup gain | Bid | Why |")
        lines.append("|---|---|---|---|---|---|")
        for t in waivers["targets"]:
            lines.append(
                f"| {t['player']} | {t['next_week_points']} | {t['ros_points']} | "
                f"+{t['starting_lineup_gain']} | {t['suggested_faab']} | {t['reason']} |"
            )
        lines.append("")
    if waivers.get("drop_candidates"):
        drops = ", ".join(d["player"] for d in waivers["drop_candidates"])
        lines.append(f"Drop candidates: {drops}\n")

    # --------------------------------------------------- league activity
    activity = league_activity(league, week)
    if activity:
        lines.append("## League activity since last check")
        for a in activity:
            lines.append(f"- {a}")
        lines.append("")

    # ----------------------------------------------------- trade targets
    targets = find_trade_targets(league, roster, week, top_n=3)
    if targets["opportunities"]:
        lines.append("## Trade angles")
        lines.append(f"{targets['guidance']}.\n")
        for opp in targets["opportunities"]:
            best = opp["buy_low_candidates"][0]
            lines.append(
                f"- **{opp['team']}** is benching {best['player']}, "
                f"worth +{best['your_weekly_gain']} per week to you"
            )
        lines.append("")

    # -------------------------------------------------------- bye weeks
    byes = bye_week_report(league, roster, through_week=14)
    if byes["weeks_with_three_or_more_out"]:
        lines.append("## Bye week planning")
        for b in byes["weeks_with_three_or_more_out"][:3]:
            lines.append(f"- Week {b['week']}: {b['count']} players unavailable")
        lines.append("")

    # ------------------------------------------------------- standings
    table = standings(league)
    lines.append("## Standings")
    lines.append("| # | Team | Record | PF |")
    lines.append("|---|---|---|---|")
    for row in table:
        lines.append(
            f"| {row['rank']} | {row['team']} | {row['wins']}-{row['losses']} | {row['points_for']} |"
        )

    snapshot_rosters(league.league_id, league.season, week, league.rosters())
    return "\n".join(lines)


def league_activity(league: League, week: int) -> list[str]:
    """Diff each roster against the last snapshot to catch adds, drops, trades."""
    out: list[str] = []
    now = utcnow()
    names = league.owner_names()
    for roster in league.rosters():
        rid = roster.get("roster_id")
        current = set(roster.get("players") or [])
        prior = previous_roster(league.league_id, rid, now)
        if prior is None:
            continue
        prior_set = set(prior)
        added = current - prior_set
        dropped = prior_set - current
        if not added and not dropped:
            continue
        team = names.get(roster.get("owner_id"), f"Roster {rid}")
        players = load_players(list(added | dropped))
        if added:
            out.append(
                f"{team} added " + ", ".join(
                    players[p].name for p in added if p in players
                )
            )
        if dropped:
            out.append(
                f"{team} dropped " + ", ".join(
                    players[p].name for p in dropped if p in players
                )
            )
    return out


def notify(text: str, title: str = "Fantasy brief") -> dict:
    """Push the digest out. Supports a generic JSON webhook and ntfy."""
    results = {}
    if settings.webhook_url:
        try:
            r = requests.post(
                settings.webhook_url,
                json={"text": text, "title": title},
                timeout=20,
            )
            results["webhook"] = r.status_code
        except Exception as exc:  # noqa: BLE001
            results["webhook"] = f"error: {exc}"
    if settings.ntfy_topic:
        try:
            r = requests.post(
                f"{settings.ntfy_server.rstrip('/')}/{settings.ntfy_topic}",
                data=text.encode("utf-8"),
                headers={"Title": title, "Markdown": "yes"},
                timeout=20,
            )
            results["ntfy"] = r.status_code
        except Exception as exc:  # noqa: BLE001
            results["ntfy"] = f"error: {exc}"
    if not results:
        results["note"] = "no DIGEST_WEBHOOK_URL or NTFY_TOPIC configured, printed only"
    return results


def digest_json(league_id: str | None = None, week: int | None = None) -> str:
    """Structured version of the same brief, for feeding another agent."""
    league = League(league_id)
    week = week or current_week()
    roster = league.my_roster() or {}
    payload = {
        "league": league.summary(),
        "week": week,
        "matchup": matchup_preview(league, roster, week),
        "lineup": start_sit_advice(league, roster, week),
        "waivers": recommend_waivers(league, roster, week),
        "trades": find_trade_targets(league, roster, week),
        "standings": standings(league),
    }
    return json.dumps(payload, indent=2)
