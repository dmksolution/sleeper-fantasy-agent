"""League context and the custom scoring engine.

The important idea here: rather than trusting Sleeper's generic `pts_ppr`, we take
the raw projected stat line and dot-product it against your league's own
`scoring_settings`. A 6-point passing TD league, a TE premium, a bonus for 100
yard rushing games, IDP scoring, all of it falls out of the same calculation.
Keys that are not scoring categories (adp_ppr, gp, pos_rank_ppr) simply have no
entry in scoring_settings and contribute zero.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta

from .client import client
from .config import settings
from .store import cached, connect, read_conn

# Slots that are not real players.
NON_PLAYER_SLOTS = {"BN", "IR", "TAXI"}

# Which positions may fill each Sleeper roster slot.
FLEX_ELIGIBILITY: dict[str, set[str]] = {
    "QB": {"QB"},
    "RB": {"RB"},
    "WR": {"WR"},
    "TE": {"TE"},
    "K": {"K"},
    "DEF": {"DEF"},
    "FLEX": {"RB", "WR", "TE"},
    "WRRB_FLEX": {"RB", "WR"},
    "REC_FLEX": {"WR", "TE"},
    "WRRB_WRT": {"RB", "WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
    "IDP_FLEX": {"DL", "LB", "DB"},
    "DL": {"DL", "DE", "DT"},
    "LB": {"LB"},
    "DB": {"DB", "CB", "S"},
}


def slot_eligibility(slot: str) -> set[str]:
    return FLEX_ELIGIBILITY.get(slot, {slot})


@dataclass
class Player:
    player_id: str
    name: str
    position: str
    team: str | None = None
    injury_status: str | None = None
    fantasy_positions: list[str] = field(default_factory=list)
    depth_chart_order: int | None = None
    age: int | None = None

    @property
    def is_out(self) -> bool:
        return (self.injury_status or "").upper() in {"OUT", "IR", "PUP", "SUS", "DNR", "NA"}

    @property
    def is_questionable(self) -> bool:
        return (self.injury_status or "").upper() in {"QUESTIONABLE", "DOUBTFUL"}

    def eligible_for(self, slot: str) -> bool:
        allowed = slot_eligibility(slot)
        pool = set(self.fantasy_positions or []) | {self.position}
        return bool(pool & allowed)

    def label(self) -> str:
        tag = f" ({self.injury_status})" if self.injury_status else ""
        return f"{self.name} {self.position}-{self.team or 'FA'}{tag}"


def _query_players() -> dict[str, Player]:
    rows = read_conn().execute(
        "SELECT player_id, full_name, position, team, injury_status, fantasy_positions,"
        " depth_chart_order, age FROM players"
    ).fetchall()
    return {
        r["player_id"]: Player(
            player_id=r["player_id"],
            name=r["full_name"] or r["player_id"],
            position=r["position"] or "",
            team=r["team"],
            injury_status=r["injury_status"],
            fantasy_positions=json.loads(r["fantasy_positions"] or "[]"),
            depth_chart_order=r["depth_chart_order"],
            age=r["age"],
        )
        for r in rows
    }


# optimize() calls this on every invocation, and the draft simulator calls
# optimize() tens of thousands of times. Reading the whole table once and
# slicing in memory turns a per-call query into a per-process one. Player is
# treated as an immutable value object; nothing in the package mutates one.
_PLAYER_CACHE: dict[str, Player] | None = None


def clear_player_cache() -> None:
    """Drop memoized players. Called by sync_all() after new rows land."""
    global _PLAYER_CACHE
    _PLAYER_CACHE = None


def load_players(
    player_ids: list[str] | None = None, *, refresh: bool = False
) -> dict[str, Player]:
    global _PLAYER_CACHE
    if refresh or _PLAYER_CACHE is None:
        _PLAYER_CACHE = _query_players()
    if player_ids is None:
        return _PLAYER_CACHE
    out: dict[str, Player] = {}
    for pid in player_ids:
        found = _PLAYER_CACHE.get(pid)
        if found is not None:
            out[pid] = found
    return out


class League:
    """Everything about one league, with short-lived caching."""

    def __init__(self, league_id: str | None = None) -> None:
        self.league_id = league_id or settings.require_league()
        ttl = timedelta(minutes=settings.league_cache_minutes)
        self.raw = cached(
            f"league:{self.league_id}", ttl, lambda: client.league(self.league_id)
        ) or {}
        if not self.raw:
            raise RuntimeError(f"League {self.league_id} not found on Sleeper")
        self._ttl = ttl

    # ------------------------------------------------------------- settings

    @property
    def name(self) -> str:
        return self.raw.get("name", "")

    @property
    def season(self) -> str:
        return str(self.raw.get("season") or settings.season)

    @property
    def scoring(self) -> dict:
        return self.raw.get("scoring_settings") or {}

    @property
    def roster_positions(self) -> list[str]:
        return self.raw.get("roster_positions") or []

    @property
    def starting_slots(self) -> list[str]:
        return [s for s in self.roster_positions if s not in NON_PLAYER_SLOTS]

    @property
    def bench_size(self) -> int:
        return sum(1 for s in self.roster_positions if s == "BN")

    @property
    def team_count(self) -> int:
        return int(self.raw.get("total_rosters") or 12)

    @property
    def waiver_type(self) -> str:
        wt = (self.raw.get("settings") or {}).get("waiver_type")
        return {0: "rolling", 1: "reverse_standings", 2: "faab"}.get(wt, "unknown")

    @property
    def faab_budget(self) -> int:
        return int((self.raw.get("settings") or {}).get("waiver_budget") or 0)

    @property
    def playoff_week_start(self) -> int:
        return int((self.raw.get("settings") or {}).get("playoff_week_start") or 15)

    def scoring_format(self) -> str:
        """Best matching ADP format for this league's scoring."""
        rec = float(self.scoring.get("rec", 0) or 0)
        qb_slots = sum(
            1 for s in self.roster_positions if s in {"QB", "SUPER_FLEX"}
        )
        if qb_slots >= 2:
            return "2qb"
        if rec >= 0.9:
            return "ppr"
        if rec >= 0.4:
            return "half_ppr"
        return "std"

    # ------------------------------------------------------------- scoring

    def score(self, stats: dict) -> float:
        """Apply this league's scoring settings to a raw stat line."""
        if not stats:
            return 0.0
        scoring = self.scoring
        if not scoring:
            return float(stats.get("pts_ppr") or 0.0)
        total = 0.0
        for key, value in stats.items():
            weight = scoring.get(key)
            if weight and isinstance(value, (int, float)):
                total += float(weight) * float(value)
        return round(total, 2)

    # -------------------------------------------------------------- rosters

    def rosters(self) -> list[dict]:
        return cached(
            f"rosters:{self.league_id}", self._ttl, lambda: client.rosters(self.league_id)
        ) or []

    def users(self) -> list[dict]:
        return cached(
            f"users:{self.league_id}", self._ttl, lambda: client.league_users(self.league_id)
        ) or []

    def matchups(self, week: int) -> list[dict]:
        return cached(
            f"matchups:{self.league_id}:{week}",
            self._ttl,
            lambda: client.matchups(self.league_id, week),
        ) or []

    def transactions(self, week: int) -> list[dict]:
        return cached(
            f"txn:{self.league_id}:{week}",
            self._ttl,
            lambda: client.transactions(self.league_id, week),
        ) or []

    def owner_names(self) -> dict[str, str]:
        out = {}
        for u in self.users():
            meta = u.get("metadata") or {}
            out[u["user_id"]] = meta.get("team_name") or u.get("display_name") or u["user_id"]
        return out

    def roster_name(self, roster_id: int) -> str:
        names = self.owner_names()
        for r in self.rosters():
            if r.get("roster_id") == roster_id:
                return names.get(r.get("owner_id"), f"Roster {roster_id}")
        return f"Roster {roster_id}"

    def my_roster(self, username: str | None = None) -> dict | None:
        username = username or settings.sleeper_username
        if not username:
            return None
        user = cached(f"user:{username}", timedelta(days=7), lambda: client.user(username))
        if not user:
            return None
        user_id = user.get("user_id")
        for r in self.rosters():
            if r.get("owner_id") == user_id:
                return r
            if user_id in (r.get("co_owners") or []):
                return r
        return None

    def rostered_player_ids(self) -> set[str]:
        out: set[str] = set()
        for r in self.rosters():
            out.update(r.get("players") or [])
        return out

    def summary(self) -> dict:
        return {
            "league_id": self.league_id,
            "name": self.name,
            "season": self.season,
            "teams": self.team_count,
            "scoring_format": self.scoring_format(),
            "starting_slots": self.starting_slots,
            "bench_size": self.bench_size,
            "waiver_type": self.waiver_type,
            "faab_budget": self.faab_budget,
            "playoff_week_start": self.playoff_week_start,
            "ppr_value": self.scoring.get("rec"),
            "pass_td_value": self.scoring.get("pass_td"),
            "te_premium": self.scoring.get("bonus_rec_te"),
        }
