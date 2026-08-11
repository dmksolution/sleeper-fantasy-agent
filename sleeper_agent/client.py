"""Read-only client for the Sleeper API.

The Sleeper API requires no auth token because nothing can be modified through it.
Sleeper asks callers to stay under 1000 requests per minute; we throttle well below
that and cache aggressively in SQLite so a normal session makes only a handful of
network calls.

Two families of endpoints are used:

  * https://api.sleeper.app/v1/...   the documented endpoints (docs.sleeper.com)
  * https://api.sleeper.app/projections/nfl/...   undocumented but stable, and the
    only free source of per-week projections and ADP. Treated as best-effort:
    every caller handles an empty result.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

# Trust the operating system's certificate store instead of only the CA bundle
# baked into certifi. Antivirus "HTTPS scanning" (Norton, Kaspersky, ESET) and
# corporate proxies terminate TLS and re-sign it with a private root that is
# installed in the OS store but is unknown to certifi, so without this every
# request fails with CERTIFICATE_VERIFY_FAILED. Optional: if truststore is not
# installed we fall back to certifi, which is correct on an uninspected network.
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:  # pragma: no cover - depends on the install
    log.debug("truststore not installed; using the certifi CA bundle")

V1 = "https://api.sleeper.app/v1"
ROOT = "https://api.sleeper.app"

SCORING_FORMATS = ("ppr", "half_ppr", "std", "2qb", "dynasty_ppr")
FANTASY_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")


class RateLimiter:
    """Simple token-bucket style throttle shared across threads."""

    def __init__(self, calls_per_minute: int = 300) -> None:
        self.min_interval = 60.0 / max(calls_per_minute, 1)
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            delta = time.monotonic() - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.monotonic()


class SleeperClient:
    def __init__(self, calls_per_minute: int = 300, timeout: int = 60) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "sleeper-fantasy-agent/1.0"})
        self.limiter = RateLimiter(calls_per_minute)
        self.timeout = timeout

    def _get(self, url: str, params: dict | None = None, retries: int = 3) -> Any:
        last_error: Exception | None = None
        for attempt in range(retries):
            self.limiter.wait()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 404:
                    return None
                if resp.status_code == 429:
                    time.sleep(2 ** attempt * 2)
                    continue
                resp.raise_for_status()
                if not resp.content:
                    return None
                return resp.json()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                log.warning("GET %s failed (attempt %s): %s", url, attempt + 1, exc)
                time.sleep(1.5 * (attempt + 1))
        log.error("GET %s gave up: %s", url, last_error)
        return None

    # ---------------------------------------------------------------- state

    def state(self, sport: str = "nfl") -> dict:
        """Current season, week and season_type. The clock for everything else."""
        return self._get(f"{V1}/state/{sport}") or {}

    # ----------------------------------------------------------------- user

    def user(self, username_or_id: str) -> dict | None:
        return self._get(f"{V1}/user/{username_or_id}")

    def user_leagues(self, user_id: str, season: str, sport: str = "nfl") -> list[dict]:
        return self._get(f"{V1}/user/{user_id}/leagues/{sport}/{season}") or []

    # --------------------------------------------------------------- league

    def league(self, league_id: str) -> dict | None:
        return self._get(f"{V1}/league/{league_id}")

    def rosters(self, league_id: str) -> list[dict]:
        return self._get(f"{V1}/league/{league_id}/rosters") or []

    def league_users(self, league_id: str) -> list[dict]:
        return self._get(f"{V1}/league/{league_id}/users") or []

    def matchups(self, league_id: str, week: int) -> list[dict]:
        return self._get(f"{V1}/league/{league_id}/matchups/{week}") or []

    def transactions(self, league_id: str, week: int) -> list[dict]:
        return self._get(f"{V1}/league/{league_id}/transactions/{week}") or []

    def traded_picks(self, league_id: str) -> list[dict]:
        return self._get(f"{V1}/league/{league_id}/traded_picks") or []

    def winners_bracket(self, league_id: str) -> list[dict]:
        return self._get(f"{V1}/league/{league_id}/winners_bracket") or []

    def losers_bracket(self, league_id: str) -> list[dict]:
        return self._get(f"{V1}/league/{league_id}/losers_bracket") or []

    # ---------------------------------------------------------------- draft

    def league_drafts(self, league_id: str) -> list[dict]:
        return self._get(f"{V1}/league/{league_id}/drafts") or []

    def draft(self, draft_id: str) -> dict | None:
        return self._get(f"{V1}/draft/{draft_id}")

    def draft_picks(self, draft_id: str) -> list[dict]:
        return self._get(f"{V1}/draft/{draft_id}/picks") or []

    def draft_traded_picks(self, draft_id: str) -> list[dict]:
        return self._get(f"{V1}/draft/{draft_id}/traded_picks") or []

    # --------------------------------------------------------------- players

    def all_players(self, sport: str = "nfl") -> dict:
        """~15 MB payload. Sleeper asks that this be called at most once per day."""
        return self._get(f"{V1}/players/{sport}") or {}

    def trending(
        self, kind: str = "add", lookback_hours: int = 24, limit: int = 25, sport: str = "nfl"
    ) -> list[dict]:
        return (
            self._get(
                f"{V1}/players/{sport}/trending/{kind}",
                params={"lookback_hours": lookback_hours, "limit": limit},
            )
            or []
        )

    # ------------------------------------------------- projections and stats

    def projections(
        self,
        season: str,
        week: int | None = None,
        positions: tuple[str, ...] = FANTASY_POSITIONS,
        season_type: str = "regular",
        order_by: str = "ppr",
    ) -> list[dict]:
        """Projected stat lines. week=None returns the full-season aggregate,
        which is what carries ADP."""
        path = f"{ROOT}/projections/nfl/{season}"
        if week is not None:
            path = f"{path}/{week}"
        params = [("season_type", season_type), ("order_by", order_by)]
        params += [("position[]", p) for p in positions]
        return self._get(path, params=params) or []

    def stats(
        self, season: str, week: int | None = None, season_type: str = "regular"
    ) -> Any:
        """Actual results. Week form returns {player_id: {stat: value}}."""
        if week is None:
            return self._get(f"{V1}/stats/nfl/{season_type}/{season}") or {}
        return self._get(f"{V1}/stats/nfl/{season_type}/{season}/{week}") or {}


client = SleeperClient()
