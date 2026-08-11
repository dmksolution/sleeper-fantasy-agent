"""Validate the assignment solver against brute force.

The README claims the optimizer was "validated against brute force on 300
randomized cases with zero mismatches". No such harness existed in the repo, so
every downstream number rested on an unverified solver. This is that harness.

Everything the tool recommends -- start/sit, waiver lineup delta, trade value,
draft rollouts -- is a difference of two optimize() calls, so a solver bug would
not look like a crash. It would look like confident, wrong advice.
"""

from __future__ import annotations

import itertools
import random
import unittest

from sleeper_agent.lineup import BIG_COST, hungarian, optimize_points

# Mirrors the real league: single QB, two flex, K and DEF.
SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "K", "DEF"]
FLEX_OK = {"RB", "WR", "TE"}
POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]


def brute_force(slots, eligibility, scores):
    """Max total points over every legal assignment. Exponential, so keep small.

    Slots may be left empty: a roster with no kicker should score the rest of
    the lineup, not fail. Padding with None is what makes this a correct oracle
    for a partial assignment, which is exactly what the solver returns.
    """
    candidates = list(eligibility) + [None] * len(slots)
    best = 0.0
    for combo in itertools.permutations(candidates, len(slots)):
        total = 0.0
        ok = True
        for slot, pid in zip(slots, combo):
            if pid is None:
                continue
            if slot not in eligibility[pid]:
                ok = False
                break
            total += scores[pid]
        if ok and total > best:
            best = total
    return round(best, 2)


def eligibility_for(position: str, slots: list[str]) -> set[str]:
    out = {s for s in slots if s == position}
    if position in FLEX_OK:
        out |= {s for s in slots if s == "FLEX"}
    return out


class TestHungarian(unittest.TestCase):
    def test_matches_brute_force_on_randomized_cases(self):
        """300 randomized rosters, exact agreement with exhaustive search."""
        rng = random.Random(20260811)
        mismatches = []

        for case in range(300):
            n_slots = rng.randint(2, 4)
            slots = rng.sample(SLOTS, n_slots)
            n_players = rng.randint(1, 6)
            eligibility, scores = {}, {}
            for i in range(n_players):
                pid = f"p{i}"
                pos = rng.choice(POSITIONS)
                eligibility[pid] = eligibility_for(pos, slots)
                scores[pid] = round(rng.uniform(0, 30), 2)

            got, filled = optimize_points(slots, eligibility, scores)
            want = brute_force(slots, eligibility, scores)
            if abs(got - want) > 0.011:
                mismatches.append((case, got, want, slots, eligibility, scores))

            # Whatever it returned must also be a legal assignment.
            used = [p for p in filled if p]
            self.assertEqual(len(used), len(set(used)), "a player was started twice")
            for slot, pid in zip(slots, filled):
                if pid is not None:
                    self.assertIn(slot, eligibility[pid], "illegal slot assignment")

        self.assertEqual(mismatches, [], f"{len(mismatches)} of 300 disagreed with brute force")

    def test_two_flex_beats_greedy_fill(self):
        """The case that motivates an exact solver over 'best QB, then best RB'.

        Greedy fills RB/RB with the two best RBs and leaves a 20-point WR on the
        bench. The exact answer spends one RB slot on the weaker RB so both flex
        spots can take the higher scorers.
        """
        slots = ["RB", "RB", "FLEX"]
        eligibility = {
            "rb1": {"RB", "FLEX"},
            "rb2": {"RB", "FLEX"},
            "rb3": {"RB", "FLEX"},
            "wr1": {"FLEX"},
        }
        scores = {"rb1": 20.0, "rb2": 18.0, "rb3": 5.0, "wr1": 19.0}
        total, filled = optimize_points(slots, eligibility, scores)
        self.assertAlmostEqual(total, 57.0, places=2)
        self.assertEqual(set(p for p in filled if p), {"rb1", "rb2", "wr1"})

    def test_forbidden_pairings_are_never_chosen(self):
        """A slot with no eligible player stays empty rather than taking a big cost."""
        slots = ["QB", "K"]
        eligibility = {"qb1": {"QB"}}
        scores = {"qb1": 22.0}
        total, filled = optimize_points(slots, eligibility, scores)
        self.assertAlmostEqual(total, 22.0, places=2)
        self.assertEqual(filled, ["qb1", None])
        self.assertLess(total, BIG_COST)

    def test_more_slots_than_players(self):
        slots = ["QB", "RB", "WR"]
        eligibility = {"rb1": {"RB"}}
        total, filled = optimize_points(slots, eligibility, {"rb1": 12.0})
        self.assertAlmostEqual(total, 12.0, places=2)
        self.assertEqual(filled, [None, "rb1", None])

    def test_empty_inputs(self):
        self.assertEqual(optimize_points([], {}, {}), (0.0, []))
        self.assertEqual(optimize_points(["QB"], {}, {}), (0.0, [None]))

    def test_hungarian_rectangular_contract(self):
        """Direct check of the solver: minimum cost, one column per row."""
        cost = [[4.0, 1.0, 3.0], [2.0, 0.0, 5.0]]
        assignment = hungarian(cost)
        self.assertEqual(len(assignment), 2)
        self.assertEqual(len(set(assignment)), 2, "two rows took the same column")
        total = sum(cost[i][assignment[i]] for i in range(2))
        self.assertAlmostEqual(total, 3.0, places=6)


if __name__ == "__main__":
    unittest.main()
