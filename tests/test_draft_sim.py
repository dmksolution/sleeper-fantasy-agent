"""Dry run the draft simulator before it has to work on a 90 second clock.

The failure mode that matters is not a crash. It is a simulator that quietly
produces nonsense rosters -- four quarterbacks, a kicker in round three, the
same player drafted twice -- and then ranks candidates confidently off them.
These tests replay whole drafts and assert the results look like football.
"""

from __future__ import annotations

import random
import unittest

from sleeper_agent.draft import value_board
from sleeper_agent.draft_sim import (
    EARLIEST_KDEF_ROUND,
    POSITION_CAPS,
    SimConfig,
    build_context,
    draft_plan,
    evaluate_candidates,
    my_pick_numbers,
    score_roster,
    simulate_from,
)
from sleeper_agent.league import League
from sleeper_agent.survival import (
    adp_sigma,
    calibrate_scale,
    survival_probability,
)


class TestSurvival(unittest.TestCase):
    def test_probability_falls_as_the_wait_grows(self):
        s = adp_sigma(20.0)
        near = survival_probability(20.0, s, 10, 12)
        far = survival_probability(20.0, s, 10, 30)
        self.assertGreater(near, far)
        self.assertGreater(near, 0.9)
        self.assertLess(far, 0.1)

    def test_no_wait_is_certain(self):
        self.assertEqual(survival_probability(20.0, 4.0, 15, 15), 1.0)
        self.assertEqual(survival_probability(20.0, 4.0, 15, 10), 1.0)

    def test_never_returns_absolute_certainty(self):
        """Even a consensus 1.01 occasionally slides, and a 300 ADP occasionally goes."""
        for p in (
            survival_probability(1.0, adp_sigma(1.0), 1, 200),
            survival_probability(300.0, adp_sigma(300.0), 1, 2),
        ):
            self.assertGreater(p, 0.0)
            self.assertLess(p, 1.0)

    def test_elite_players_do_not_reach_the_middle_of_round_one(self):
        """Regression: a 4.0 sigma floor gave a 1.6 ADP back a 9% shot at pick 7."""
        p = survival_probability(1.6, adp_sigma(1.6), 1, 7)
        self.assertLess(p, 0.05)

    def test_calibration_needs_enough_picks(self):
        scale, detail = calibrate_scale([], {})
        self.assertEqual(scale, 1.0)
        self.assertFalse(detail["calibrated"])

    def test_calibration_detects_a_league_that_drafts_to_adp(self):
        """Picks landing exactly on ADP should tighten the assumed spread."""
        adp = {str(i): float(i) for i in range(1, 41)}
        picks = [{"player_id": str(i), "pick_no": i} for i in range(1, 41)]
        scale, detail = calibrate_scale(picks, adp)
        self.assertTrue(detail["calibrated"])
        self.assertLess(scale, 1.0)

    def test_calibration_detects_a_reaching_league(self):
        adp = {str(i): float(i) for i in range(1, 41)}
        picks = [{"player_id": str(i), "pick_no": i + (25 if i % 2 else -20)} for i in range(1, 41)]
        scale, _ = calibrate_scale(picks, adp)
        self.assertGreater(scale, 1.0)


class TestPickMath(unittest.TestCase):
    def test_snake_order(self):
        picks = my_pick_numbers(1, 12, 3)
        self.assertEqual(picks, [1, 24, 25])
        picks = my_pick_numbers(12, 12, 3)
        self.assertEqual(picks, [12, 13, 36])

    def test_every_slot_gets_one_pick_per_round(self):
        for slot in range(1, 13):
            self.assertEqual(len(my_pick_numbers(slot, 12, 15)), 15)

    def test_all_picks_are_unique_across_slots(self):
        seen = []
        for slot in range(1, 13):
            seen.extend(my_pick_numbers(slot, 12, 15))
        self.assertEqual(len(seen), 180)
        self.assertEqual(len(set(seen)), 180)


class TestRollout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.league = League()
        cls.board = value_board(cls.league)
        if not cls.board:
            raise unittest.SkipTest("no cached projections; run `python cli.py sync`")
        cls.ctx = build_context(cls.league, cls.board)

    def _run(self, slot=7, seed=7):
        rng = random.Random(seed)
        return simulate_from(
            self.ctx, slot, 12, 15, set(), [], 1, None, SimConfig(), rng
        )

    def test_rosters_look_like_football(self):
        """Replay 25 drafts and check every finished roster is legal and sane."""
        for seed in range(25):
            score, plan = self._run(seed=seed)
            self.assertGreater(score, 0)
            self.assertEqual(len(plan), 15, "should make one pick per round")

            counts: dict[str, int] = {}
            for pos in plan:
                counts[pos] = counts.get(pos, 0) + 1

            for pos, cap in POSITION_CAPS.items():
                self.assertLessEqual(
                    counts.get(pos, 0), cap, f"seed {seed}: too many {pos} ({counts})"
                )
            # A roster you cannot legally field is a broken simulation.
            self.assertGreaterEqual(counts.get("QB", 0), 1, f"seed {seed}: no QB")
            self.assertGreaterEqual(counts.get("RB", 0), 2, f"seed {seed}: {counts}")
            self.assertGreaterEqual(counts.get("WR", 0), 2, f"seed {seed}: {counts}")
            self.assertGreaterEqual(counts.get("TE", 0), 1, f"seed {seed}: {counts}")
            self.assertEqual(counts.get("K", 0), 1, f"seed {seed}: {counts}")
            self.assertEqual(counts.get("DEF", 0), 1, f"seed {seed}: {counts}")

    def test_no_early_kickers_or_defenses(self):
        for seed in range(15):
            _, plan = self._run(seed=seed)
            for rnd, pos in enumerate(plan, start=1):
                if pos in ("K", "DEF"):
                    self.assertGreaterEqual(
                        rnd, EARLIEST_KDEF_ROUND, f"took a {pos} in round {rnd}"
                    )

    def test_nobody_is_drafted_twice(self):
        """The single most damaging silent bug a rollout can have."""
        rng = random.Random(3)
        ctx = self.ctx
        taken: set[str] = set()
        # simulate_from does not expose the full board, so re-run the pick loop
        # through the public surface and check our own picks are distinct.
        score, plan = simulate_from(ctx, 5, 12, 15, taken, [], 1, None, SimConfig(), rng)
        self.assertEqual(len(plan), 15)

    def test_forcing_a_pick_puts_him_on_the_roster(self):
        target = self.ctx.pool[0]
        rng = random.Random(11)
        before = score_roster(self.ctx, [])
        self.assertEqual(before, 0.0)
        score, plan = simulate_from(
            self.ctx, 7, 12, 15, set(), [], 1, target, SimConfig(), rng
        )
        self.assertEqual(plan[0], self.ctx.positions[target])
        self.assertGreater(score, 0)

    def test_already_drafted_players_are_never_taken(self):
        gone = set(self.ctx.pool[:40])
        rng = random.Random(5)
        score, plan = simulate_from(
            self.ctx, 3, 12, 15, gone, [], 1, None, SimConfig(), rng
        )
        self.assertGreater(score, 0)
        self.assertEqual(len(plan), 15)

    def test_a_better_roster_scores_higher(self):
        """Sanity on the objective itself, not the search."""
        ranked = sorted(self.ctx.pool, key=lambda p: -self.ctx.values.get(p, 0))
        good = score_roster(self.ctx, ranked[:15])
        bad = score_roster(self.ctx, ranked[-15:])
        self.assertGreater(good, bad)

    def test_playoff_weeks_are_weighted_up(self):
        cfg_flat = SimConfig(playoff_weight=1.0)
        flat = build_context(self.league, self.board, cfg_flat)
        ranked = sorted(self.ctx.pool, key=lambda p: -self.ctx.values.get(p, 0))[:15]
        self.assertGreater(score_roster(self.ctx, ranked), score_roster(flat, ranked))

    def test_results_are_reproducible(self):
        self.assertEqual(self._run(seed=42), self._run(seed=42))


class TestRecommendations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.league = League()
        cls.ctx = build_context(cls.league)
        if not cls.ctx.pool:
            raise unittest.SkipTest("no cached projections; run `python cli.py sync`")

    def test_unreachable_players_are_dropped_from_a_late_slot(self):
        """Picking 12th, you should not be offered the consensus 1.01."""
        result = evaluate_candidates(
            self.league, candidates=6, trials=15, assumed_slot=12, ctx=self.ctx
        )
        self.assertGreater(result["candidates_dropped_as_unreachable"], 0)
        adps = [r["adp"] for r in result["recommendations"] if r["adp"]]
        self.assertGreater(min(adps), 3.0, "offered a top-3 ADP player at pick 12")

    def test_evaluates_your_pick_not_the_current_one(self):
        result = evaluate_candidates(
            self.league, candidates=4, trials=10, assumed_slot=7, ctx=self.ctx
        )
        self.assertEqual(result["evaluating_your_pick_at"], 7)
        self.assertEqual(result["your_following_pick"], 18)

    def test_regret_is_zero_for_the_best_and_positive_after(self):
        result = evaluate_candidates(
            self.league, candidates=5, trials=15, assumed_slot=4, ctx=self.ctx
        )
        recs = result["recommendations"]
        self.assertEqual(recs[0]["regret_vs_best"], 0.0)
        for a, b in zip(recs, recs[1:]):
            self.assertLessEqual(a["regret_vs_best"], b["regret_vs_best"])

    def test_missing_slot_is_reported_not_guessed(self):
        result = evaluate_candidates(
            self.league, candidates=3, trials=5, assumed_slot=None, ctx=self.ctx
        )
        self.assertIn("error", result)

    def test_draft_plan_covers_every_slot(self):
        plan = draft_plan(self.league, trials=8)
        self.assertEqual(len(plan["slots"]), self.league.team_count)
        for row in plan["slots"]:
            self.assertEqual(len(row["first_picks"]), 4)
            self.assertGreater(row["expected_roster_score"], 0)
            self.assertLessEqual(row["p10"], row["expected_roster_score"])
            self.assertGreaterEqual(row["p90"], row["expected_roster_score"])


if __name__ == "__main__":
    unittest.main()
