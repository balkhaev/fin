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
                "atlas-v517-reference",
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
        self.assertGreaterEqual(first["execution"]["duration_seconds"], 0.0)
        self.assertLess(first["execution"]["duration_seconds"], 5.0)
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

    def test_click_run_recomputes_atlas_v517_and_keeps_paper_identity_separate(
        self,
    ) -> None:
        def forbidden_loader(
            _symbols: tuple[str, ...], _start: date, _end: date
        ) -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
            raise AssertionError("Atlas V517 must use its pinned V75 account stream")

        report = run_backtest(
            "atlas-v517-reference",
            now=datetime(2026, 7, 30, 12, tzinfo=UTC),
            history_loader=forbidden_loader,
        )

        self.assertEqual(report["execution"]["status"], "completed")
        self.assertEqual(report["strategy_identity"], "v517_v524_v75_tristate_guard")
        self.assertEqual(report["paper_strategy_identity"], "atlas_nx_r1")
        self.assertEqual(report["report_kind"], "on_demand_historical_replay")
        self.assertEqual(report["window"]["start"], "2021-01-01")
        self.assertEqual(report["window"]["end"], "2026-06-30")
        self.assertEqual(report["requested_window"]["start"], "2024-07-01")
        self.assertEqual(report["requested_window"]["end"], "2026-06-30")
        self.assertAlmostEqual(report["metrics"]["cagr_percent"], 50.547706, places=5)
        self.assertAlmostEqual(
            report["requested_window_metrics"]["cagr_percent"],
            43.344905,
            places=5,
        )
        self.assertTrue(report["evidence"]["cagr_threshold_passed"])
        self.assertTrue(report["evidence"]["parameters_informed_by_known_history"])
        self.assertFalse(report["evidence"]["program_level_holdout_pristine"])
        self.assertEqual(report["trade_table_kind"], "account_leverage_episodes")
        self.assertEqual(
            report["provenance"]["input_sha256"],
            "f9d543ba8ec15c90efa757e64ed772b1a5934e458463124b7df48ddcac96ef01",
        )
        self.assertEqual(
            report["provenance"]["strategy_commit"],
            "663cd5f19ed381cd616bf783faf5a30c5df8baaf",
        )
        self.assertFalse(report["provenance"]["is_current_paper_account"])
        self.assertGreater(report["trade_count"], 0)
        self.assertAlmostEqual(
            sum(trade["net_pnl_usd"] for trade in report["trades"]),
            report["requested_window_metrics"]["ending_nav_usd"]
            - report["requested_window_metrics"]["starting_nav_usd"],
            places=6,
        )

    def test_click_atlas_nx_does_not_replay_predecessor_metrics(self) -> None:
        report = run_backtest(
            "atlas-nx", now=datetime(2026, 7, 30, 12, tzinfo=UTC)
        )

        self.assertEqual(report["strategy_identity"], "atlas_nx_r1")
        self.assertEqual(report["execution"]["status"], "not_available")
        self.assertIsNone(report["metrics"])
        self.assertFalse(
            report["historical_reference"]["belongs_to_active_strategy"]
        )
        self.assertLess(report["execution"]["duration_seconds"], 5.0)

    def test_click_run_supports_preregistered_dyn_shadow_profiles(self) -> None:
        now = datetime(2026, 7, 30, 12, tzinfo=UTC)
        risk50 = run_backtest(
            "dyn-iv113-risk50", now=now, history_loader=synthetic_history_loader
        )
        band2 = run_backtest(
            "dyn-iv113-band2", now=now, history_loader=synthetic_history_loader
        )

        self.assertEqual(risk50["strategy_identity"], "DYN-IV113-RISK50")
        self.assertEqual(band2["strategy_identity"], "DYN-IV113-BAND2")
        self.assertEqual(risk50["report_kind"], "on_demand_backtest")
        self.assertEqual(band2["report_kind"], "on_demand_backtest")

    def test_click_run_executes_factor_strategies_without_ohlc_approximation(
        self,
    ) -> None:
        calls: list[tuple[str, date, date]] = []

        def forbidden_loader(
            _symbols: tuple[str, ...], _start: date, _end: date
        ) -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
            raise AssertionError("OHLC loader must not approximate a factor strategy")

        def factor_runner(strategy_id: str, start: date, end: date) -> dict[str, Any]:
            calls.append((strategy_id, start, end))
            return {
                "metrics": {
                    "scope": "on_demand_two_year_factor_replay",
                    "scope_label": "factor replay",
                    "cagr_percent": 12.0,
                    "total_return_percent": 25.0,
                    "sharpe": 0.8,
                    "sortino": 1.1,
                    "max_drawdown_percent": -9.0,
                    "years": 2.0,
                    "starting_nav_usd": 10_000.0,
                    "ending_nav_usd": 12_500.0,
                    "daily_observations": 731,
                },
                "trades": [
                    {
                        "id": f"{strategy_id}-1",
                        "asset": "TEST",
                        "direction": "LONG",
                        "status": "closed",
                        "entry_date": start.isoformat(),
                        "exit_date": end.isoformat(),
                        "held_through": end.isoformat(),
                        "holding_days": (end - start).days,
                        "entry_price": 100.0,
                        "exit_price": 125.0,
                        "asset_return_percent": 25.0,
                        "net_pnl_usd": 2_500.0,
                        "order_count": 2,
                    }
                ],
                "input_sha256": "a" * 64,
                "market_data_requests": 4,
                "market_data_bytes": 1_024,
                "diagnostics": {},
            }

        for strategy_id in ("funding-neutral", "consensus-wif-dot"):
            with self.subTest(strategy_id=strategy_id):
                report = run_backtest(
                    strategy_id,
                    now=datetime(2026, 7, 30, 12, tzinfo=UTC),
                    history_loader=forbidden_loader,
                    factor_runner=factor_runner,
                )
                self.assertEqual(report["execution"]["status"], "completed")
                self.assertEqual(report["evidence"]["status"], "computed")
                self.assertEqual(report["metrics"]["ending_nav_usd"], 12_500.0)
                self.assertEqual(report["trade_count"], 1)
                self.assertEqual(report["blockers"], [])

        self.assertEqual(
            calls,
            [
                ("funding-neutral", date(2024, 7, 29), date(2026, 7, 29)),
                ("consensus-wif-dot", date(2024, 7, 29), date(2026, 7, 29)),
            ],
        )


if __name__ == "__main__":
    unittest.main()
