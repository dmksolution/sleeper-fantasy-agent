"""Pull Sleeper data into the local SQLite cache.

Call `sync_all()` from cron once a day in the preseason and a few times a week
in season. Everything else in the package reads from SQLite, so the analysis
tools stay fast and work offline if Sleeper is down.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

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


def _projection_age(season: str, week: int) -> timedelta | None:
    """How long ago week `week` was written, or None if we have never had it."""
    with connect() as conn:
        row = conn.execute(
            "SELECT MAX(updated_at) AS ts FROM projections WHERE season = ? AND week = ?",
            (season, week),
        ).fetchone()
    if not row or not row["ts"]:
        return None
    try:
        ts = datetime.fromisoformat(row["ts"])
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ts


def projection_coverage(season: str) -> dict[int, int]:
    """week -> row count, for honest reporting about what we can actually compute."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT week, COUNT(*) AS n FROM projections WHERE season = ? GROUP BY week"
            " ORDER BY week",
            (season,),
        ).fetchall()
    return {int(r["week"]): int(r["n"]) for r in rows}


def sync_week_projections(season: str, week: int, force: bool = False) -> int:
    """Refresh one week of projections.

    Deliberately does not go through `cached()`. That would keep the ~3 MB raw
    payload in kv_cache on top of the parsed rows in `projections`, and at 18
    weeks the duplication costs more than the whole rest of the database. The
    `projections` table is itself the cache; we just ask how old it is.
    """
    max_age = (
        timedelta(seconds=0) if force else timedelta(hours=settings.projection_cache_hours)
    )
    age = _projection_age(season, week)
    if age is not None and age < max_age:
        return 0

    records = client.projections(season, week, FANTASY_POSITIONS)
    if not records:
        # Stale rows beat no rows on a Sunday morning; leave what we have.
        log.warning("no projections returned for %s week %s, keeping cached rows", season, week)
        return 0
    return _write_projection_rows(season, week, records)


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


def data_health(league=None) -> dict:
    """Can these numbers be trusted right now?

    Written for the MCP surface: an assistant reasoning about a recommendation
    should be able to ask whether the cache is complete and whether scoring
    reconciles, rather than discovering mid-answer that half the season is
    missing and every rest-of-season total is short.
    """
    from .league import League
    from .valuation import FULL, coverage, team_bye_weeks

    league = league or League()
    season = league.season
    cov = projection_coverage(season)
    weekly = {w: n for w, n in cov.items() if w > 0}
    expected = set(range(1, settings.nfl_weeks + 1))

    with connect() as conn:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            for table in ("players", "projections", "adp", "actuals", "recommendations")
        }
        stale = conn.execute(
            "SELECT MIN(updated_at) AS oldest FROM projections WHERE season = ? AND week > 0",
            (season,),
        ).fetchone()["oldest"]

    # Scoring audit: our dot product against Sleeper's own number, per position.
    # QB is expected to differ; see tests/test_scoring.py.
    audit: dict[str, dict] = {}
    with connect() as conn:
        rows = conn.execute(
            "SELECT p.stats, pl.position FROM projections p"
            " JOIN players pl ON pl.player_id = p.player_id"
            " WHERE p.season = ? AND p.week BETWEEN 1 AND 17",
            (season,),
        ).fetchall()
    worst: dict[str, float] = {}
    for r in rows:
        stats = json.loads(r["stats"])
        truth = stats.get("pts_ppr")
        if not truth or truth <= 0:
            continue
        diff = abs(league.score(stats) - truth)
        pos = r["position"] or "?"
        if diff > worst.get(pos, 0.0):
            worst[pos] = diff
    for pos, diff in sorted(worst.items()):
        tolerance = 3.0 if pos == "QB" else 1.2
        audit[pos] = {
            "worst_abs_diff_vs_sleeper": round(diff, 2),
            "ok": diff <= tolerance,
            "note": (
                "expected: Sleeper's pts_ppr scores interceptions at +2, this"
                " league at -1, so their number runs 3 x pass_int high"
                if pos == "QB"
                else ""
            ),
        }

    byes = team_bye_weeks(league)
    missing_weeks = sorted(expected - set(weekly))
    return {
        "season": season,
        "projection_weeks_cached": sorted(weekly),
        "projection_weeks_missing": missing_weeks,
        "coverage_complete": not missing_weeks,
        "season_value_source": "summed weekly projections (week 0 is ADP only)",
        "valuation_coverage": coverage(league, FULL),
        "nfl_teams_with_bye_detected": len(byes),
        "oldest_projection_row": stale,
        "row_counts": counts,
        "scoring_audit": audit,
        "warnings": [
            w
            for w in (
                f"missing projection weeks {missing_weeks}; run `cli.py sync`"
                if missing_weeks
                else "",
                "actuals table is empty, so consistency() and any"
                " variance work have no data yet"
                if not counts["actuals"]
                else "",
                f"only {len(byes)} of 32 teams have a detected bye"
                if len(byes) < 32
                else "",
            )
            if w
        ],
    }


def _should_sync_full_season(season: str) -> bool:
    """Full season if we are in the preseason, or if coverage is thin.

    Sleeper publishes every week's projections from the preseason onward, so
    there is no reason to run on a four week window and pro-rate the rest. The
    only cost is 18 cheap GETs.
    """
    if (current_state().get("season_type") or "") == "pre":
        return True
    covered = [w for w in projection_coverage(season) if w > 0]
    return len(covered) < 14


def sync_all(
    force: bool = False,
    weeks_ahead: int | None = None,
    full_season: bool | None = None,
) -> dict:
    """One call that leaves the cache ready for every other tool.

    By default this pulls every week of the season. `weeks_ahead` narrows it to
    a rolling window when you only want a quick in-season refresh.
    """
    init_db()
    season = resolve_season()
    week = current_week()

    if full_season is None:
        full_season = weeks_ahead is None and _should_sync_full_season(season)

    if full_season:
        weeks = list(range(1, settings.nfl_weeks + 1))
    else:
        span = weeks_ahead if weeks_ahead is not None else 4
        weeks = list(range(week, min(week + span, settings.nfl_weeks + 1)))

    result = {
        "season": season,
        "week": week,
        "full_season": full_season,
        "players": sync_players(force=force),
        "season_projections": sync_season_projections(season, force=force),
        "week_projections": {},
        "actuals": {},
    }

    for w in weeks:
        result["week_projections"][w] = sync_week_projections(season, w, force=force)

    for w in range(max(1, week - 3), week):
        count = sync_actuals(season, w, force=force)
        if count:
            result["actuals"][w] = count

    result["coverage"] = projection_coverage(season)

    # Anything holding parsed rows in memory is now stale.
    from .league import clear_player_cache
    from .projections import clear_projection_cache
    from .store import close_read_conn
    from .valuation import clear_bye_cache, clear_value_cache

    clear_projection_cache()
    clear_player_cache()
    clear_bye_cache()
    clear_value_cache()
    close_read_conn()  # the shared reader would otherwise hold a pre-sync view
    return result
