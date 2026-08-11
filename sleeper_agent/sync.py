"""Pull Sleeper data into the local SQLite cache.

Call `sync_all()` from cron once a day in the preseason and a few times a week
in season. Everything else in the package reads from SQLite, so the analysis
tools stay fast and work offline if Sleeper is down.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

from .client import FANTASY_POSITIONS, client
from .config import settings
from .store import cached, connect, init_db, utcnow

log = logging.getLogger(__name__)

ADP_FORMATS = {
    "ppr": ("adp_ppr", "pos_adp_ppr"),
    "half_ppr": ("adp_half_ppr", "pos_adp_half_ppr"),
    "std": ("adp_std", "pos_adp_std"),
    "2qb": ("adp_2qb", "pos_adp_2qb"),
    "dynasty_ppr": ("adp_dynasty_ppr", "pos_adp_dynasty_ppr"),
}
# Sleeper uses 999/1000 as a sentinel for "undrafted / no data".
ADP_SENTINELS = (999.0, 1000.0)


def current_state() -> dict:
    return cached("state:nfl", timedelta(hours=1), client.state)


def resolve_season() -> str:
    if settings.season:
        return settings.season
    return str(current_state().get("season") or "")


def current_week() -> int:
    state = current_state()
    week = state.get("week") or 1
    if state.get("season_type") == "pre":
        return 1
    return int(week)


# ------------------------------------------------------------------ players


def sync_players(force: bool = False) -> int:
    """Load the full player index. ~15 MB, so once per day at most."""
    max_age = timedelta(seconds=0) if force else timedelta(hours=settings.player_cache_hours)
    players = cached("players:nfl", max_age, client.all_players)
    if not players:
        return 0

    rows = []
    for pid, p in players.items():
        if not isinstance(p, dict):
            continue
        first = p.get("first_name") or ""
        last = p.get("last_name") or ""
        full = (p.get("full_name") or f"{first} {last}").strip()
        rows.append(
            (
                pid,
                full,
                full.lower().replace(".", "").replace("'", ""),
                p.get("position"),
                json.dumps(p.get("fantasy_positions") or []),
                p.get("team"),
                p.get("status"),
                p.get("injury_status"),
                p.get("injury_body_part"),
                (p.get("injury_notes") or "")[:400],
                p.get("news_updated"),
                p.get("age"),
                p.get("years_exp"),
                p.get("depth_chart_position"),
                p.get("depth_chart_order"),
                p.get("number"),
                1 if p.get("active") else 0,
                utcnow(),
            )
        )

    with connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO players (player_id, full_name, search_name, position,"
            " fantasy_positions, team, status, injury_status, injury_body_part, injury_notes,"
            " news_updated, age, years_exp, depth_chart_pos, depth_chart_order, number, active,"
            " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
    log.info("synced %s players", len(rows))
    return len(rows)


# -------------------------------------------------------------- projections


def _write_projection_rows(season: str, week: int, records: list[dict]) -> int:
    rows = []
    for rec in records:
        pid = rec.get("player_id")
        stats = rec.get("stats") or {}
        if not pid:
            continue
        rows.append(
            (
                season,
                week,
                pid,
                rec.get("team"),
                rec.get("opponent"),
                json.dumps(stats),
                stats.get("pts_ppr"),
                stats.get("pts_half_ppr"),
                stats.get("pts_std"),
                utcnow(),
            )
        )
    if rows:
        with connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO projections (season, week, player_id, team, opponent,"
                " stats, pts_ppr, pts_half_ppr, pts_std, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
    return len(rows)


def sync_week_projections(season: str, week: int, force: bool = False) -> int:
    max_age = (
        timedelta(seconds=0) if force else timedelta(hours=settings.projection_cache_hours)
    )
    records = cached(
        f"proj:{season}:{week}",
        max_age,
        lambda: client.projections(season, week, FANTASY_POSITIONS),
    )
    return _write_projection_rows(season, week, records or [])


def sync_season_projections(season: str, force: bool = False) -> int:
    """Season aggregate, stored as week 0. This is where ADP lives."""
    max_age = timedelta(seconds=0) if force else timedelta(hours=12)
    records = cached(
        f"proj:{season}:season",
        max_age,
        lambda: client.projections(season, None, FANTASY_POSITIONS),
    )
    records = records or []
    written = _write_projection_rows(season, 0, records)

    adp_rows = []
    stamp = utcnow()
    for rec in records:
        pid = rec.get("player_id")
        stats = rec.get("stats") or {}
        if not pid:
            continue
        for fmt, (adp_key, pos_key) in ADP_FORMATS.items():
            value = stats.get(adp_key)
            if value is None or value in ADP_SENTINELS:
                continue
            adp_rows.append((season, pid, fmt, value, stats.get(pos_key), stamp))
    if adp_rows:
        with connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO adp (season, player_id, format, adp, pos_adp, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                adp_rows,
            )
    log.info("synced %s season projections, %s adp rows", written, len(adp_rows))
    return written


# ------------------------------------------------------------------ actuals


def sync_actuals(season: str, week: int, force: bool = False) -> int:
    max_age = timedelta(seconds=0) if force else timedelta(hours=3)
    data = cached(
        f"stats:{season}:{week}", max_age, lambda: client.stats(season, week)
    )
    if not isinstance(data, dict):
        return 0
    rows = [
        (
            season,
            week,
            pid,
            json.dumps(stats),
            stats.get("pts_ppr"),
            stats.get("pts_half_ppr"),
            stats.get("pts_std"),
            utcnow(),
        )
        for pid, stats in data.items()
        if isinstance(stats, dict)
    ]
    if rows:
        with connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO actuals (season, week, player_id, stats, pts_ppr,"
                " pts_half_ppr, pts_std, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                rows,
            )
    return len(rows)


# --------------------------------------------------------------------- all


def sync_all(force: bool = False, weeks_ahead: int = 4) -> dict:
    """One call that leaves the cache ready for every other tool."""
    init_db()
    season = resolve_season()
    week = current_week()

    result = {
        "season": season,
        "week": week,
        "players": sync_players(force=force),
        "season_projections": sync_season_projections(season, force=force),
        "week_projections": {},
        "actuals": {},
    }

    for w in range(week, min(week + weeks_ahead, settings.regular_season_weeks + 1)):
        result["week_projections"][w] = sync_week_projections(season, w, force=force)

    for w in range(max(1, week - 3), week):
        count = sync_actuals(season, w, force=force)
        if count:
            result["actuals"][w] = count

    return result
