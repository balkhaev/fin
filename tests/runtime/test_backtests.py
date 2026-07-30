from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta
from typing import Any

from finruntime.observability.backtest_runner import run_backtest
from finruntime.observability.backtests import backtest_report, backtest_strategy_ids


def synthetic_history_loader(
    symbols: tuple[str, ...], start: date, end: date
) -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
    histories: list[dict[str, Any]] = []
    day_count = (end - start).days + 1
    for asset_index, symbol in enumerate(symbols):
        bars: dict[str, dict[str, Any]] = {}
        for day_index in range(day_count):
            observed = datetime.combine(
                start + timedelta(days=day_index), datetime.min.time(), UTC
            )
            growth = 1.001 + asset_index * 0.00003
            close = (10 + asset_index) * growth**day_index
            bars[observed.date().isoformat()] = {
                "openTime": int(observed.timestamp() * 1000),
                "open": close * 0.995,
                "high": close * 1.02,
                "low": close * 0.98,
                "close": close,
                "closeTime": int((observed + timedelta(days=1)).timestamp() * 1000 - 1),
                "quoteVolume": 20_000_000 + asset_index * 1_000_000,
                "closed": True,
            }
        histories.append(
            {
                "asset": symbol.removesuffix("USDT"),
                "symbol": symbol,
                "bars": bars,
                "liveCandle": list(bars.values())[-1],
            }
        )
    return histories, [], len(symbols)


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
        self.assertEqual(
            report["provenance"]["normalized_trades_sha256"],
            "32e2fabaedccb0cea99b19422222d89e7459a787a9b4ca00738b0eca4af69a90",
        )
        self.assertEqual(
            report["provenance"]["normalized_trades_format"], "readable_json"
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

    def test_click_run_recomputes_dyn_with_current_engine_and_fresh_run_id(
        self,
    ) -> None:
        calls: list[tuple[tuple[str, ...], date, date]] = []

        def loader(
            symbols: tuple[str, ...], start: date, end: date
        ) -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
            calls.append((symbols, start, end))
            return synthetic_history_loader(symbols, start, end)

        now = datetime(2026, 7, 30, 12, tzinfo=UTC)
        first = run_backtest("dyn-iv113", now=now, history_loader=loader)
        second = run_backtest("dyn-iv113", now=now, history_loader=loader)

        self.assertEqual(len(calls), 2)
        self.assertNotEqual(first["execution"]["run_id"], second["execution"]["run_id"])
        self.assertEqual(first["execution"]["status"], "completed")
        self.assertEqual(first["execution"]["trigger"], "user_click")
        self.assertEqual(first["report_kind"], "on_demand_backtest")
        self.assertEqual(first["evidence"]["status"], "computed")
        self.assertEqual(first["window"]["start"], "2024-07-29")
        self.assertEqual(first["window"]["end"], "2026-07-29")
        self.assertEqual(first["metrics"]["scope"], "on_demand_two_year_replay")
        self.assertEqual(first["metrics"]["starting_nav_usd"], 10_000.0)
        self.assertGreater(first["metrics"]["ending_nav_usd"], 0)
        self.assertGreater(first["trade_count"], 0)
        self.assertEqual(first["trade_count"], len(first["trades"]))
        self.assertEqual(
            len({trade["id"] for trade in first["trades"]}), first["trade_count"]
        )
        self.assertAlmostEqual(
            sum(trade["net_pnl_usd"] for trade in first["trades"]),
            first["metrics"]["ending_nav_usd"] - first["metrics"]["starting_nav_usd"],
            places=6,
        )
        self.assertEqual(len(first["provenance"]["input_sha256"]), 64)
        self.assertTrue(
            all(trade["entry_date"] >= "2024-07-29" for trade in first["trades"])
        )

    def test_click_run_recomputes_atlas_with_its_active_identity(self) -> None:
        report = run_backtest(
            "atlas-nx",
            now=datetime(2026, 7, 30, 12, tzinfo=UTC),
            history_loader=synthetic_history_loader,
        )

        self.assertEqual(report["execution"]["status"], "completed")
        self.assertEqual(report["strategy_identity"], "atlas_nx_r1")
        self.assertEqual(report["provenance"]["engine_module"], "atlas_nx_r1_paper")
        self.assertIsNone(report["historical_reference"])
        self.assertGreater(report["trade_count"], 0)
        self.assertAlmostEqual(
            sum(trade["net_pnl_usd"] for trade in report["trades"]),
            report["metrics"]["ending_nav_usd"] - report["metrics"]["starting_nav_usd"],
            places=6,
        )

    def test_click_run_blocks_strategies_without_required_historical_inputs(
        self,
    ) -> None:
        def forbidden_loader(
            _symbols: tuple[str, ...], _start: date, _end: date
        ) -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
            raise AssertionError("OHLC loader must not approximate a factor strategy")

        for strategy_id in ("funding-neutral", "consensus-wif-dot"):
            with self.subTest(strategy_id=strategy_id):
                report = run_backtest(
                    strategy_id,
                    now=datetime(2026, 7, 30, 12, tzinfo=UTC),
                    history_loader=forbidden_loader,
                )
                self.assertEqual(report["execution"]["status"], "blocked")
                self.assertEqual(report["evidence"]["status"], "blocked_missing_inputs")
                self.assertIsNone(report["metrics"])
                self.assertEqual(report["trades"], [])
                self.assertTrue(report["blockers"])


if __name__ == "__main__":
    unittest.main()
