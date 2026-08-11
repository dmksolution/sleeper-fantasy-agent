#!/usr/bin/env python3
"""Command line interface. Everything the MCP server does, from a terminal.

    python cli.py setup --username <your_sleeper_username>
    python cli.py sync --full
    python cli.py board --position RB --top 30
    python cli.py draft
    python cli.py lineup
    python cli.py waivers
    python cli.py digest --notify
"""

from __future__ import annotations

import argparse
import json
import sys

from sleeper_agent import draft as draft_mod
from sleeper_agent import trades as trades_mod
from sleeper_agent.client import client
from sleeper_agent.config import settings
from sleeper_agent.digest import build_digest, digest_json, notify
from sleeper_agent.league import League
from sleeper_agent.lineup import optimize, start_sit_advice
from sleeper_agent.matchup import bye_week_report, matchup_preview, standings
from sleeper_agent.store import init_db
from sleeper_agent.sync import current_week, resolve_season, sync_all
from sleeper_agent.trades import evaluate_trade, find_trade_targets
from sleeper_agent.waivers import recommend_waivers


def out(payload) -> None:
    if isinstance(payload, str):
        print(payload)
    else:
        print(json.dumps(payload, indent=2, default=str))


def ctx(args):
    league = League(getattr(args, "league", "") or None)
    week = getattr(args, "week", 0) or current_week()
    roster = league.my_roster()
    if roster is None:
        sys.exit(
            "Could not find your roster. Set SLEEPER_USERNAME in .env, or pass "
            "--league with the right league id."
        )
    return league, roster, week


def cmd_setup(args):
    init_db()
    username = args.username or settings.sleeper_username
    if not username:
        sys.exit("Pass --username with your Sleeper username")
    user = client.user(username)
    if not user:
        sys.exit(f"No Sleeper user named {username}")
    season = args.season or resolve_season()
    leagues = client.user_leagues(user["user_id"], season)
    print(f"User {username} (user_id {user['user_id']}), season {season}\n")
    if not leagues:
        print("No leagues found for that season.")
        return
    for lg in leagues:
        print(f"  {lg.get('name')}")
        print(f"    SLEEPER_LEAGUE_ID={lg.get('league_id')}")
        if lg.get("draft_id"):
            print(f"    SLEEPER_DRAFT_ID={lg.get('draft_id')}")
        print(f"    teams={lg.get('total_rosters')} status={lg.get('status')}\n")
    print("Copy the values above into your .env file, then run: python cli.py sync --full")


def cmd_sync(args):
    out(sync_all(force=args.full, weeks_ahead=args.weeks))


def cmd_info(args):
    out(League(args.league or None).summary())


def cmd_board(args):
    league = League(args.league or None)
    board = draft_mod.value_board(league)
    if args.position:
        board = [b for b in board if b.position == args.position.upper()]
    rows = [b.as_dict() for b in board[: args.top]]
    if args.json:
        out({"replacement_levels": draft_mod.replacement_levels(league), "board": rows})
        return
    print(f"{league.name} — {league.scoring_format().upper()} value board\n")
    print(f"{'#':<4}{'Player':<32}{'Pos':<5}{'Tier':<6}{'Proj':>7}{'VBD':>8}{'ADP':>8}")
    print("-" * 70)
    for i, r in enumerate(rows, start=1):
        adp = f"{r['adp']:.1f}" if r["adp"] else "-"
        print(
            f"{i:<4}{r['player'][:31]:<32}{r['position']:<5}{r['tier']:<6}"
            f"{r['projected_points']:>7.1f}{r['vbd']:>8.1f}{adp:>8}"
        )


def cmd_draft(args):
    league = League(args.league or None)
    result = draft_mod.recommend_pick(league, args.draft_id or None, top_n=args.top)
    if args.json:
        out(result)
        return
    print(f"Draft status: {result['draft_status']}, {result['picks_made']} picks made")
    if result.get("picks_until_my_turn") is not None:
        print(f"Picks until your turn: {result['picks_until_my_turn']}")
    if result["my_roster_so_far"]:
        print("Your picks: " + ", ".join(result["my_roster_so_far"]))
    print(f"Needs: {result['positional_needs']}\n")
    print(f"{'Player':<34}{'Pos':<5}{'Tier':<6}{'VBD':>7}{'Score':>8}{'ADP':>8}")
    print("-" * 68)
    for r in result["recommendations"]:
        adp = f"{r['adp']:.1f}" if r["adp"] else "-"
        print(
            f"{r['player'][:33]:<34}{r['position']:<5}{r['tier']:<6}"
            f"{r['vbd']:>7.1f}{r['adjusted_score']:>8.1f}{adp:>8}"
        )


def cmd_recap(args):
    out(draft_mod.draft_recap(League(args.league or None), args.draft_id or None))


def cmd_lineup(args):
    league, roster, week = ctx(args)
    ids = [p for p in (roster.get("players") or []) if p]
    result = optimize(league, ids, week, log=True).as_dict()
    if args.json:
        out(result)
        return
    print(f"Week {week} optimal lineup — projected {result['projected_total']}\n")
    for s in result["starters"]:
        note = f"  ({s['note']})" if s["note"] else ""
        print(f"  {s['slot']:<12}{s['player'][:36]:<38}{s['points']:>6.1f}{note}")
    print("\nBench:")
    for b in result["bench"][:8]:
        print(f"  {'':<12}{b['player'][:36]:<38}{b['points']:>6.1f}")


def cmd_startsit(args):
    league, roster, week = ctx(args)
    out(start_sit_advice(league, roster, week))


def cmd_waivers(args):
    league, roster, week = ctx(args)
    result = recommend_waivers(league, roster, week, top_n=args.top, log=True)
    if args.json:
        out(result)
        return
    if result.get("faab_remaining") is not None:
        print(f"FAAB remaining: ${result['faab_remaining']}\n")
    for t in result["targets"]:
        print(f"  {t['player']}")
        print(
            f"      next wk {t['next_week_points']}, ROS {t['ros_points']}, "
            f"lineup gain +{t['starting_lineup_gain']}, bid {t['suggested_faab']}"
        )
        print(f"      {t['reason']}\n")
    if result.get("drop_candidates"):
        print("Drop candidates: " + ", ".join(d["player"] for d in result["drop_candidates"]))


def cmd_matchup(args):
    league, roster, week = ctx(args)
    out(matchup_preview(league, roster, week))


def cmd_standings(args):
    out(standings(League(args.league or None)))


def cmd_byes(args):
    league, roster, _ = ctx(args)
    out(bye_week_report(league, roster, args.through))


def cmd_trade(args):
    league, roster, week = ctx(args)
    send = [i for i in (trades_mod.resolve_player(n) for n in args.send.split(",")) if i]
    recv = [i for i in (trades_mod.resolve_player(n) for n in args.receive.split(",")) if i]
    out(evaluate_trade(league, roster, send, recv, week, args.partner or None, log=True))


def cmd_trade_targets(args):
    league, roster, week = ctx(args)
    out(find_trade_targets(league, roster, week, top_n=args.top))


def cmd_player(args):
    from sleeper_agent.league import load_players
    from sleeper_agent.projections import consistency, rest_of_season, week_projections

    league, _, week = ctx(args)
    pid = trades_mod.resolve_player(args.name)
    if not pid:
        sys.exit(f"No player matching '{args.name}'")
    player = load_players([pid]).get(pid)
    proj = week_projections(league, week).get(pid)
    out(
        {
            "player": player.label() if player else pid,
            "week": week,
            "projected": proj.points if proj else None,
            "opponent": proj.opponent if proj else None,
            "rest_of_season": rest_of_season(league, [pid], week).get(pid),
            "consistency": consistency(league, pid, week),
        }
    )


def cmd_digest(args):
    if args.json:
        out(digest_json(args.league or None, args.week or None))
        return
    text = build_digest(args.league or None, args.week or None)
    print(text)
    if args.notify:
        print("\n---\nnotify:", json.dumps(notify(text)))


def main():
    p = argparse.ArgumentParser(description="Sleeper fantasy football agent")
    p.add_argument("--league", default="", help="league id (defaults to .env)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("setup", help="find your leagues and print .env values")
    s.add_argument("--username", default="")
    s.add_argument("--season", default="")
    s.set_defaults(func=cmd_setup)

    s = sub.add_parser("sync", help="refresh the local cache")
    s.add_argument("--full", action="store_true", help="ignore TTLs and refetch")
    s.add_argument("--weeks", type=int, default=4, help="weeks of projections to pull")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("info", help="league settings summary")
    s.set_defaults(func=cmd_info)

    s = sub.add_parser("board", help="pre-draft value board")
    s.add_argument("--position", default="")
    s.add_argument("--top", type=int, default=40)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_board)

    s = sub.add_parser("draft", help="live draft recommendation")
    s.add_argument("--draft-id", dest="draft_id", default="")
    s.add_argument("--top", type=int, default=12)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_draft)

    s = sub.add_parser("recap", help="post-draft recap")
    s.add_argument("--draft-id", dest="draft_id", default="")
    s.set_defaults(func=cmd_recap)

    s = sub.add_parser("lineup", help="optimal lineup")
    s.add_argument("--week", type=int, default=0)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_lineup)

    s = sub.add_parser("startsit", help="swaps vs your current lineup")
    s.add_argument("--week", type=int, default=0)
    s.set_defaults(func=cmd_startsit)

    s = sub.add_parser("waivers", help="waiver targets and FAAB bids")
    s.add_argument("--week", type=int, default=0)
    s.add_argument("--top", type=int, default=10)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_waivers)

    s = sub.add_parser("matchup", help="this week's head to head")
    s.add_argument("--week", type=int, default=0)
    s.set_defaults(func=cmd_matchup)

    s = sub.add_parser("standings", help="league standings")
    s.set_defaults(func=cmd_standings)

    s = sub.add_parser("byes", help="bye week trouble spots")
    s.add_argument("--through", type=int, default=14)
    s.set_defaults(func=cmd_byes)

    s = sub.add_parser("trade", help="evaluate a trade")
    s.add_argument("--send", required=True, help="comma separated names")
    s.add_argument("--receive", required=True, help="comma separated names")
    s.add_argument("--partner", type=int, default=0, help="partner roster_id")
    s.add_argument("--week", type=int, default=0)
    s.set_defaults(func=cmd_trade)

    s = sub.add_parser("targets", help="find trade partners")
    s.add_argument("--week", type=int, default=0)
    s.add_argument("--top", type=int, default=5)
    s.set_defaults(func=cmd_trade_targets)

    s = sub.add_parser("player", help="single player outlook")
    s.add_argument("name")
    s.add_argument("--week", type=int, default=0)
    s.set_defaults(func=cmd_player)

    s = sub.add_parser("digest", help="full weekly brief")
    s.add_argument("--week", type=int, default=0)
    s.add_argument("--notify", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_digest)

    args = p.parse_args()
    try:
        args.func(args)
    except RuntimeError as exc:
        # Missing configuration and unknown league ids are the user's problem to
        # fix, not bugs, so show the guidance instead of a stack trace.
        sys.exit(str(exc))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
