"""Configuration loaded from environment variables (or a .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Minimal .env loader so we do not need python-dotenv."""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


@dataclass
class Settings:
    sleeper_username: str = field(default_factory=lambda: _env("SLEEPER_USERNAME"))
    league_id: str = field(default_factory=lambda: _env("SLEEPER_LEAGUE_ID"))
    draft_id: str = field(default_factory=lambda: _env("SLEEPER_DRAFT_ID"))
    season: str = field(default_factory=lambda: _env("SLEEPER_SEASON"))

    db_path: str = field(
        default_factory=lambda: _env("DB_PATH", str(PROJECT_ROOT / "data" / "sleeper.db"))
    )
    player_cache_hours: int = field(
        default_factory=lambda: int(_env("PLAYER_CACHE_HOURS", "24") or 24)
    )
    projection_cache_hours: int = field(
        default_factory=lambda: int(_env("PROJECTION_CACHE_HOURS", "6") or 6)
    )
    league_cache_minutes: int = field(
        default_factory=lambda: int(_env("LEAGUE_CACHE_MINUTES", "15") or 15)
    )

    # Outbound notification for the scheduled digest. Any one of these may be set.
    webhook_url: str = field(default_factory=lambda: _env("DIGEST_WEBHOOK_URL"))
    ntfy_topic: str = field(default_factory=lambda: _env("NTFY_TOPIC"))
    ntfy_server: str = field(default_factory=lambda: _env("NTFY_SERVER", "https://ntfy.sh"))

    # How many future weeks count toward "rest of season" value.
    ros_horizon_weeks: int = field(
        default_factory=lambda: int(_env("ROS_HORIZON_WEEKS", "6") or 6)
    )
    regular_season_weeks: int = field(
        default_factory=lambda: int(_env("REGULAR_SEASON_WEEKS", "17") or 17)
    )
    # How many weeks the projections endpoint publishes. Sleeper serves all of
    # them from the preseason onward, so we cache the whole season rather than
    # pro-rating an aggregate for the weeks we have not looked at yet.
    nfl_weeks: int = field(default_factory=lambda: int(_env("NFL_WEEKS", "18") or 18))

    def db_file(self) -> Path:
        p = Path(self.db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def require_league(self) -> str:
        if not self.league_id:
            raise RuntimeError(
                "SLEEPER_LEAGUE_ID is not set. Run `python cli.py setup --username <you>` "
                "to find it, then add it to .env"
            )
        return self.league_id


settings = Settings()
