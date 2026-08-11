"""Optimal lineup solver.

Setting a lineup is an assignment problem: slots on one side, players on the
other, projected points as the weight. Greedy "best QB, then best RB" filling is
wrong whenever flex slots overlap, so this solves it exactly with the Hungarian
algorithm. Slot counts are small (10 to 12), so the O(n^3) cost is irrelevant.
"""

from __future__ import annotations

from dataclasses import dataclass

from .league import League, Player, load_players
from .projections import Projection, week_projections
from .store import log_recommendation

BIG_COST = 1e9  # cost used to forbid an illegal slot/player pairing


def hungarian(cost: list[list[float]]) -> list[int]:
    """Minimum-cost assignment for a rectangular matrix (rows <= cols).

    Returns assignment[row] = col. Classic O(n^3) JV-style implementation using
    potentials, adapted for rectangular input.
    """
    n = len(cost)
    if n == 0:
        return []
    m = len(cost[0])
    assert n <= m, "need at least as many columns as rows"

    INF = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)   # p[col] = row assigned to that column
    way = [0] * (m + 1)

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = INF
            j1 = 0
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1

    assignment = [-1] * n
    for j in range(1, m + 1):
        if p[j]:
            assignment[p[j] - 1] = j - 1
    return assignment


@dataclass
class SlotAssignment:
    slot: str
    player: Player | None
    points: float
    opponent: str | None = None
    note: str = ""


@dataclass
class OptimalLineup:
    week: int
    assignments: list[SlotAssignment]
    bench: list[tuple[Player, float]]
    projected_total: float

    def as_dict(self) -> dict:
        return {
            "week": self.week,
            "projected_total": self.projected_total,
            "starters": [
                {
                    "slot": a.slot,
                    "player": a.player.label() if a.player else "EMPTY",
                    "player_id": a.player.player_id if a.player else None,
                    "points": a.points,
                    "opponent": a.opponent,
                    "note": a.note,
                }
                for a in self.assignments
            ],
            "bench": [
                {"player": p.label(), "player_id": p.player_id, "points": pts}
                for p, pts in self.bench
            ],
        }


def _effective_points(player: Player, proj: Projection | None) -> tuple[float, str]:
    """Projected points with injury and bye adjustments."""
    if proj is None:
        return 0.0, "no projection"
    pts = proj.points
    if not proj.has_game:
        return 0.0, "bye or no game"
    if player.is_out:
        return 0.0, f"{player.injury_status}, will not play"
    if (player.injury_status or "").upper() == "DOUBTFUL":
        return round(pts * 0.35, 2), "doubtful, heavy discount"
    if (player.injury_status or "").upper() == "QUESTIONABLE":
        return round(pts * 0.88, 2), "questionable, small discount"
    return pts, ""


def eligibility_map(slots: list[str], players: dict[str, Player]) -> dict[str, set[str]]:
    """pid -> the set of slot names that player may legally fill."""
    distinct = set(slots)
    return {
        pid: {slot for slot in distinct if player.eligible_for(slot)}
        for pid, player in players.items()
    }


def optimize_points(
    slots: list[str],
    eligibility: dict[str, set[str]],
    scores: dict[str, float],
) -> tuple[float, list[str | None]]:
    """Exact best assignment of players to slots. The hot inner primitive.

    Deliberately knows nothing about League, Player, or projections: it is just
    slots, who may fill them, and what they are worth. That keeps it callable
    from simulation loops that solve this tens of thousands of times, where
    rebuilding domain objects per solve would dominate the runtime.

    Returns (total points, player_id per slot with None for an unfilled slot).
    """
    if not slots:
        return 0.0, []
    pool = list(eligibility)
    if not pool:
        return 0.0, [None] * len(slots)

    # Pad the player side so the matrix is always rows(slots) <= cols(players).
    padded = pool + [""] * max(0, len(slots) - len(pool))

    cost = []
    for slot in slots:
        row = []
        for pid in padded:
            allowed = eligibility.get(pid)
            if not allowed or slot not in allowed:
                row.append(BIG_COST)              # forbidden pairing
            else:
                row.append(-scores.get(pid, 0.0))  # negate: min cost == max points
        cost.append(row)

    assignment = hungarian(cost)

    total = 0.0
    filled: list[str | None] = []
    for idx, slot in enumerate(slots):
        col = assignment[idx]
        pid = padded[col] if 0 <= col < len(padded) else ""
        allowed = eligibility.get(pid)
        if not pid or not allowed or slot not in allowed:
            filled.append(None)
            continue
        filled.append(pid)
        total += scores.get(pid, 0.0)
    return round(total, 2), filled


def optimize(
    league: League,
    player_ids: list[str],
    week: int,
    log: bool = False,
) -> OptimalLineup:
    slots = league.starting_slots
    players = load_players(player_ids)
    proj = week_projections(league, week)

    scored: dict[str, tuple[float, str]] = {}
    for pid in player_ids:
        player = players.get(pid)
        if not player:
            continue
        scored[pid] = _effective_points(player, proj.get(pid))

    pool = [pid for pid in player_ids if pid in players]
    if not pool or not slots:
        return OptimalLineup(week, [], [], 0.0)

    ordered = {pid: players[pid] for pid in pool}
    eligibility = eligibility_map(slots, ordered)
    _, filled = optimize_points(
        slots, eligibility, {pid: scored[pid][0] for pid in pool}
    )

    used: set[str] = set()
    results: list[SlotAssignment] = []
    for idx, slot in enumerate(slots):
        pid = filled[idx]
        player = players.get(pid) if pid else None
        if player is None:
            results.append(SlotAssignment(slot, None, 0.0, note="no eligible player"))
            continue
        pts, note = scored[pid]
        p = proj.get(pid)
        results.append(
            SlotAssignment(slot, player, pts, p.opponent if p else None, note)
        )
        used.add(pid)

    bench = sorted(
        ((players[pid], scored[pid][0]) for pid in pool if pid not in used),
        key=lambda t: -t[1],
    )
    total = round(sum(a.points for a in results), 2)
    result = OptimalLineup(week, results, bench, total)

    if log:
        log_recommendation(
            league.league_id, league.season, week, "start_sit", None, result.as_dict()
        )
    return result


def start_sit_advice(league: League, roster: dict, week: int) -> dict:
    """Compare the lineup currently set in Sleeper against the optimal one."""
    all_players = roster.get("players") or []
    current = [p for p in (roster.get("starters") or []) if p and p != "0"]

    optimal = optimize(league, all_players, week)
    players = load_players(all_players)
    proj = week_projections(league, week)

    current_total = 0.0
    for pid in current:
        player = players.get(pid)
        if player:
            current_total += _effective_points(player, proj.get(pid))[0]
    current_total = round(current_total, 2)

    optimal_ids = {a.player.player_id for a in optimal.assignments if a.player}
    current_ids = set(current)

    bench_up = []
    for pid in optimal_ids - current_ids:
        slot = next(
            (a.slot for a in optimal.assignments if a.player and a.player.player_id == pid),
            "",
        )
        pts = next(
            (a.points for a in optimal.assignments if a.player and a.player.player_id == pid),
            0.0,
        )
        bench_up.append(
            {
                "player": players[pid].label(),
                "player_id": pid,
                "slot": slot,
                "points": pts,
            }
        )

    sit_down = []
    for pid in current_ids - optimal_ids:
        player = players.get(pid)
        if not player:
            continue
        pts, note = _effective_points(player, proj.get(pid))
        sit_down.append(
            {"player": player.label(), "player_id": pid, "points": pts, "reason": note}
        )

    bench_up.sort(key=lambda d: -d["points"])
    sit_down.sort(key=lambda d: d["points"])

    return {
        "week": week,
        "current_projected": current_total,
        "optimal_projected": optimal.projected_total,
        "points_left_on_bench": round(optimal.projected_total - current_total, 2),
        "start": bench_up,
        "sit": sit_down,
        "optimal_lineup": optimal.as_dict(),
    }
