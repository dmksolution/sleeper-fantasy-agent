#!/usr/bin/env python3
"""MCP server exposing the fantasy toolkit to Claude.

Register in Claude Desktop or Claude Code, then ask things like:

    "Who should I start at flex this week?"
    "Best waiver adds and what should I bid?"
    "Is trading Puka Nacua for Bijan Robinson a good idea?"
    "Who is the best available player in my draft right now?"

Every tool is read-only against Sleeper.
"""

from __future__ import annotations

import json
from typing import Any

# MCP SDK 2.x renamed FastMCP to MCPServer. Support both so this runs on
# whichever version is installed.
try:
    from mcp.server import MCPServer as _Server  # SDK >= 2.0
except ImportError:  # pragma: no cover
    from mcp.server.fastmcp import FastMCP as _Server  # SDK 1.x

from sleeper_agent import draft as draft_mod
from sleeper_agent import trades as trades_mod
from sleeper_agent.client import client
from sleeper_agent.config import settings
from sleeper_agent.digest import build_digest, league_activity
from sleeper_agent.league import League, load_players
from sleeper_agent.lineup import optimize, start_sit_advice
from sleeper_agent.matchup import bye_week_report, matchup_preview, standings
from sleeper_agent.projections import consistency, rest_of_season, week_projections
from sleeper_agent.sync import current_week, resolve_season, sync_all
from sleeper_agent.waivers import recommend_waivers

mcp = _Server("sleeper-fantasy")


def _ctx(league_id: str | None = None, week: int | None = None):
    league = League(league_id)
    week = week or current_week()
    roster = league.my_roster()
    if roster is None:
        raise ValueError(
            "Could not find your roster in this league. Set SLEEPER_USERNAME in .env."
        )
    return league, roster, week


def _json(payload: Any) -> str:
    return json.dumps(payload, indent=2, default=str)


# ------------------------------------------------------------------ setup


@mcp.tool()
def sync_data(force: bool = False, full_season: bool = True) -> str:
    """Refresh the local cache of players, projections and stats from Sleeper.

    Run once before a session. Everything else reads from the local cache.
    `full_season` pulls all 18 weeks, which is what makes bye weeks, playoff
    week value and full-horizon trade math correct.
    """
    return _json(sync_all(force=force, full_season=full_season))


@mcp.tool()
def data_health(league_id: str = "") -> str:
    """Report whether the cached data can be trusted before you rely on it.

    Returns which projection weeks are cached, whether scoring reconciles
    against Sleeper's own numbers per position, row counts, and explicit
    warnings. Call this first if a recommendation looks surprising, or if you
    need to say how confident an answer is.
    """
    from sleeper_agent.sync import data_health as _health

    return _json(_health(League(league_id or None)))


@mcp.tool()
def find_my_leagues(username: str = "", season: str = "") -> str:
    """List the Sleeper leagues for a username, with their league IDs."""
    username = username or settings.sleeper_username
    season = season or resolve_season()
    user = client.user(username)
    if not user:
        return _json({"error": f"no Sleeper user named {username}"})
    leagues = client.user_leagues(user["user_id"], season)
    return _json(
        [
            {
                "league_id": lg.get("league_id"),
                "name": lg.get("name"),
                "teams": lg.get("total_rosters"),
                "status": lg.get("status"),
                "draft_id": lg.get("draft_id"),
            }
            for lg in leagues
        ]
    )


@mcp.tool()
def league_info(league_id: str = "") -> str:
    """Scoring settings, roster slots, waiver type and playoff schedule."""
    return _json(League(league_id or None).summary())


# ------------------------------------------------------------------ draft


@mcp.tool()
def draft_board(league_id: str = "", position: str = "", top_n: int = 40) -> str:
    """Pre-draft value board ranked by value over replacement, with tiers and ADP.

    Build this before draft night. Set position to QB, RB, WR, TE, K or DEF to
    filter to one position.
    """
    league = League(league_id or None)
    board = draft_mod.value_board(league)
    if position:
        board = [b for b in board if b.position == position.upper()]
    return _json(
        {
            "league": league.name,
            "scoring_format": league.scoring_format(),
            "replacement_levels": draft_mod.replacement_levels(league),
            "board": [b.as_dict() for b in board[:top_n]],
        }
    )


@mcp.tool()
def draft_recommendation(league_id: str = "", draft_id: str = "", top_n: int = 12) -> str:
    """Who to take with your next pick, accounting for who is already off the board.

    Use this live during the draft. Reflects positional need, tier scarcity and
    how many picks until your turn comes back around.
    """
    league = League(league_id or None)
    return _json(draft_mod.recommend_pick(league, draft_id or None, top_n=top_n))


@mcp.tool()
def draft_results(league_id: str = "", draft_id: str = "") -> str:
    """Post-draft recap: value captured per team, reaches and steals."""
    return _json(draft_mod.draft_recap(League(league_id or None), draft_id or None))


# ----------------------------------------------------------------- weekly


@mcp.tool()
def optimal_lineup(league_id: str = "", week: int = 0) -> str:
    """The highest projected legal lineup for your roster this week."""
    league, roster, wk = _ctx(league_id or None, week or None)
    ids = [p for p in (roster.get("players") or []) if p]
    return _json(optimize(league, ids, wk, log=True).as_dict())


@mcp.tool()
def start_sit(league_id: str = "", week: int = 0) -> str:
    """Compare your currently set lineup to the optimal one and list the swaps."""
    league, roster, wk = _ctx(league_id or None, week or None)
    return _json(start_sit_advice(league, roster, wk))


@mcp.tool()
def waiver_targets(league_id: str = "", week: int = 0, top_n: int = 10) -> str:
    """Best available free agents ranked by how much they improve your lineup,
    with suggested FAAB bids and drop candidates."""
    league, roster, wk = _ctx(league_id or None, week or None)
    return _json(recommend_waivers(league, roster, wk, top_n=top_n, log=True))


@mcp.tool()
def matchup(league_id: str = "", week: int = 0) -> str:
    """This week's head to head preview with win probability and slot by slot edges."""
    league, roster, wk = _ctx(league_id or None, week or None)
    return _json(matchup_preview(league, roster, wk))


@mcp.tool()
def league_standings(league_id: str = "") -> str:
    """Current standings with points for and against."""
    return _json(standings(League(league_id or None)))


@mcp.tool()
def recent_activity(league_id: str = "", week: int = 0) -> str:
    """Adds, drops and trades by other managers since the last snapshot."""
    league = League(league_id or None)
    return _json(league_activity(league, week or current_week()))


@mcp.tool()
def bye_weeks(league_id: str = "", through_week: int = 14) -> str:
    """Upcoming weeks where three or more of your players are unavailable."""
    league, roster, _ = _ctx(league_id or None, None)
    return _json(bye_week_report(league, roster, through_week))


# ----------------------------------------------------------------- trades


@mcp.tool()
def evaluate_trade(
    send: str,
    receive: str,
    league_id: str = "",
    week: int = 0,
    partner_roster_id: int = 0,
) -> str:
    """Evaluate a proposed trade. Pass comma separated player names.

    Example: send="Puka Nacua", receive="Bijan Robinson, Jaylen Waddle"
    Reports the change in expected starting lineup points for you and, if
    partner_roster_id is given, for the other manager too.
    """
    league, roster, wk = _ctx(league_id or None, week or None)
    send_ids = [i for i in (trades_mod.resolve_player(n) for n in send.split(",")) if i]
    recv_ids = [i for i in (trades_mod.resolve_player(n) for n in receive.split(",")) if i]
    return _json(
        trades_mod.evaluate_trade(
            league,
            roster,
            send_ids,
            recv_ids,
            wk,
            partner_roster_id or None,
            log=True,
        )
    )


@mcp.tool()
def trade_targets(league_id: str = "", week: int = 0, top_n: int = 5) -> str:
    """Find managers whose bench surplus fills your weakest starting slots."""
    league, roster, wk = _ctx(league_id or None, week or None)
    return _json(trades_mod.find_trade_targets(league, roster, wk, top_n=top_n))


# ---------------------------------------------------------------- players


@mcp.tool()
def player_outlook(name: str, league_id: str = "", week: int = 0) -> str:
    """Projection, injury status, rest of season value and week to week
    consistency for one player, scored in your league's format."""
    league, _, wk = _ctx(league_id or None, week or None)
    pid = trades_mod.resolve_player(name)
    if not pid:
        return _json({"error": f"no player matching '{name}'"})
    player = load_players([pid]).get(pid)
    proj = week_projections(league, wk).get(pid)
    return _json(
        {
            "player": player.label() if player else pid,
            "player_id": pid,
            "week": wk,
            "projected_points": proj.points if proj else None,
            "opponent": proj.opponent if proj else None,
            "injury_status": player.injury_status if player else None,
            "rest_of_season": rest_of_season(league, [pid], wk).get(pid),
            "consistency": consistency(league, pid, wk),
        }
    )


@mcp.tool()
def trending_players(kind: str = "add", hours: int = 24, limit: int = 15) -> str:
    """Most added or dropped players across all of Sleeper. kind is add or drop."""
    rows = client.trending(kind, lookback_hours=hours, limit=limit)
    players = load_players([r["player_id"] for r in rows])
    return _json(
        [
            {
                "player": players[r["player_id"]].label()
                if r["player_id"] in players
                else r["player_id"],
                "count": r.get("count"),
            }
            for r in rows
        ]
    )


# ----------------------------------------------------------------- digest


@mcp.tool()
def weekly_brief(league_id: str = "", week: int = 0) -> str:
    """The full weekly report: matchup, lineup changes, waivers, trades, standings."""
    return build_digest(league_id or None, week or None)


if __name__ == "__main__":
    import os

    # stdio is the right choice for local use with Claude Desktop or Claude Code.
    # http is only for the optional networked container in docker-compose.
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    if transport in ("http", "streamable-http"):
        mcp.settings.host = os.environ.get("MCP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.environ.get("MCP_PORT", "8848"))
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
