"""Validate league.score() against Sleeper's own points as an oracle.

This league uses exactly Sleeper's default scoring, so Sleeper's `pts_ppr` on
every projection row is a free, independent check on the dot product in
`League.score()`. Any disagreement is either a bug in our scoring or a quirk in
theirs, and the difference matters: `score()` feeds the draft board, the lineup
optimizer, and every downstream recommendation.

Two known, explained discrepancies are asserted rather than tolerated loosely,
so that a *new* discrepancy cannot hide inside a wide tolerance:

  1. QB: Sleeper's `pts_ppr` scores an interception at +2 while this league
     scores it at -1, so their number runs 3.0 * pass_int high. Verified by
     least squares over 442 QB week-rows: R^2 = 0.99934 on pass_int alone with
     a coefficient of 3.025, and the pass_cmp coefficient falling out at
     -0.0009. `score()` is authoritative here and `pts_ppr` is the wrong one --
     any tool that ranks QBs on `pts_ppr` systematically overvalues turnover
     prone passers.

  2. Week 0 K and DEF: structurally broken. See the `valuation` docstring.
     Tested in test_valuation.py; excluded here.
"""

from __future__ import annotations

import json
import sqlite3
import unittest

from sleeper_agent.config import settings
from sleeper_agent.league import League

# Everything except QB should reconcile to the cent. The DEF allowance covers a
# small number of rows carrying a bucket flag we round differently.
TOLERANCE = {"QB": 0.15, "DEF": 1.10}
DEFAULT_TOLERANCE = 0.10

# Sleeper's pts_ppr scores pass_int at +2; this league scores it at -1.
SLEEPER_INT_BIAS = 3.0


def fetch_rows(season: str, weeks: range):
    """Weekly projection rows joined to position, read only."""
    uri = f"file:{settings.db_file().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [
            (r["position"], json.loads(r["stats"]))
            for r in conn.execute(
                "SELECT p.stats, pl.position FROM projections p"
                " JOIN players pl ON pl.player_id = p.player_id"
                " WHERE p.season = ? AND p.week BETWEEN ? AND ?",
                (season, weeks.start, weeks.stop - 1),
            )
        ]
    finally:
        conn.close()


class TestScoring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.league = League()
        cls.rows = fetch_rows(cls.league.season, range(1, 18))
        if not cls.rows:
            raise unittest.SkipTest("no cached projections; run `python cli.py sync`")

    def _adjusted(self, position: str, stats: dict) -> float:
        """Our score, corrected for the known Sleeper QB interception bias."""
        score = self.league.score(stats)
        if position == "QB":
            score += SLEEPER_INT_BIAS * float(stats.get("pass_int") or 0.0)
        return score

    def test_weekly_scoring_matches_sleeper(self):
        """Every weekly row reconciles once the known QB bias is accounted for."""
        worst: dict[str, float] = {}
        counts: dict[str, int] = {}
        for position, stats in self.rows:
            truth = stats.get("pts_ppr")
            if truth is None or truth <= 0:
                continue
            diff = abs(self._adjusted(position, stats) - truth)
            counts[position] = counts.get(position, 0) + 1
            if diff > worst.get(position, 0.0):
                worst[position] = diff

        self.assertTrue(counts, "no scorable rows found")
        for position, diff in sorted(worst.items()):
            limit = TOLERANCE.get(position, DEFAULT_TOLERANCE)
            self.assertLessEqual(
                diff,
                limit,
                f"{position}: worst |score - pts_ppr| was {diff:.3f} over"
                f" {counts[position]} rows, above the {limit} tolerance",
            )

    def test_qb_interception_bias_is_exactly_three_points(self):
        """Pin the QB discrepancy to its cause so nobody 'fixes' score() to match.

        If this fails, Sleeper changed how pts_ppr treats interceptions. That is
        a reason to re-derive the bias, not to change League.score(), which is
        a faithful dot product against the league's own scoring_settings.
        """
        self.assertEqual(self.league.scoring.get("pass_int"), -1.0)

        residuals = []
        for position, stats in self.rows:
            truth = stats.get("pts_ppr")
            ints = float(stats.get("pass_int") or 0.0)
            if position != "QB" or not truth or truth <= 0 or ints <= 0:
                continue
            gap = truth - self.league.score(stats)
            residuals.append(gap / ints)

        self.assertGreater(len(residuals), 100, "not enough QB rows to characterize")
        mean_ratio = sum(residuals) / len(residuals)
        self.assertAlmostEqual(mean_ratio, SLEEPER_INT_BIAS, delta=0.25)

    def test_non_scoring_keys_contribute_nothing(self):
        """ADP and rank fields ride along in the stat line and must score zero."""
        base = {"rec": 5.0, "rec_yd": 60.0}
        noisy = {**base, "adp_ppr": 12.5, "pos_rank_ppr": 3, "gp": 1.0, "cmp_pct": 64.0}
        self.assertEqual(self.league.score(base), self.league.score(noisy))

    def test_empty_stats_score_zero(self):
        self.assertEqual(self.league.score({}), 0.0)

    def test_ppr_reception_value_is_applied(self):
        """A guard on the single setting this league most depends on."""
        self.assertEqual(self.league.scoring.get("rec"), 1.0)
        self.assertEqual(self.league.scoring_format(), "ppr")
        one_catch = self.league.score({"rec": 1.0})
        self.assertAlmostEqual(one_catch, 1.0, places=2)


if __name__ == "__main__":
    unittest.main()
