from __future__ import annotations

import unittest

from finruntime.observability.backtests import backtest_report, backtest_strategy_ids


class BacktestReportTests(unittest.TestCase):
    def test_catalog_covers_every_active_strategy(self) -> None:
        self.assertEqual(
            set(backtest_strategy_ids()),
            {
                "funding-neutral",
                "consensus-wif-dot",
                "dyn-iv113",
                "atlas-nx",
            },
        )

    def test_dyn_report_verifies_archive_and_exposes_two_year_trades(self) -> None:
        report = backtest_report("dyn-iv113")

        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["evidence"]["status"], "verified")
        self.assertTrue(report["evidence"]["cagr_threshold_passed"])
        self.assertEqual(report["evidence"]["cagr_threshold_percent"], 50.0)
        self.assertAlmostEqual(report["metrics"]["cagr_percent"], 112.638, places=3)
        self.assertEqual(report["metrics"]["scope"], "full_frozen_oos")
        self.assertEqual(report["window"]["start"], "2024-07-26")
        self.assertEqual(report["window"]["end"], "2026-07-26")
        self.assertEqual(report["window"]["requested_years"], 2)
        self.assertEqual(report["trade_count"], 53)
        self.assertEqual(len(report["trades"]), 53)
        self.assertTrue(
            all(trade["entry_date"] <= "2026-07-26" for trade in report["trades"])
        )
        self.assertTrue(
            all(trade["held_through"] >= "2024-07-26" for trade in report["trades"])
        )
        self.assertEqual(
            report["provenance"]["episodes_payload_sha256"],
            "7a35e00cd449bc0d9359498137ad09f90f7a253497d69ec14e8b25ffde32815a",
        )
        self.assertFalse(report["provenance"]["is_current_paper_account"])

    def test_unproven_strategies_never_receive_fabricated_metrics(self) -> None:
        for strategy_id in ("funding-neutral", "consensus-wif-dot", "atlas-nx"):
            with self.subTest(strategy_id=strategy_id):
                report = backtest_report(strategy_id)
                self.assertEqual(report["evidence"]["status"], "insufficient_evidence")
                self.assertIsNone(report["evidence"]["cagr_threshold_passed"])
                self.assertIsNone(report["metrics"])
                self.assertEqual(report["trades"], [])
                self.assertTrue(report["blockers"])

    def test_atlas_predecessor_metric_is_not_attributed_to_successor(self) -> None:
        report = backtest_report("atlas-nx")
        reference = report["historical_reference"]

        self.assertEqual(reference["strategy_identity"], "V517/V524")
        self.assertAlmostEqual(reference["cagr_percent"], 50.55, places=2)
        self.assertFalse(reference["belongs_to_active_strategy"])

    def test_unknown_strategy_is_rejected(self) -> None:
        with self.assertRaises(KeyError):
            backtest_report("unknown")


if __name__ == "__main__":
    unittest.main()
