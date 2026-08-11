"""Draft board, tiers, and live draft assistance.

Two modes:

  * `value_board()` builds the pre-draft cheat sheet. Precompute this the night
    before, because you do not want to be waiting on API calls with 90 seconds
    on the clock.
  * `draft_status()` and `recommend_pick()` read the live pick feed during the
    draft and tell you who is actually available.

Value is VBD (value based drafting): a player is worth the points he scores
above the last starter at his position who will realistically be started in your
league. That replacement level is derived from your league's own roster slots and
team count, not a generic assumption.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .client import client
from .config import settings
from .league import League, Player, load_players, slot_eligibility
from .store import connect

FLEX_SLOTS = {"FLEX", "WRRB_FLEX", "REC_FLEX", "WRRB_WRT", "SUPER_FLEX"}
CORE_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")


@dataclass
class DraftValue:
    player_id: str
    name: str
    position: str
    team: str | None
    projected_points: float
    vbd: float
    adp: float | None
    pos_rank: int
    tier: int
    bye_risk: str = ""
    value_vs_adp: float | None = None

    def as_dict(self) -> dict:
        return {
            "player_id": self.player_id,
            "player": f"{self.name} ({self.position}-{self.team or 'FA'})",
            "position": self.position,
            "pos_rank": self.pos_rank,
            "tier": self.tier,
            "projected_points": round(self.projected_points, 1),
            "vbd": round(self.vbd, 1),
            "adp": self.adp,
            "value_vs_adp": self.value_vs_adp,
        }


def replacement_levels(league: League) -> dict[str, int]:
    """How many players at each position will be starting league-wide.

    A 12 team league with 2 RB and 1 FLEX slots starts 24 RBs outright, plus a
    share of the flex. We split flex demand by historical usage rather than
    evenly, because flex is mostly RB and WR in practice.
    """
    teams = league.team_count
    counts = {pos: 0 for pos in CORE_POSITIONS}
    flex_pools: list[set[str]] = []

    for slot in league.starting_slots:
        if slot in FLEX_SLOTS:
            flex_pools.append(slot_eligibility(slot))
        elif slot in counts:
            counts[slot] += 1

    flex_share = {"RB": 0.45, "WR": 0.45, "TE": 0.10, "QB": 1.0}
    for pool in flex_pools:
        if pool == {"QB", "RB", "WR", "TE"}:  # superflex is nearly all QB
            share = {"QB": 0.85, "RB": 0.05, "WR": 0.08, "TE": 0.02}
        else:
            total = sum(flex_share.get(p, 0) for p in pool) or 1
            share = {p: flex_share.get(p, 0) / total for p in pool}
        for pos, frac in share.items():
            if pos in counts:
                counts[pos] += frac

    return {pos: max(1, round(n * teams)) for pos, n in counts.items() if n > 0}


def _assign_tiers(values: list[float], max_tiers: int = 12) -> list[int]:
    """Tier by gap detection: a new tier starts at an unusually large drop."""
    if not values:
        return []
    gaps = [values[i] - values[i + 1] for i in range(len(values) - 1)]
    if not gaps:
        return [1]
    positive = [g for g in gaps if g > 0]
    mean = sum(positive) / len(positive) if positive else 0
    spread = (sum((g - mean) ** 2 for g in positive) / len(positive)) ** 0.5 if positive else 0
    threshold = mean + spread

    tiers = [1]
    current = 1
    for gap in gaps:
        if gap >= threshold and current < max_tiers:
            current += 1
        tiers.append(current)
    return tiers


def value_board(
    league: League,
    positions: tuple[str, ...] = CORE_POSITIONS,
    limit_per_position: int = 60,
) -> list[DraftValue]:
    """The pre-draft cheat sheet, sorted by value over replacement."""
    fmt = league.scoring_format()
    with connect() as conn:
        proj_rows = conn.execute(
            "SELECT p.player_id, p.stats, pl.full_name, pl.position, pl.team"
            " FROM projections p JOIN players pl ON pl.player_id = p.player_id"
            " WHERE p.season = ? AND p.week = 0",
            (league.season,),
        ).fetchall()
        adp_rows = conn.execute(
            "SELECT player_id, adp, pos_adp FROM adp WHERE season = ? AND format = ?",
            (league.season, fmt),
        ).fetchall()

    adp_map = {r["player_id"]: (r["adp"], r["pos_adp"]) for r in adp_rows}

    by_pos: dict[str, list[tuple[str, str, str | None, float]]] = {}
    for r in proj_rows:
        pos = r["position"]
        if pos not in positions:
            continue
        pts = league.score(json.loads(r["stats"]))
        if pts <= 0:
            continue
        by_pos.setdefault(pos, []).append((r["player_id"], r["full_name"], r["team"], pts))

    levels = replacement_levels(league)
    board: list[DraftValue] = []

    for pos, entries in by_pos.items():
        entries.sort(key=lambda t: -t[3])
        entries = entries[:limit_per_position]
        idx = min(levels.get(pos, 12), len(entries)) - 1
        replacement = entries[idx][3] if idx >= 0 else 0.0
        tiers = _assign_tiers([e[3] for e in entries])

        for rank, (pid, name, team, pts) in enumerate(entries, start=1):
            adp, pos_adp = adp_map.get(pid, (None, None))
            board.append(
                DraftValue(
                    player_id=pid,
                    name=name,
                    position=pos,
                    team=team,
                    projected_points=pts,
                    vbd=pts - replacement,
                    adp=adp,
                    pos_rank=rank,
                    tier=tiers[rank - 1] if rank - 1 < len(tiers) else 99,
                )
            )

    board.sort(key=lambda d: -d.vbd)
    for overall, item in enumerate(board, start=1):
        if item.adp:
            # Positive means he is going later than his value warrants.
            item.value_vs_adp = round(item.adp - overall, 1)
    return board


# --------------------------------------------------------------- live draft


def resolve_draft_id(league: League, draft_id: str | None = None) -> str | None:
    if draft_id or settings.draft_id:
        return draft_id or settings.draft_id
    drafts = client.league_drafts(league.league_id)
    if not drafts:
        return None
    return drafts[0].get("draft_id")


@dataclass
class DraftState:
    draft_id: str
    status: str
    rounds: int
    teams: int
    pick_type: str
    picks_made: int
    drafted_ids: set[str] = field(default_factory=set)
    my_slot: int | None = None
    my_picks: list[str] = field(default_factory=list)
    on_the_clock_slot: int | None = None
    next_pick_overall: int | None = None
    picks_until_my_turn: int | None = None


def draft_status(league: League, draft_id: str | None = None) -> DraftState | None:
    did = resolve_draft_id(league, draft_id)
    if not did:
        return None
    draft = client.draft(did) or {}
    picks = client.draft_picks(did) or []
    dsettings = draft.get("settings") or {}
    teams = int(dsettings.get("teams") or league.team_count)
    rounds = int(dsettings.get("rounds") or 15)

    drafted = {p.get("player_id") for p in picks if p.get("player_id")}

    my_slot = None
    my_roster = league.my_roster()
    draft_order = draft.get("draft_order") or {}
    if my_roster and my_roster.get("owner_id") in draft_order:
        my_slot = int(draft_order[my_roster["owner_id"]])

    my_picks = [
        p["player_id"]
        for p in picks
        if my_slot and p.get("draft_slot") == my_slot and p.get("player_id")
    ]

    next_overall = len(picks) + 1
    on_clock = None
    picks_until = None
    if teams:
        rnd = (next_overall - 1) // teams + 1
        idx = (next_overall - 1) % teams
        snake = draft.get("type") == "snake"
        on_clock = (teams - idx) if (snake and rnd % 2 == 0) else (idx + 1)
        if my_slot:
            picks_until = _picks_until(next_overall, my_slot, teams, rounds, snake)

    return DraftState(
        draft_id=did,
        status=draft.get("status", "unknown"),
        rounds=rounds,
        teams=teams,
        pick_type=draft.get("type", "snake"),
        picks_made=len(picks),
        drafted_ids=drafted,
        my_slot=my_slot,
        my_picks=my_picks,
        on_the_clock_slot=on_clock,
        next_pick_overall=next_overall,
        picks_until_my_turn=picks_until,
    )


def _picks_until(next_overall: int, my_slot: int, teams: int, rounds: int, snake: bool) -> int | None:
    for overall in range(next_overall, teams * rounds + 1):
        rnd = (overall - 1) // teams + 1
        idx = (overall - 1) % teams
        slot = (teams - idx) if (snake and rnd % 2 == 0) else (idx + 1)
        if slot == my_slot:
            return overall - next_overall
    return None


def recommend_pick(
    league: League, draft_id: str | None = None, top_n: int = 12
) -> dict:
    """Best available, filtered by what is actually left and what you need."""
    state = draft_status(league, draft_id)
    board = value_board(league)
    if state:
        available = [b for b in board if b.player_id not in state.drafted_ids]
        my_ids = state.my_picks
    else:
        available = board
        my_ids = []

    my_players = load_players(my_ids) if my_ids else {}
    have: dict[str, int] = {}
    for p in my_players.values():
        have[p.position] = have.get(p.position, 0) + 1

    needs = positional_needs(league, have)

    ranked = []
    for item in available[: top_n * 6]:
        need_multiplier = 1.0 + 0.12 * needs.get(item.position, 0)
        scarcity = _tier_scarcity(available, item)
        ranked.append(
            (
                item.vbd * need_multiplier + scarcity,
                item,
                need_multiplier,
                scarcity,
            )
        )
    ranked.sort(key=lambda t: -t[0])

    return {
        "draft_status": state.status if state else "not started",
        "picks_made": state.picks_made if state else 0,
        "my_draft_slot": state.my_slot if state else None,
        "picks_until_my_turn": state.picks_until_my_turn if state else None,
        "my_roster_so_far": [p.label() for p in my_players.values()],
        "positional_needs": needs,
        "recommendations": [
            {
                **item.as_dict(),
                "adjusted_score": round(score, 1),
                "need_boost": round(mult, 2),
                "tier_scarcity_bonus": round(scarce, 1),
            }
            for score, item, mult, scarce in ranked[:top_n]
        ],
    }


def positional_needs(league: League, have: dict[str, int]) -> dict[str, int]:
    """How many more of each position you still need to fill starting slots."""
    required: dict[str, int] = {}
    flex_count = 0
    for slot in league.starting_slots:
        if slot in FLEX_SLOTS:
            flex_count += 1
        else:
            required[slot] = required.get(slot, 0) + 1
    needs = {
        pos: max(0, count - have.get(pos, 0)) for pos, count in required.items()
    }
    if flex_count:
        surplus = sum(
            max(0, have.get(pos, 0) - required.get(pos, 0)) for pos in ("RB", "WR", "TE")
        )
        remaining = max(0, flex_count - surplus)
        for pos in ("RB", "WR"):
            needs[pos] = needs.get(pos, 0) + remaining
    return needs


def _tier_scarcity(available: list[DraftValue], item: DraftValue) -> float:
    """Bonus when a player is the last one in his tier at his position."""
    same = [
        a for a in available if a.position == item.position and a.tier == item.tier
    ]
    if len(same) <= 1:
        return 8.0
    if len(same) <= 2:
        return 4.0
    if len(same) <= 3:
        return 2.0
    return 0.0


def draft_recap(league: League, draft_id: str | None = None) -> dict:
    """After the draft: who won it on paper, and where the value went."""
    did = resolve_draft_id(league, draft_id)
    if not did:
        return {"error": "no draft found for this league"}
    picks = client.draft_picks(did) or []
    board = {b.player_id: b for b in value_board(league)}

    by_slot: dict[int, list[dict]] = {}
    for p in picks:
        pid = p.get("player_id")
        slot = p.get("draft_slot")
        if not pid or slot is None:
            continue
        entry = board.get(pid)
        by_slot.setdefault(slot, []).append(
            {
                "round": p.get("round"),
                "pick_no": p.get("pick_no"),
                "player": (p.get("metadata") or {}).get("first_name", "")
                + " "
                + (p.get("metadata") or {}).get("last_name", ""),
                "position": (p.get("metadata") or {}).get("position"),
                "vbd": round(entry.vbd, 1) if entry else None,
                "reach_or_steal": (
                    round(entry.adp - p.get("pick_no", 0), 1)
                    if entry and entry.adp
                    else None
                ),
            }
        )

    totals = {
        slot: round(sum(e["vbd"] or 0 for e in entries), 1)
        for slot, entries in by_slot.items()
    }
    return {
        "draft_id": did,
        "total_picks": len(picks),
        "vbd_by_draft_slot": dict(sorted(totals.items(), key=lambda t: -t[1])),
        "picks_by_slot": by_slot,
    }
