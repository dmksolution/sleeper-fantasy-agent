"""Will he still be there at my next pick?

The question "who is the best player available" is the wrong one. The right one
is "which pick leaves my roster best off", and that depends on who survives to
your next turn. Taking a player who would have lasted twelve more picks, over
one who will be gone in two, costs you the difference for free.

Sleeper publishes an average draft position but no standard deviation, so the
spread has to be assumed and then corrected against the draft as it happens.
That correction is the part worth having: a league of ADP followers has a tight
spread and you should wait longer on everyone, while a league of homers has a
wide one and waiting gets your guy sniped. Ten leagues drafting the same board
should not get the same advice, and after fifteen picks they no longer do.

Model: draft position D is normal around ADP with standard deviation sigma,
truncated on the picks that have already happened.

    P(D > n1 | D > n0) = (1 - Phi((n1 - ADP)/sigma)) / (1 - Phi((n0 - ADP)/sigma))

`math.erf` gives Phi, so this stays stdlib.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from .league import League
from .store import connect

log = logging.getLogger(__name__)

# Spread grows with ADP: the consensus on the 1.01 is far tighter than on the
# 9th round. 20% of ADP matches the shape of published ADP dispersion well
# enough, with a floor so early picks are not treated as certainties.
SIGMA_FRACTION = 0.20
# The floor keeps early picks from being treated as certainties, but it has to
# stay small: at 4.0 picks a consensus 1.6 ADP back showed a 9% chance of
# lasting to pick 7, which is not a thing that happens. 2.0 keeps a little
# uncertainty at the top without inventing it.
SIGMA_FLOOR = 2.0

# Calibration is meaningless on a handful of picks and should not be able to
# run away on a weird draft.
MIN_PICKS_TO_CALIBRATE = 15
SCALE_MIN, SCALE_MAX = 0.5, 2.5

# Never report certainty. Even a consensus 1.01 occasionally slides.
P_MIN, P_MAX = 0.001, 0.999


def _phi(z: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def adp_sigma(adp: float, scale: float = 1.0) -> float:
    """Assumed standard deviation of a player's draft slot, in picks."""
    return max(SIGMA_FLOOR, SIGMA_FRACTION * adp) * max(scale, 0.01)


def load_adp(league: League) -> dict[str, float]:
    """player_id -> ADP in this league's scoring format."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT player_id, adp FROM adp WHERE season = ? AND format = ?",
            (league.season, league.scoring_format()),
        ).fetchall()
    return {r["player_id"]: float(r["adp"]) for r in rows if r["adp"]}


def survival_probability(
    adp: float, sigma: float, next_overall: int, my_next_overall: int
) -> float:
    """Probability a player lasts from `next_overall` until `my_next_overall`.

    `next_overall` is the pick about to be made; `my_next_overall` is your next
    turn. Conditioning on "still available now" is what makes this usable
    mid-draft: a player whose ADP has already passed is not treated as gone.
    """
    if my_next_overall <= next_overall:
        return 1.0
    z0 = (next_overall - adp) / sigma
    z1 = (my_next_overall - adp) / sigma
    tail_now = 1.0 - _phi(z0)
    if tail_now <= 1e-12:
        # ADP is far in the past and he is somehow still here, so the market
        # clearly does not want him. Treat as very likely to last.
        return P_MAX
    p = (1.0 - _phi(z1)) / tail_now
    return min(P_MAX, max(P_MIN, p))


def calibrate_scale(picks: list[dict], adp: dict[str, float]) -> tuple[float, dict]:
    """Rescale sigma from how far this league's picks actually stray from ADP.

    Compares the observed median absolute deviation of |pick_no - adp| against
    what the assumed sigma predicts. For a half-normal, E|X| = sigma * 0.7979
    and the median is sigma * 0.6745.
    """
    deviations = []
    for p in picks:
        pid = p.get("player_id")
        pick_no = p.get("pick_no")
        if not pid or not pick_no or pid not in adp:
            continue
        deviations.append((abs(pick_no - adp[pid]), adp[pid]))

    detail = {"picks_with_adp": len(deviations), "scale": 1.0, "calibrated": False}
    if len(deviations) < MIN_PICKS_TO_CALIBRATE:
        detail["note"] = (
            f"need {MIN_PICKS_TO_CALIBRATE} picks with known ADP to calibrate,"
            f" have {len(deviations)}"
        )
        return 1.0, detail

    observed = sorted(d for d, _ in deviations)
    obs_median = observed[len(observed) // 2]
    expected = sorted(0.6745 * adp_sigma(a) for _, a in deviations)
    exp_median = expected[len(expected) // 2] or 1.0

    scale = min(SCALE_MAX, max(SCALE_MIN, obs_median / exp_median))
    detail.update(
        {
            "scale": round(scale, 3),
            "calibrated": True,
            "observed_median_deviation": round(obs_median, 1),
            "expected_median_deviation": round(exp_median, 1),
            "reading": (
                "this league drafts closer to ADP than average, so you can wait longer"
                if scale < 0.85
                else "this league reaches more than average, so wait less on your targets"
                if scale > 1.15
                else "this league drafts about at ADP"
            ),
        }
    )
    return scale, detail


@dataclass
class SurvivalReport:
    scale: float
    next_overall: int
    my_next_overall: int | None
    probabilities: dict[str, float]
    detail: dict


def available_at(
    league: League,
    board,
    next_overall: int,
    my_next_overall: int | None,
    scale: float = 1.0,
    adp: dict[str, float] | None = None,
) -> SurvivalReport:
    """Survival probability for every player on the board.

    Players with no published ADP get a pseudo-ADP past the end of the draft
    with a wide sigma, which lands them near certainty rather than excluding
    them. Nobody is sniping an undrafted player two picks early.
    """
    adp = adp if adp is not None else load_adp(league)
    undrafted_adp = float(league.team_count * 15 + 40)

    probs: dict[str, float] = {}
    for item in board:
        pid = getattr(item, "player_id", None) or item["player_id"]
        if my_next_overall is None:
            probs[pid] = 1.0
            continue
        a = adp.get(pid, undrafted_adp)
        sigma = adp_sigma(a, scale) if pid in adp else adp_sigma(a, scale) * 2
        probs[pid] = round(
            survival_probability(a, sigma, next_overall, my_next_overall), 4
        )

    return SurvivalReport(
        scale=scale,
        next_overall=next_overall,
        my_next_overall=my_next_overall,
        probabilities=probs,
        detail={"players_with_adp": sum(1 for i in board if getattr(i, "player_id", "") in adp)},
    )
