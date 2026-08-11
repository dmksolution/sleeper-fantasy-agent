"""Monte Carlo rollout of the rest of the draft.

Best-available is the wrong question. The right one is "which pick leaves my
roster best off after all fifteen rounds", and that depends on what comes back
to you. A running back and a receiver of equal value are not equal choices if
one lasts until your next turn and the other does not.

This answers it by playing the draft out. For each candidate you could take now,
force that pick, let everyone (including you) finish the draft under a fixed
policy, then score the finished roster by the starting lineups it would actually
produce, week by week, with real byes and extra weight on the fantasy playoffs.
Repeat a few hundred times with the market resampled and compare the means.

Doing it this way subsumes three things the old recommender bolted on as
separate hand-tuned terms, and deletes all of them:

  * positional need -- falls out, because a second quarterback does not improve
    any starting lineup
  * tier scarcity -- falls out, because if a tier empties before your next turn
    the rollout sees the drop
  * survival -- falls out, because opponents take players according to the
    market

The performance trick that makes it fit inside a 90 second pick clock: do not
run an argmin over every available player for every opponent pick. Draw one
noisy market order per trial and sort it once. Every opponent pick is then "the
next name on this trial's list that their roster can still take". Statistically
this is the same model, and it is about three orders of magnitude faster.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field

from .config import settings
from .draft import (
    DEFAULT_MARKET_WEIGHT,
    DraftState,
    DraftValue,
    blended_value,
    draft_status,
    value_board,
)
from .league import League, load_players
from .lineup import optimize_points
from .survival import adp_sigma, calibrate_scale, load_adp, survival_probability
from .valuation import PLAYOFFS, REGULAR, season_value

log = logging.getLogger(__name__)

# What a real manager will and will not do. These matter more than model
# sophistication: without them the simulated teams draft four quarterbacks and
# every ranking built on top is meaningless.
POSITION_CAPS = {"QB": 2, "RB": 6, "WR": 7, "TE": 3, "K": 1, "DEF": 1}

# Nobody takes a kicker in the sixth round. Enforcing this keeps the simulated
# market realistic in exactly the range where your late picks happen.
EARLIEST_KDEF_ROUND = 13

# Weeks 15-17 decide the season, so a roster that is strong then is worth more
# than one merely strong in October.
DEFAULT_PLAYOFF_WEIGHT = 1.5

# Only the top slice of the board is realistically draftable in 15 rounds.
DRAFT_POOL = 260


@dataclass
class SimConfig:
    trials: int = 300
    sigma_scale: float = 1.0
    playoff_weight: float = DEFAULT_PLAYOFF_WEIGHT
    market_weight: float = DEFAULT_MARKET_WEIGHT
    seed: int | None = 20260811


@dataclass
class CandidateResult:
    player_id: str
    player: str
    position: str
    adp: float | None
    mean_score: float
    p10: float
    p90: float
    regret: float
    survival_next_pick: float
    modal_plan: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "player": self.player,
            "player_id": self.player_id,
            "position": self.position,
            "adp": self.adp,
            "expected_roster_score": round(self.mean_score, 1),
            "p10": round(self.p10, 1),
            "p90": round(self.p90, 1),
            "regret_vs_best": round(self.regret, 1),
            "survival_to_next_pick": round(self.survival_next_pick, 3),
            "typical_next_rounds": self.modal_plan,
        }


# ------------------------------------------------------------- static setup


@dataclass
class SimContext:
    """Everything reusable across trials, computed once."""

    league: League
    board: list[DraftValue]
    values: dict[str, float]            # blended draft value
    positions: dict[str, str]
    adp: dict[str, float]
    weekly: dict[str, dict[int, float]]  # pid -> week -> points
    eligibility: dict[str, set[str]]
    slots: list[str]
    week_weights: dict[int, float]
    pool: list[str]
    starters_needed: dict[str, int]
    flex_slots: int


def build_context(
    league: League,
    board: list[DraftValue] | None = None,
    cfg: SimConfig | None = None,
) -> SimContext:
    cfg = cfg or SimConfig()
    board = board if board is not None else value_board(league)
    values = blended_value(league, board, cfg.market_weight)

    # Only players good enough to be drafted matter; the rest never come up.
    ordered = sorted(board, key=lambda b: -values.get(b.player_id, b.vbd))[:DRAFT_POOL]
    pool = [b.player_id for b in ordered]
    positions = {b.player_id: b.position for b in ordered}

    sv = season_value(league, player_ids=pool)
    weekly = {pid: sv[pid].weekly for pid in pool if pid in sv}

    slots = league.starting_slots
    players = load_players(pool)
    distinct = set(slots)
    eligibility = {
        pid: {s for s in distinct if p.eligible_for(s)} for pid, p in players.items()
    }

    week_weights = {w: 1.0 for w in REGULAR}
    week_weights.update({w: cfg.playoff_weight for w in PLAYOFFS})

    starters_needed: dict[str, int] = {}
    flex_slots = 0
    for slot in slots:
        if slot in ("QB", "RB", "WR", "TE", "K", "DEF"):
            starters_needed[slot] = starters_needed.get(slot, 0) + 1
        else:
            flex_slots += 1

    return SimContext(
        league=league,
        board=ordered,
        values=values,
        positions=positions,
        adp={b.player_id: b.adp for b in ordered if b.adp},
        weekly=weekly,
        eligibility=eligibility,
        slots=slots,
        week_weights=week_weights,
        pool=pool,
        starters_needed=starters_needed,
        flex_slots=flex_slots,
    )


# ------------------------------------------------------------------ scoring


def score_roster(ctx: SimContext, player_ids: list[str]) -> float:
    """Sum of optimal weekly lineups, playoff weeks weighted up.

    This is where the Phase 0 work pays off: real weekly projections mean byes
    and playoff schedules are already baked in, so a roster whose stars all rest
    in week 11 scores worse without any special-case bye logic.
    """
    elig = {pid: ctx.eligibility[pid] for pid in player_ids if pid in ctx.eligibility}
    if not elig:
        return 0.0
    total = 0.0
    for week, weight in ctx.week_weights.items():
        scores = {}
        for pid in elig:
            wk = ctx.weekly.get(pid)
            if wk:
                pts = wk.get(week)
                if pts:
                    scores[pid] = pts
        if not scores:
            continue
        best, _ = optimize_points(ctx.slots, elig, scores)
        total += best * weight
    return total


# -------------------------------------------------------------- draft policy


def _can_take(position: str, counts: dict[str, int], rnd: int, rounds: int, needed: int) -> bool:
    """Would a real manager take this position here?"""
    if counts.get(position, 0) >= POSITION_CAPS.get(position, 99):
        return False
    # Nobody rosters a backup quarterback in round 2 of a single-QB league.
    if position == "QB" and counts.get("QB", 0) >= 1 and rnd < 10:
        return False
    if position in ("K", "DEF"):
        if rnd < EARLIEST_KDEF_ROUND:
            return False
    else:
        # Reserve the last rounds for mandatory kicker and defense.
        if rounds - rnd + 1 <= needed:
            return False
    return True


def _mandatory_remaining(counts: dict[str, int]) -> int:
    return sum(1 for pos in ("K", "DEF") if counts.get(pos, 0) < 1)


# Bench players are worth a fraction of starters, because they only score when
# somebody ahead of them is hurt or on bye. Without this the value-greedy policy
# happily drafts five running backs -- each one genuinely the highest value
# player left, and each one contributing nothing to a starting lineup.
BENCH_DECAY = 0.55
FLEX_MULTIPLIER = 0.9


def _marginal_multiplier(
    ctx: SimContext, position: str, counts: dict[str, int]
) -> float:
    """How much a player at this position is really worth as the Nth of his kind.

    A cheap stand-in for "how much would this actually raise my weekly starting
    lineup". Evaluating that properly would mean re-optimizing seventeen weeks
    for every candidate at every pick, which is far too slow inside a rollout.
    """
    have = counts.get(position, 0)
    required = ctx.starters_needed.get(position, 0)
    if have < required:
        return 1.0

    if position in ("RB", "WR", "TE"):
        # Flex slots are shared, so count the surplus across all flex-eligible
        # positions rather than pretending each one has its own flex.
        surplus = sum(
            max(0, counts.get(p, 0) - ctx.starters_needed.get(p, 0))
            for p in ("RB", "WR", "TE")
        )
        if surplus < ctx.flex_slots:
            return FLEX_MULTIPLIER
        depth = surplus - ctx.flex_slots
        return FLEX_MULTIPLIER * (BENCH_DECAY ** (depth + 1))

    # A second quarterback, or any extra kicker or defense, is nearly worthless
    # in a single-QB league with five bench spots.
    return BENCH_DECAY ** (have - required + 1) * 0.5


def _pick_for(
    ctx: SimContext,
    order: list[str],
    cursor: int,
    taken: set[str],
    counts: dict[str, int],
    rnd: int,
    rounds: int,
) -> tuple[str | None, int]:
    """Next name on this trial's market order that this roster can take."""
    needed = _mandatory_remaining(counts)
    i = cursor
    n = len(order)
    fallback = None
    while i < n:
        pid = order[i]
        if pid in taken:
            if i == cursor:
                cursor += 1
            i += 1
            continue
        pos = ctx.positions.get(pid, "")
        if _can_take(pos, counts, rnd, rounds, needed):
            return pid, cursor
        if fallback is None:
            fallback = pid
        i += 1
    return fallback, cursor


def _my_pick(
    ctx: SimContext,
    available: list[str],
    taken: set[str],
    counts: dict[str, int],
    rnd: int,
    rounds: int,
) -> str | None:
    """Our own policy inside the rollout: best blended value we can legally use.

    Intentionally simple. Every candidate branch runs the identical policy after
    its forced first pick, so the comparison between candidates stays clean; a
    cleverer policy would change all branches equally and cost a great deal of
    time per trial.
    """
    needed = _mandatory_remaining(counts)
    best_pid, best_score = None, float("-inf")
    seen = 0
    for pid in available:
        if pid in taken:
            continue
        pos = ctx.positions.get(pid, "")
        if not _can_take(pos, counts, rnd, rounds, needed):
            continue
        # `available` is sorted by value, so the best marginal pick is always
        # near the front. Scanning the top slice keeps this O(1) per pick.
        seen += 1
        if seen > 25:
            break
        score = ctx.values.get(pid, 0.0) * _marginal_multiplier(ctx, pos, counts)
        if score > best_score:
            best_score, best_pid = score, pid
    if best_pid is not None:
        return best_pid
    for pid in available:
        if pid not in taken:
            return pid
    return None


def _slot_on_clock(overall: int, teams: int, snake: bool) -> int:
    rnd = (overall - 1) // teams + 1
    idx = (overall - 1) % teams
    return (teams - idx) if (snake and rnd % 2 == 0) else (idx + 1)


def my_pick_numbers(my_slot: int, teams: int, rounds: int, snake: bool = True) -> list[int]:
    return [
        overall
        for overall in range(1, teams * rounds + 1)
        if _slot_on_clock(overall, teams, snake) == my_slot
    ]


# ----------------------------------------------------------------- rollout


def simulate_from(
    ctx: SimContext,
    my_slot: int,
    teams: int,
    rounds: int,
    already_taken: set[str],
    my_existing: list[str],
    next_overall: int,
    force_first: str | None,
    cfg: SimConfig,
    rng: random.Random,
) -> tuple[float, list[str]]:
    """Play out one draft. Returns (weighted roster score, my positions taken)."""
    # One noisy market order for this trial, sorted once.
    keys = []
    for pid in ctx.pool:
        if pid in already_taken:
            continue
        a = ctx.adp.get(pid)
        if a is None:
            a = float(teams * rounds + 40)
            sigma = adp_sigma(a, cfg.sigma_scale) * 2
        else:
            sigma = adp_sigma(a, cfg.sigma_scale)
        keys.append((a + rng.gauss(0.0, sigma), pid))
    keys.sort()
    order = [pid for _, pid in keys]

    # Our own preference order is by value, not by market noise.
    my_order = sorted(
        (pid for pid in ctx.pool if pid not in already_taken),
        key=lambda p: -ctx.values.get(p, 0.0),
    )

    taken = set(already_taken)
    counts: dict[str, dict[str, int]] = {}
    for slot in range(1, teams + 1):
        counts[str(slot)] = {}
    mine = list(my_existing)
    for pid in mine:
        pos = ctx.positions.get(pid, "")
        counts[str(my_slot)][pos] = counts[str(my_slot)].get(pos, 0) + 1

    my_positions: list[str] = []
    cursor = 0
    forced = force_first

    for overall in range(next_overall, teams * rounds + 1):
        rnd = (overall - 1) // teams + 1
        slot = _slot_on_clock(overall, teams, True)
        key = str(slot)

        if slot == my_slot:
            if forced is not None:
                pid = forced
                forced = None
            else:
                pid = _my_pick(ctx, my_order, taken, counts[key], rnd, rounds)
            if pid is None:
                continue
            mine.append(pid)
            my_positions.append(ctx.positions.get(pid, "?"))
        else:
            pid, cursor = _pick_for(ctx, order, cursor, taken, counts[key], rnd, rounds)
            if pid is None:
                continue

        taken.add(pid)
        pos = ctx.positions.get(pid, "")
        counts[key][pos] = counts[key].get(pos, 0) + 1

    return score_roster(ctx, mine), my_positions


def evaluate_candidates(
    league: League,
    draft_id: str | None = None,
    candidates: int = 8,
    trials: int = 300,
    assumed_slot: int | None = None,
    cfg: SimConfig | None = None,
    state: DraftState | None = None,
    ctx: SimContext | None = None,
) -> dict:
    """Rank the picks you could make right now by how the draft tends to end."""
    cfg = cfg or SimConfig(trials=trials)
    state = state if state is not None else draft_status(league, draft_id, assumed_slot)
    ctx = ctx or build_context(league, cfg=cfg)

    teams = state.teams if state else league.team_count
    rounds = state.rounds if state else 15
    my_slot = (state.my_slot if state else None) or assumed_slot
    already = set(state.drafted_ids) if state else set()
    mine = list(state.my_picks) if state else []
    next_overall = (state.next_pick_overall if state else 1) or 1

    if my_slot is None:
        return {
            "error": "no draft slot known",
            "hint": "pass assumed_slot, or use draft_plan() to see all slots",
        }

    # Calibrate the market's spread against how this league has actually drafted.
    scale_detail = {"calibrated": False, "scale": 1.0}
    if state and state.picks:
        cfg.sigma_scale, scale_detail = calibrate_scale(state.picks, ctx.adp)

    picks = my_pick_numbers(my_slot, teams, rounds)
    upcoming = [p for p in picks if p >= next_overall]
    if not upcoming:
        return {"error": "your draft is over", "my_draft_slot": my_slot}
    # Evaluate as of YOUR pick, not the pick currently on the clock. Asked
    # before the draft from slot 7, the useful question is "who should I take
    # at 7", not "who would I take at 1" -- the latter recommends players who
    # will never reach you.
    my_pick_overall = upcoming[0]
    my_next = upcoming[1] if len(upcoming) > 1 else None

    ranked_pool = [
        pid
        for pid in sorted(ctx.pool, key=lambda p: -ctx.values.get(p, 0.0))
        if pid not in already
    ]

    # Drop players who will almost certainly be gone before your turn.
    reachable = []
    dropped = []
    for pid in ranked_pool:
        a = ctx.adp.get(pid)
        if my_pick_overall > next_overall and a is not None:
            p = survival_probability(
                a, adp_sigma(a, cfg.sigma_scale), next_overall, my_pick_overall
            )
            if p < 0.05:
                dropped.append(pid)
                continue
        reachable.append(pid)
    shortlist = reachable[: candidates * 3]

    # Do not waste trials on a third quarterback.
    counts: dict[str, int] = {}
    for pid in mine:
        pos = ctx.positions.get(pid, "")
        counts[pos] = counts.get(pos, 0) + 1
    rnd_now = (my_pick_overall - 1) // teams + 1
    needed = _mandatory_remaining(counts)
    shortlist = [
        pid
        for pid in shortlist
        if _can_take(ctx.positions.get(pid, ""), counts, rnd_now, rounds, needed)
    ][:candidates]

    by_pos = {b.player_id: b for b in ctx.board}
    results: list[CandidateResult] = []

    for pid in shortlist:
        scores: list[float] = []
        plans: list[tuple[str, ...]] = []
        rng = random.Random(cfg.seed)
        for _ in range(cfg.trials):
            score, plan = simulate_from(
                ctx, my_slot, teams, rounds, already, mine, next_overall, pid, cfg, rng
            )
            scores.append(score)
            plans.append(tuple(plan[:4]))
        scores.sort()
        n = len(scores)
        mean = sum(scores) / n
        modal = max(set(plans), key=plans.count) if plans else ()

        a = ctx.adp.get(pid)
        surv = 1.0
        if my_next is not None and a is not None:
            # Probability he would still be there if you passed now and waited
            # a full turn. This is the column that decides close calls.
            surv = survival_probability(
                a, adp_sigma(a, cfg.sigma_scale), my_pick_overall, my_next
            )

        item = by_pos.get(pid)
        results.append(
            CandidateResult(
                player_id=pid,
                player=f"{item.name} ({item.position}-{item.team or 'FA'})" if item else pid,
                position=ctx.positions.get(pid, "?"),
                adp=round(a, 1) if a else None,
                mean_score=mean,
                p10=scores[int(n * 0.10)],
                p90=scores[int(n * 0.90)],
                regret=0.0,
                survival_next_pick=surv,
                modal_plan=list(modal),
            )
        )

    results.sort(key=lambda r: -r.mean_score)
    if results:
        best = results[0].mean_score
        for r in results:
            r.regret = best - r.mean_score

    return {
        "draft_status": state.status if state else "not started",
        "picks_made": state.picks_made if state else 0,
        "my_draft_slot": my_slot,
        "slot_source": state.slot_source if state else "assumed",
        "pick_on_the_clock": next_overall,
        "evaluating_your_pick_at": my_pick_overall,
        "your_following_pick": my_next,
        "candidates_dropped_as_unreachable": len(dropped),
        "picks_until_my_turn": state.picks_until_my_turn if state else None,
        "trials_per_candidate": cfg.trials,
        "market_calibration": scale_detail,
        "how_to_read": (
            "expected_roster_score is the weighted sum of optimal weekly lineups"
            " over the whole season, playoff weeks counted 1.5x. Compare"
            " regret_vs_best against survival_to_next_pick: if the top choice is"
            " unlikely to last and the second is, take the top one now even when"
            " the scores are close."
        ),
        "recommendations": [r.as_dict() for r in results],
    }


def precompute(league: League, draft_id: str | None = None) -> dict:
    """Warm everything the draft needs, the night before.

    Two things make draft night slow: the first read of the projection cache
    (several seconds on a network drive) and building the value board. Neither
    should happen while a 90 second clock is running. This does both, persists
    the board into the existing `recommendations` table, and reports what it
    found so you can check the setup before it matters.
    """
    import time

    from .store import log_recommendation

    started = time.perf_counter()
    ctx = build_context(league)
    board_time = time.perf_counter() - started

    state = draft_status(league, draft_id)
    dissent = None
    try:
        from .draft import disagreements

        dissent = disagreements(league, ctx.board, top_n=10)
    except Exception as exc:  # noqa: BLE001
        log.warning("disagreement report failed: %s", exc)

    payload = {
        "board": [b.as_dict() for b in ctx.board[:200]],
        "blended_values": {pid: ctx.values[pid] for pid in ctx.pool[:200]},
        "generated_in_seconds": round(board_time, 2),
    }
    log_recommendation(
        league.league_id, league.season, 0, "draft_precompute", None, payload
    )

    return {
        "ok": True,
        "seconds_to_build": round(time.perf_counter() - started, 2),
        "players_on_board": len(ctx.board),
        "draft_pool": len(ctx.pool),
        "weeks_of_projections": len(ctx.week_weights),
        "draft": {
            "status": state.status if state else "no draft found",
            "rounds": state.rounds if state else None,
            "teams": state.teams if state else None,
            "my_slot": state.my_slot if state else None,
            "slot_source": state.slot_source if state else "unknown",
            "warnings": state.warnings if state else [],
        },
        "top_disagreements": (dissent or {}).get("we_like_more_than_market", [])[:5],
        "note": (
            "Board cached in the recommendations table and the projection cache"
            " is warm. Keep this process alive with `draft --watch` for instant"
            " picks, or expect a few seconds on the first call in a new process."
        ),
    }


def watch_draft(
    league: League,
    draft_id: str | None = None,
    assumed_slot: int | None = None,
    trials: int = 200,
    candidates: int = 8,
    interval: float = 5.0,
) -> None:
    """Poll the live draft and reprint recommendations whenever a pick lands.

    Keeping one process alive is the whole point: the context is built once, so
    every refresh after the first is fast enough to be useful on a 90 second
    clock. Also alarms when your turn is close, because the real failure mode
    on draft night is not a bad pick, it is autopick making one for you.
    """
    import time

    print("Building the board (once)...", flush=True)
    ctx = build_context(league)
    last_count = -1
    print(f"Ready. Polling every {interval:g}s. Ctrl-C to stop.\n", flush=True)

    try:
        while True:
            state = draft_status(league, draft_id, assumed_slot)
            if state is None:
                print("No draft found for this league.")
                return
            if state.picks_made != last_count:
                last_count = state.picks_made
                print("\033[2J\033[H", end="")  # clear screen
                print(
                    f"{league.name} | {state.status} | {state.picks_made} picks made"
                    f" | polling every {interval:g}s"
                )
                for w in state.warnings:
                    print(f"  ! {w}")

                until = state.picks_until_my_turn
                if until == 0:
                    print("\n*** YOU ARE ON THE CLOCK ***\a")
                elif until is not None and until <= 2:
                    print(f"\n*** {until} PICKS UNTIL YOUR TURN ***\a")
                elif until is not None:
                    print(f"\n{until} picks until your turn")

                result = evaluate_candidates(
                    league,
                    draft_id,
                    candidates=candidates,
                    trials=trials,
                    assumed_slot=assumed_slot,
                    state=state,
                    ctx=ctx,
                )
                if result.get("error"):
                    print(result)
                else:
                    print()
                    print(
                        f"{'Player':<32}{'Pos':<5}{'ADP':>7}{'Score':>9}"
                        f"{'Regret':>8}{'Lasts':>7}"
                    )
                    print("-" * 68)
                    for r in result["recommendations"]:
                        adp = f"{r['adp']:.1f}" if r["adp"] else "-"
                        print(
                            f"{r['player'][:31]:<32}{r['position']:<5}{adp:>7}"
                            f"{r['expected_roster_score']:>9.1f}"
                            f"{r['regret_vs_best']:>8.1f}"
                            f"{r['survival_to_next_pick']:>7.2f}"
                        )
                    if state.my_picks:
                        from .league import load_players

                        mine = load_players(state.my_picks)
                        print(
                            "\nYour roster: "
                            + ", ".join(p.label() for p in mine.values())
                        )
            if state.status == "complete":
                print("\nDraft complete.")
                return
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")


def draft_plan(
    league: League,
    slot: int | None = None,
    trials: int = 200,
    cfg: SimConfig | None = None,
) -> dict:
    """What the draft tends to look like from each slot, before it starts.

    Useful precisely because the draft order is usually not set until the last
    minute: it turns "I do not know where I am picking" into a study aid rather
    than a blocker.
    """
    cfg = cfg or SimConfig(trials=trials)
    ctx = build_context(league, cfg=cfg)
    teams = league.team_count
    rounds = 15

    slots = [slot] if slot else list(range(1, teams + 1))
    rows = []
    for s in slots:
        rng = random.Random(cfg.seed)
        scores, plans = [], []
        for _ in range(cfg.trials):
            score, plan = simulate_from(
                ctx, s, teams, rounds, set(), [], 1, None, cfg, rng
            )
            scores.append(score)
            plans.append(tuple(plan[:5]))
        scores.sort()
        modal = max(set(plans), key=plans.count)
        picks = my_pick_numbers(s, teams, rounds)
        rows.append(
            {
                "slot": s,
                "first_picks": picks[:4],
                "expected_roster_score": round(sum(scores) / len(scores), 1),
                "p10": round(scores[int(len(scores) * 0.1)], 1),
                "p90": round(scores[int(len(scores) * 0.9)], 1),
                "typical_opening": list(modal),
            }
        )

    rows.sort(key=lambda r: -r["expected_roster_score"])
    return {
        "season": league.season,
        "teams": teams,
        "rounds": rounds,
        "trials_per_slot": cfg.trials,
        "note": (
            "Scores across slots are close by construction -- snake drafts are"
            " roughly fair. The useful column is typical_opening: the position"
            " sequence that tends to come back to you from each slot."
        ),
        "slots": rows,
    }
