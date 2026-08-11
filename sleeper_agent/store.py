"""SQLite persistence and TTL cache."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .config import PROJECT_ROOT, settings

SCHEMA_PATH = PROJECT_ROOT / "sql" / "schema.sql"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(settings.db_file(), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Opening and closing a connection per query is cheap on a local disk and
# expensive on a network share, where this database often lives: profiling the
# draft simulator's startup showed ~2.3s of pure connect/close across 19 reads.
# Read-only callers share one connection instead. Writers keep using connect(),
# so there is still exactly one place that commits.
_READ_CONN: sqlite3.Connection | None = None


def read_conn() -> sqlite3.Connection:
    global _READ_CONN
    if _READ_CONN is None:
        conn = sqlite3.connect(
            settings.db_file(), timeout=30, check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        _READ_CONN = conn
    return _READ_CONN


def close_read_conn() -> None:
    """Drop the shared reader, so the next read sees committed writes."""
    global _READ_CONN
    if _READ_CONN is not None:
        _READ_CONN.close()
        _READ_CONN = None


def init_db() -> Path:
    path = settings.db_file()
    with connect() as conn:
        conn.executescript(SCHEMA_PATH.read_text())
    return path


def _is_fresh(fetched_at: str, max_age: timedelta) -> bool:
    try:
        ts = datetime.fromisoformat(fetched_at)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ts < max_age


def cache_get(key: str, max_age: timedelta) -> Any | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT payload, fetched_at FROM kv_cache WHERE key = ?", (key,)
        ).fetchone()
    if row and _is_fresh(row["fetched_at"], max_age):
        return json.loads(row["payload"])
    return None


def cache_put(key: str, payload: Any) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO kv_cache(key, payload, fetched_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, "
            "fetched_at=excluded.fetched_at",
            (key, json.dumps(payload), utcnow()),
        )


def cached(key: str, max_age: timedelta, loader: Callable[[], Any]) -> Any:
    """Return the cached value if fresh, otherwise call loader() and store it.

    If the loader returns nothing, fall back to any stale cached value rather
    than failing. Stale projections beat no projections on a Sunday morning.
    """
    hit = cache_get(key, max_age)
    if hit is not None:
        return hit
    value = loader()
    if value:
        cache_put(key, value)
        return value
    with connect() as conn:
        row = conn.execute("SELECT payload FROM kv_cache WHERE key = ?", (key,)).fetchone()
    return json.loads(row["payload"]) if row else value


def log_recommendation(
    league_id: str, season: str, week: int, kind: str, player_id: str | None, detail: dict
) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO recommendations(league_id, season, week, kind, player_id, detail, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (league_id, season, week, kind, player_id, json.dumps(detail), utcnow()),
        )


def snapshot_rosters(
    league_id: str, season: str, week: int, rosters: list[dict]
) -> None:
    stamp = utcnow()
    with connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO roster_history"
            "(league_id, season, week, roster_id, owner_id, players, starters, captured_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    league_id,
                    season,
                    week,
                    r.get("roster_id"),
                    r.get("owner_id"),
                    json.dumps(r.get("players") or []),
                    json.dumps(r.get("starters") or []),
                    stamp,
                )
                for r in rosters
            ],
        )


def previous_roster(league_id: str, roster_id: int, before: str) -> list[str] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT players FROM roster_history WHERE league_id=? AND roster_id=? "
            "AND captured_at < ? ORDER BY captured_at DESC LIMIT 1",
            (league_id, roster_id, before),
        ).fetchone()
    return json.loads(row["players"]) if row else None
