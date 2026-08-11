"""JSON adapters over the analysis modules.

Everything here returns plain JSON-safe dicts and never raises for an expected
condition. The web UI has to render *something* useful in every state, including
the ones that are normal rather than exceptional -- an empty roster before the
draft, a draft order the commissioner has not published, a season with no games
played yet. Those come back as structured `unavailable` payloads with a reason
and a suggested next action, not as 500s.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from sleeper_agent import draft as draft_mod
from sleeper_agent import trades as trades_mod
from sleeper_agent.config import settings
from sleeper_agent.digest import build_digest, league_activity
from sleeper_agent.client import client
from sleeper_agent.league import League, load_players
from sleeper_agent.lineup import optimize, start_sit_advice
from sleeper_agent.matchup import bye_week_report, matchup_preview, standings
from sleeper_agent.projections import consistency, rest_of_season, week_projections
from sleeper_agent.store import read_conn
from sleeper_agent.sync import current_week, data_health, resolve_season, sync_all
from sleeper_agent.valuation import FULL, season_value, team_bye_weeks
from sleeper_agent.waivers import drop_candidates, recommend_waivers

log = logging.getLogger(__name__)

_LEAGUE: League | None = None
_LEAGUE_AT: float = 0.0
_LEAGUE_TTL = 300.0


def get_league(league_id: str | None = None, refresh: bool = False) -> League:
    """One League per process, refreshed occasionally.

    Constructing a League hits the cache layer and, on a cold process over a
    network share, the disk. The UI makes many small calls, so it pays once.
    """
    global _LEAGUE, _LEAGUE_AT
    if league_id:
        return League(league_id)
    if refresh or _LEAGUE is None or (time.time() - _LEAGUE_AT) > _LEAGUE_TTL:
        _LEAGUE = League()
        _LEAGUE_AT = time.time()
    return _LEAGUE


def unavailable(reason: str, action: str = "", **extra) -> dict:
    return {"unavailable": True, "reason": reason, "action": action, **extra}


def _roster_or_none(league: League) -> dict | None:
    try:
        return league.my_roster()
    except Exception as exc:  # noqa: BLE001
        log.warning("my_roster failed: %s", exc)
        return None


def _need_roster(league: League) -> tuple[dict | None, dict | None]:
    """Returns (roster, unavailable_payload). Exactly one is not None."""
    roster = _roster_or_none(league)
    if roster is None:
        return None, unavailable(
            "Could not find your roster in this league.",
            "Check that SLEEPER_USERNAME in .env matches your Sleeper account.",
        )
    if not [p for p in (roster.get("players") or []) if p]:
        return None, unavailable(
            "Your roster is empty.",
            "This is normal before the draft. Head to the Draft Room.",
            empty_roster=True,
        )
    return roster, None


def _week(league: League, week: int | None) -> int:
    return int(week) if week else current_week()


# --------------------------------------------------------------- bootstrap


def bootstrap() -> dict:
    """Everything the shell needs on first paint."""
    league = get_league()
    state_week = current_week()
    try:
        health = data_health(league)
    except Exception as exc:  # noqa: BLE001
        health = {"warnings": [f"health check failed: {exc}"], "coverage_complete": False}

    roster = _roster_or_none(league)
    roster_size = len([p for p in (roster.get("players") or []) if p]) if roster else 0

    draft = None
    try:
        state = draft_mod.draft_status(league)
        if state:
            draft = {
                "status": state.status,
                "rounds": state.rounds,
                "teams": state.teams,
                "picks_made": state.picks_made,
                "my_slot": state.my_slot,
                "slot_source": state.slot_source,
                "picks_until_my_turn": state.picks_until_my_turn,
                "next_pick_overall": state.next_pick_overall,
                "warnings": state.warnings,
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("draft_status failed: %s", exc)

    phase = "season"
    if draft and draft["status"] in ("pre_draft", "paused"):
        phase = "predraft"
    elif draft and draft["status"] == "drafting":
        phase = "drafting"

    return {
        "league": league.summary(),
        "week": state_week,
        "season": resolve_season(),
        "phase": phase,
        "roster_size": roster_size,
        "draft": draft,
        "health": {
            "coverage_complete": health.get("coverage_complete"),
            "weeks_cached": len(health.get("projection_weeks_cached") or []),
            "warnings": health.get("warnings") or [],
            "byes_detected": health.get("nfl_teams_with_bye_detected"),
        },
        "faab_budget": league.faab_budget,
        "playoff_week_start": league.playoff_week_start,
        "starting_slots": league.starting_slots,
        "bench_size": league.bench_size,
    }


def health() -> dict:
    return data_health(get_league())


def do_sync(full: bool = False, progress: Callable[[str], None] | None = None) -> dict:
    if progress:
        progress("Contacting Sleeper...")
    result = sync_all(force=full)
    if progress:
        progress("Done.")
    get_league(refresh=True)
    return result


# ------------------------------------------------------------------ draft


def board(position: str = "", top: int = 300) -> dict:
    league = get_league()
    items = draft_mod.value_board(league)
    blended = draft_mod.blended_value(league, items)
    if position:
        items = [b for b in items if b.position == position.upper()]
    rows = []
    for i, b in enumerate(items[:top], start=1):
        d = b.as_dict()
        d["overall"] = i
        d["blended"] = blended.get(b.player_id)
        rows.append(d)
    return {
        "replacement_levels": draft_mod.replacement_levels(league),
        "scoring_format": league.scoring_format(),
        "count": len(items),
        "board": rows,
    }


def draft_state(assumed_slot: int | None = None) -> dict:
    league = get_league()
    state = draft_mod.draft_status(league, None, assumed_slot)
    if state is None:
        return unavailable(
            "No draft found for this league.",
            "Set SLEEPER_DRAFT_ID in .env if the league has more than one.",
        )
    players = load_players(state.my_picks) if state.my_picks else {}
    return {
        "draft_id": state.draft_id,
        "status": state.status,
        "rounds": state.rounds,
        "teams": state.teams,
        "pick_type": state.pick_type,
        "picks_made": state.picks_made,
        "my_slot": state.my_slot,
        "slot_source": state.slot_source,
        "on_the_clock_slot": state.on_the_clock_slot,
        "next_pick_overall": state.next_pick_overall,
        "picks_until_my_turn": state.picks_until_my_turn,
        "warnings": state.warnings,
        "my_roster": [
            {"player_id": p.player_id, "label": p.label(), "position": p.position}
            for p in players.values()
        ],
        "recent_picks": [
            {
                "pick_no": p.get("pick_no"),
                "round": p.get("round"),
                "draft_slot": p.get("draft_slot"),
                "player": ((p.get("metadata") or {}).get("first_name", "") + " "
                           + (p.get("metadata") or {}).get("last_name", "")).strip(),
                "position": (p.get("metadata") or {}).get("position"),
                "team": (p.get("metadata") or {}).get("team"),
            }
            for p in sorted(
                state.picks, key=lambda x: x.get("pick_no") or 0, reverse=True
            )[:24]
        ],
    }


# The last completed simulation, so a page refresh mid-draft does not throw
# away a result you are still reading. Keyed by nothing -- there is one of you.
_LAST_SIM: dict | None = None


def draft_simulate(
    candidates: int = 8,
    trials: int = 200,
    assumed_slot: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict:
    from sleeper_agent.draft_sim import build_context, evaluate_candidates

    global _LAST_SIM
    league = get_league()
    if progress:
        progress("Building the value board...")
    ctx = build_context(league)
    if progress:
        progress(f"Simulating {candidates} candidates x {trials} drafts...")
    result = evaluate_candidates(
        league,
        candidates=candidates,
        trials=trials,
        assumed_slot=assumed_slot,
        ctx=ctx,
    )
    result["generated_at"] = time.time()
    _LAST_SIM = result
    return result


def last_simulation() -> dict:
    """Most recent completed simulation, if it is still current.

    Stale once more picks have happened, because the whole point is who is
    still on the board.
    """
    if not _LAST_SIM:
        return {"none": True}
    try:
        state = draft_mod.draft_status(get_league())
        if state and state.picks_made != _LAST_SIM.get("picks_made"):
            return {"none": True, "stale": True}
    except Exception:  # noqa: BLE001
        pass
    return {**_LAST_SIM, "from_cache": True}


def draft_plan(
    trials: int = 150, progress: Callable[[str], None] | None = None
) -> dict:
    from sleeper_agent.draft_sim import draft_plan as _plan

    if progress:
        progress("Simulating every draft slot...")
    return _plan(get_league(), trials=trials)


def draft_dissent(top: int = 20) -> dict:
    return draft_mod.disagreements(get_league(), top_n=top)


def draft_recap() -> dict:
    return draft_mod.draft_recap(get_league())


# ----------------------------------------------------------------- weekly


def lineup(week: int | None = None) -> dict:
    league = get_league()
    roster, problem = _need_roster(league)
    if problem:
        return problem
    wk = _week(league, week)
    result = optimize(league, [p for p in roster["players"] if p], wk, log=True)
    return {"week": wk, **result.as_dict()}


def startsit(week: int | None = None) -> dict:
    league = get_league()
    roster, problem = _need_roster(league)
    if problem:
        return problem
    return start_sit_advice(league, roster, _week(league, week))


def waivers(
    week: int | None = None,
    top: int = 12,
    progress: Callable[[str], None] | None = None,
) -> dict:
    league = get_league()
    roster, problem = _need_roster(league)
    if problem:
        return problem
    wk = _week(league, week)
    if progress:
        progress("Scoring the free agent pool...")
    result = recommend_waivers(league, roster, wk, top_n=top, log=True)
    if progress:
        progress("Finding drop candidates...")
    result["drops"] = drop_candidates(league, roster, wk)
    return result


def matchup(week: int | None = None) -> dict:
    league = get_league()
    roster, problem = _need_roster(league)
    if problem:
        return problem
    return matchup_preview(league, roster, _week(league, week))


def league_standings() -> dict:
    return {"standings": standings(get_league())}


def byes(through: int = 17) -> dict:
    league = get_league()
    roster, problem = _need_roster(league)
    payload = {"team_byes": team_bye_weeks(league)}
    if problem:
        payload["roster"] = problem
        return payload
    payload["roster"] = bye_week_report(league, roster, through)
    # Per-week detail for the calendar view.
    players = load_players([p for p in roster["players"] if p])
    byes_by_team = team_bye_weeks(league)
    weeks: dict[int, list] = {}
    for p in players.values():
        w = byes_by_team.get(p.team or "")
        if w:
            weeks.setdefault(w, []).append(
                {"player": p.label(), "position": p.position, "player_id": p.player_id}
            )
    payload["by_week"] = {str(k): v for k, v in sorted(weeks.items())}
    return payload


def activity() -> dict:
    league = get_league()
    return {"activity": league_activity(league, current_week())}


def trending(kind: str = "add", hours: int = 48, limit: int = 25) -> dict:
    rows = client.trending(kind, lookback_hours=hours, limit=limit) or []
    players = load_players([r["player_id"] for r in rows if r.get("player_id")])
    rostered = get_league().rostered_player_ids()
    return {
        "kind": kind,
        "players": [
            {
                "player_id": r["player_id"],
                "label": players[r["player_id"]].label(),
                "position": players[r["player_id"]].position,
                "count": r.get("count", 0),
                "rostered": r["player_id"] in rostered,
            }
            for r in rows
            if r.get("player_id") in players
        ],
    }


# ----------------------------------------------------------------- trades


def trade_targets(week: int | None = None, top: int = 6) -> dict:
    league = get_league()
    roster, problem = _need_roster(league)
    if problem:
        return problem
    return trades_mod.find_trade_targets(league, roster, _week(league, week), top_n=top)


def trade_evaluate(
    send: list[str], receive: list[str], partner: int | None = None, week: int | None = None
) -> dict:
    league = get_league()
    roster, problem = _need_roster(league)
    if problem:
        return problem
    send_ids, receive_ids, unknown = [], [], []
    for name in send:
        pid = trades_mod.resolve_player(name)
        (send_ids if pid else unknown).append(pid or name)
    for name in receive:
        pid = trades_mod.resolve_player(name)
        (receive_ids if pid else unknown).append(pid or name)
    if unknown:
        return unavailable(
            f"Could not find: {', '.join(unknown)}",
            "Try a fuller name, or pick from the search suggestions.",
        )
    return trades_mod.evaluate_trade(
        league,
        roster,
        send_ids,
        receive_ids,
        _week(league, week),
        partner_roster_id=partner,
        log=True,
    )


# ---------------------------------------------------------------- players


def search_players(q: str, limit: int = 15) -> dict:
    q = (q or "").strip().lower().replace(".", "").replace("'", "")
    if len(q) < 2:
        return {"players": []}
    rows = read_conn().execute(
        "SELECT player_id, full_name, position, team, injury_status FROM players"
        " WHERE search_name LIKE ? AND active = 1 AND position IN"
        " ('QB','RB','WR','TE','K','DEF') ORDER BY LENGTH(full_name) LIMIT ?",
        (f"%{q}%", limit),
    ).fetchall()
    return {
        "players": [
            {
                "player_id": r["player_id"],
                "name": r["full_name"],
                "position": r["position"],
                "team": r["team"],
                "injury_status": r["injury_status"],
            }
            for r in rows
        ]
    }


def player_detail(player_id: str, week: int | None = None) -> dict:
    league = get_league()
    wk = _week(league, week)
    players = load_players([player_id])
    player = players.get(player_id)
    if not player:
        return unavailable("Unknown player.", "Try the search box.")

    values = season_value(league, player_ids=[player_id])
    sv = values.get(player_id)
    rostered_by = None
    for r in league.rosters():
        if player_id in (r.get("players") or []):
            rostered_by = league.roster_name(r.get("roster_id"))
            break

    adp_row = read_conn().execute(
        "SELECT adp FROM adp WHERE season = ? AND player_id = ? AND format = ?",
        (league.season, player_id, league.scoring_format()),
    ).fetchone()

    weekly = []
    for w in FULL:
        proj = week_projections(league, w).get(player_id)
        weekly.append(
            {
                "week": w,
                "points": round(proj.points, 2) if proj and proj.has_game else None,
                "opponent": proj.opponent if proj else None,
                "bye": bool(proj is not None and not proj.has_game)
                or (sv is not None and w in sv.bye_weeks),
            }
        )

    return {
        "player_id": player_id,
        "name": player.name,
        "position": player.position,
        "team": player.team,
        "label": player.label(),
        "injury_status": player.injury_status,
        "age": player.age,
        "depth_chart_order": player.depth_chart_order,
        "rostered_by": rostered_by,
        "free_agent": rostered_by is None,
        "adp": adp_row["adp"] if adp_row else None,
        "season_points": round(sv.points, 1) if sv else None,
        "games": sv.games if sv else 0,
        "ppg": sv.ppg if sv else None,
        "bye_weeks": sv.bye_weeks if sv else [],
        "playoff_points": round(sv.playoff_points, 1) if sv else None,
        "rest_of_season": rest_of_season(league, [player_id], wk).get(player_id),
        "consistency": consistency(league, player_id, wk),
        "weekly": weekly,
    }


def compare_players(ids: list[str], week: int | None = None) -> dict:
    return {"players": [player_detail(pid, week) for pid in ids[:4]]}


# ----------------------------------------------------------------- digest


def digest(week: int | None = None, progress: Callable[[str], None] | None = None) -> dict:
    league = get_league()
    if progress:
        progress("Syncing and assembling the brief...")
    return {"markdown": build_digest(league.league_id, week)}


def notify_digest(week: int | None = None) -> dict:
    from sleeper_agent.digest import notify

    league = get_league()
    text = build_digest(league.league_id, week)
    return {"sent": notify(text), "markdown": text}
