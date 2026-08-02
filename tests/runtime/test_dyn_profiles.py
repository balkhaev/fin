from __future__ import annotations

import unittest

from finruntime.strategies import dyn_paper


class DynProfileTests(unittest.TestCase):
    def test_profiles_are_distinct_and_baseline_is_unchanged(self) -> None:
        baseline = dyn_paper.get_profile("baseline")
        risk50 = dyn_paper.get_profile("risk50")
        band2 = dyn_paper.get_profile("band2")

        self.assertEqual(baseline.strategy_id, "DYN-IV113")
        self.assertEqual(baseline.mode, "paper")
        self.assertEqual(risk50.target_volatility, 0.50)
        self.assertEqual(risk50.mode, "shadow")
        self.assertEqual(band2.target_deadband, 0.02)
        self.assertEqual(band2.mode, "shadow")

    def test_deadband_holds_small_changes_but_executes_exit_and_flip(self) -> None:
        targets = [
            [0.10, -0.10],
            [0.11, -0.11],
            [0.13, 0.10],
            [0.00, 0.10],
        ]
        actual = dyn_paper._apply_target_deadband(targets, 0.02)

        self.assertEqual(actual[0], [0.10, -0.10])
        self.assertEqual(actual[1], [0.10, -0.10])
        self.assertEqual(actual[2], [0.13, 0.10])
        self.assertEqual(actual[3], [0.00, 0.10])

    def test_unknown_profile_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            dyn_paper.get_profile("unknown")


if __name__ == "__main__":
    unittest.main()
