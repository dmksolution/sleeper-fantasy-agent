"""Guard the season-value derivation, which the draft board is built on.

The property that matters is *internal consistency*: season value must be the
sum of exactly the weekly numbers the lineup optimizer will use. Matching
Sleeper's week-0 aggregate is explicitly not the goal, because that aggregate is
the thing this module exists to replace.
"""

from __future__ import annotations

import json
import sqlite3
import unittest

from sleeper_agent.config import settings
from sleeper_agent.league import League, load_players
from sleeper_agent.projections import week_projections
from sleeper_agent.valuation import (
    FULL,
    PLAYOFFS,
    coverage,
    replacement_baseline,
    season_value,
    team_bye_weeks,
)


def week0_truth(season: str) -> dict[str, float]:
    """Sleeper's own season total, used only as a sanity band."""
    uri = f"file:{settings.db_file().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return {
            r["player_id"]: json.loads(r["stats"]).get("pts_ppr") or 0.0
            for r in conn.execute(
                "SELECT player_id, stats FROM projections WHERE season = ? AND week = 0",
                (season,),
            )
        }
    finally:
        conn.close()


class TestValuation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.league = League()
        cls.values = season_value(cls.league)
        if not cls.values:
            raise unittest.SkipTest("no cached projections; run `python cli.py sync`")
        cls.players = load_players()

    def test_full_season_is_cached(self):
        cov = coverage(self.league)
        self.assertTrue(
            cov["complete"], f"weeks {cov['weeks_missing']} missing; run `cli.py sync`"
        )

    def test_points_equal_sum_of_weekly(self):
        """The invariant. Season value is never independent of the weekly numbers."""
        for pid, sv in self.values.items():
            self.assertAlmostEqual(
                sv.points, round(sum(sv.weekly.values()), 2), places=1, msg=f"player {pid}"
            )

    def test_playoff_points_come_from_playoff_weeks(self):
        for pid, sv in self.values.items():
            expected = round(sum(v for w, v in sv.weekly.items() if w in PLAYOFFS), 2)
            self.assertAlmostEqual(sv.playoff_points, expected, places=1, msg=f"player {pid}")

    def test_every_nfl_team_has_exactly_one_bye(self):
        byes = team_bye_weeks(self.league)
        self.assertEqual(len(byes), 32, f"expected 32 teams, got {sorted(byes)}")
        for team, week in byes.items():
            self.assertIn(week, range(1, 19), f"{team} bye week {week} out of range")

    def test_defenses_report_a_bye(self):
        """Regression: defenses get NO projection row on a bye, offense gets an
        empty one. Per-player detection silently reported defenses as never
        having a bye, which fed the draft board and bye planning."""
        defenses = [
            sv
            for pid, sv in self.values.items()
            if sv.position == "DEF" and self.players.get(pid) and self.players[pid].team
        ]
        self.assertGreaterEqual(len(defenses), 30)
        without = [sv.player_id for sv in defenses if not sv.bye_weeks]
        self.assertEqual(without, [], "defenses missing a bye week")

    def test_starters_play_one_fewer_week_than_the_season(self):
        """17 fantasy weeks minus one bye is 16 games for anyone with a real role."""
        regulars = [sv for sv in self.values.values() if sv.points > 100]
        self.assertGreater(len(regulars), 100)
        for sv in regulars:
            self.assertLessEqual(sv.games, len(FULL) - 1, f"{sv.player_id} never byes")

    def test_kickers_are_no_longer_understated(self):
        """The headline bug: week-0 scoring put every kicker ~35% low.

        Scoring Aubrey's week-0 line gave 76.0 against Sleeper's own 116.0,
        because the aggregate emits `fgm_50p` where the league pays
        `fgm_50_59`/`fgm_60p`. Some kickers are worse still: Trey Smack's
        aggregate carries only an `fgm_40_49` bucket, so even Sleeper's own
        season number for him (70.0) is far below a real kicker season.

        So week 0 cannot be a tight bound -- it is the broken thing. What is
        assertable is the direction and the plausibility of the result: summing
        weekly lines never lands below the aggregate, and every starting kicker
        ends up in a believable range.
        """
        truth = week0_truth(self.league.season)
        ratios = []
        for pid, sv in self.values.items():
            if sv.position != "K" or sv.points < 80:
                continue
            expected = truth.get(pid) or 0.0
            if expected <= 0:
                continue
            ratios.append(sv.points / expected)
            # A starting kicker scores roughly 90-190 in this scoring. The old
            # path put them near 76, below every real replacement level.
            self.assertGreater(sv.points, 85.0, f"kicker {pid} implausibly low")
            self.assertLess(sv.points, 200.0, f"kicker {pid} implausibly high")
            self.assertGreaterEqual(
                sv.points,
                expected * 0.95,
                f"kicker {pid} came out below the known-understated aggregate",
            )

        self.assertGreater(len(ratios), 10, "not enough kickers to check")
        mean_ratio = sum(ratios) / len(ratios)
        self.assertGreater(mean_ratio, 1.05, "kickers did not move up as expected")

    def test_aubrey_regression(self):
        """The specific number from the bug report, pinned."""
        aubrey = [
            sv
            for pid, sv in self.values.items()
            if self.players.get(pid) and self.players[pid].name == "Brandon Aubrey"
        ]
        if not aubrey:
            self.skipTest("Brandon Aubrey not in the player index")
        # Was 76.0 under the week-0 dot product; Sleeper's own number is 116.0.
        self.assertGreater(aubrey[0].points, 110.0)

    def test_defenses_lose_the_phantom_shutout(self):
        """Week 0 gave all 32 defenses a free pts_allow_0 bucket worth +10."""
        truth = week0_truth(self.league.season)
        for pid, sv in self.values.items():
            if sv.position != "DEF":
                continue
            expected = truth.get(pid) or 0.0
            if expected <= 0:
                continue
            # Weekly defense lines carry a real points-allowed distribution that
            # the aggregate lacks entirely, so ours runs higher, not lower.
            self.assertGreater(sv.points, expected * 0.9)

    def test_nothing_scores_off_week_zero(self):
        """Week 0 is an ADP carrier. If it ever becomes a scoring source again,
        kickers and defenses silently break."""
        self.assertEqual(week_projections(self.league, 0) and 0, 0)  # row exists
        for sv in self.values.values():
            self.assertNotIn(0, sv.weekly, "week 0 leaked into a season total")

    def test_replacement_modes_differ_and_are_ordered(self):
        """A league-average starter must be a higher bar than the waiver wire."""
        values = {pid: sv.points for pid, sv in self.values.items() if sv.points > 0}
        positions = {pid: sv.position for pid, sv in self.values.items() if sv.points > 0}
        startable = replacement_baseline(self.league, values, positions, "startable")
        waiver = replacement_baseline(self.league, values, positions, "waiver")
        for pos in ("RB", "WR", "TE", "QB"):
            self.assertIn(pos, startable)
            self.assertLess(
                startable[pos],
                waiver[pos],
                f"{pos}: startable replacement should sit deeper than waiver depth",
            )

    def test_unknown_replacement_mode_raises(self):
        with self.assertRaises(ValueError):
            replacement_baseline(self.league, {}, {}, "nonsense")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
