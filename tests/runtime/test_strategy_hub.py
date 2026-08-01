from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from finruntime.observability.strategy_hub import (
    StrategyHub,
    UpstreamSnapshotCache,
    _dyn_strategy,
    read_atlas_snapshot,
    read_ds40180_snapshot,
    read_dyn_snapshot,
)
from finruntime.strategies.consensus_paper import (
    _empty_state,
    create_initial_risk_state,
    evaluate_signals,
    transition_risk_state,
    update_paper_state,
)


def timestamp_ms(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0
) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp() * 1000)


class ConsensusPaperTests(unittest.TestCase):
    def market(self) -> dict:
        now = timestamp_ms(2026, 7, 28, 0, 20)
        return {
            "observed_at_ms": now,
            "prices": {"WIFUSDT": 1.0, "DOTUSDT": 4.0},
            "wif": {
                "signal_closed_at_ms": timestamp_ms(2026, 7, 28, 0, 14),
                "entry_price": 1.0,
                "open": 1.0,
                "high": 1.1,
                "low": 0.8,
                "close": 1.08,
                "atr": 0.1,
                "move_45m_atr": -2.4,
                "volume_z": 1.2,
                "taker_imbalance": -0.05,
                "oi_z": -1.5,
                "premium_z": -1.0,
            },
            "dot": {
                "funding_time_ms": timestamp_ms(2026, 7, 27, 0, 0),
                "evaluated_at_ms": timestamp_ms(2026, 7, 27, 0, 20),
                "funding_rate_bps": -3.0,
                "entry_price": 4.0,
                "atr": 0.1,
            },
            "candles": [],
            "diagnostics": {},
        }

    def test_trader_signals_open_paper_positions(self) -> None:
        market = self.market()
        signals = evaluate_signals(market)
        self.assertEqual({item["symbol"] for item in signals}, {"WIFUSDT", "DOTUSDT"})
        state = update_paper_state(_empty_state(), market)
        self.assertEqual(state["health"], "healthy")
        self.assertEqual(len(state["paper"]["positions"]), 2)
        self.assertLess(state["paper"]["equity_usdt"], 10_000.0)
        self.assertTrue(state["signal_context"]["wif"]["passes"])
        self.assertTrue(state["signal_context"]["dot"]["passes"])
        self.assertFalse(state["exchange_submission_available"])

    def test_trader_risk_accelerator_matches_source_transitions(self) -> None:
        initial = create_initial_risk_state(10_000)
        boosted = transition_risk_state(initial, 11_500)
        self.assertEqual(boosted["mode"], "boost")
        derisked = transition_risk_state(boosted, 10_500)
        self.assertEqual(derisked["mode"], "base")
        stopped = transition_risk_state(derisked, 9_700)
        self.assertEqual(stopped["mode"], "stopped")
        self.assertEqual(transition_risk_state(stopped, 12_000)["mode"], "stopped")

    def test_boost_mode_uses_source_risk_percentages(self) -> None:
        state = _empty_state()
        state["paper"]["realized_pnl_usdt"] = 1_500
        updated = update_paper_state(state, self.market())
        self.assertEqual(updated["risk_state"]["mode"], "boost")
        risk_by_symbol = {
            item["symbol"]: item["risk_percent"]
            for item in updated["paper"]["positions"]
        }
        self.assertEqual(risk_by_symbol, {"WIFUSDT": 7.5, "DOTUSDT": 10.0})


class StrategyHubTests(unittest.TestCase):
    def test_dyn_unavailable_is_not_reported_as_a_cash_decision(self) -> None:
        strategy = _dyn_strategy(None, "snapshot is not available", True)

        self.assertEqual(strategy["status"], "degraded")
        self.assertEqual(strategy["status_label"], "Нет данных")
        self.assertIn("недоступны", strategy["context"]["why_now"])
        self.assertNotIn("Стратегия в кэше", strategy["context"]["why_now"])

    def test_reads_fresh_local_dyn_snapshot_and_rejects_stale_file(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "dyn.json"
            path.write_text(
                '{"schema_version":1,"strategyId":"DYN-IV113",'
                f'"generatedAt":"{datetime.now(UTC).isoformat()}",'
                '"status":"ready"}',
                encoding="utf-8",
            )

            snapshot, error, stale = read_dyn_snapshot(path, stale_after_seconds=60)
            self.assertEqual(snapshot["strategyId"], "DYN-IV113")
            self.assertIsNone(error)
            self.assertFalse(stale)

            snapshot, error, stale = read_dyn_snapshot(path, stale_after_seconds=-1)
            self.assertIsNotNone(snapshot)
            self.assertIn("stale", error or "")
            self.assertTrue(stale)

    def test_reads_only_fresh_reconstructed_atlas_identity(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "atlas.json"
            path.write_text(
                '{"schema_version":1,"strategyId":"atlas_nx_r1",'
                f'"generatedAt":"{datetime.now(UTC).isoformat()}",'
                '"status":"ready"}',
                encoding="utf-8",
            )

            snapshot, error, stale = read_atlas_snapshot(path, stale_after_seconds=60)
            self.assertEqual(snapshot["strategyId"], "atlas_nx_r1")
            self.assertIsNone(error)
            self.assertFalse(stale)

            path.write_text(
                '{"schema_version":1,"strategyId":"v75_atlas_nx",'
                f'"generatedAt":"{datetime.now(UTC).isoformat()}",'
                '"status":"ready"}',
                encoding="utf-8",
            )
            snapshot, error, stale = read_atlas_snapshot(path)
            self.assertIsNone(snapshot)
            self.assertIn("identity", error or "")
            self.assertTrue(stale)


    def test_reads_only_fresh_ds40180_v2_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ds40180.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "strategyId": "ds40180_t50c3_okx_paper",
                        "strategyVersion": "okx-paper-v2",
                        "generatedAt": datetime.now(UTC).isoformat(),
                        "status": "ready",
                    }
                ),
                encoding="utf-8",
            )
            snapshot, error, stale = read_ds40180_snapshot(
                path, stale_after_seconds=60
            )
            self.assertEqual(snapshot["strategyVersion"], "okx-paper-v2")
            self.assertIsNone(error)
            self.assertFalse(stale)

            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "strategyId": "ds40180_t50c3_okx_paper",
                        "generatedAt": datetime.now(UTC).isoformat(),
                    }
                ),
                encoding="utf-8",
            )
            snapshot, error, stale = read_ds40180_snapshot(path)
            self.assertIsNone(snapshot)
            self.assertIn("schema", error or "")
            self.assertTrue(stale)

    def test_normalizes_all_repository_strategies(self) -> None:
        dyn = {
            "status": "ready",
            "marketDataAt": "2026-07-29T12:00:00Z",
            "paper": {
                "account": {"initialNavUsd": 10_000},
                "navUsd": 10_100,
                "totalExecutions": 2,
            },
            "positions": [],
            "targetGross": 0,
            "cashWeight": 1,
        }
        cache = UpstreamSnapshotCache(lambda _url, _timeout: dyn)
        hub = StrategyHub(fin2_url="https://example.test/forward", cache=cache)
        snapshot = hub.snapshot(
            funding={
                "health": "healthy",
                "updated_at_ms": 1,
                "paper": {
                    "starting_balance_usdt": 10_000,
                    "equity_usdt": 10_010,
                    "closed_positions": 1,
                    "open_position": None,
                },
                "scan": {"candidates": [], "rejections": []},
                "markets": [],
                "risk": {},
            },
            consensus={
                **_empty_state(),
                "health": "healthy",
                "paper": {
                    **_empty_state()["paper"],
                    "equity_usdt": 10_020,
                },
            },
            runtime={
                "scheduler": {"state": "idle"},
                "strategies": [
                    {
                        "strategy_id": "v75_atlas_nx",
                        "health": "healthy",
                        "observation_count": 1,
                        "account": {"equity": 10_000},
                    }
                ],
            },
        )
        self.assertEqual(snapshot["summary"]["strategy_count"], 5)
        self.assertEqual(
            {item["repository"] for item in snapshot["strategies"]},
            {"fin", "trader", "fin2"},
        )
        self.assertEqual(snapshot["summary"]["paper_equity_usdt"], 50_130)
        self.assertEqual(snapshot["summary"]["paper_starting_balance_usdt"], 50_000)
        self.assertTrue(
            all(
                item["starting_balance_usdt"] == 10_000
                for item in snapshot["strategies"]
            )
        )
        self.assertTrue(
            all(
                {
                    "how_it_works",
                    "why_now",
                    "waiting_for",
                    "metrics",
                    "full_description",
                }
                <= item["context"].keys()
                for item in snapshot["strategies"]
            )
        )
        for item in snapshot["strategies"]:
            description = item["context"]["full_description"]
            self.assertTrue(
                {
                    "summary",
                    "steps",
                    "entry_conditions",
                    "exit_conditions",
                    "risk_controls",
                    "data_scope",
                    "current_state",
                }
                <= description.keys()
            )
            self.assertGreaterEqual(len(description["steps"]), 3)
            self.assertGreaterEqual(len(description["risk_controls"]), 2)
            self.assertEqual(description["current_state"], item["context"]["why_now"])
        self.assertFalse(snapshot["exchange_submission_available"])

    def test_funding_context_explains_current_spread_gap(self) -> None:
        dyn = {
            "status": "ready",
            "paper": {"account": {"initialNavUsd": 10_000}, "navUsd": 10_000},
            "positions": [],
        }
        hub = StrategyHub(
            fin2_url="https://example.test/forward",
            cache=UpstreamSnapshotCache(lambda _url, _timeout: dyn),
        )
        snapshot = hub.snapshot(
            funding={
                "health": "healthy",
                "paper": {
                    "starting_balance_usdt": 10_000,
                    "equity_usdt": 10_000,
                },
                "risk": {
                    "min_current_spread_bps_8h": 8.0,
                    "min_predicted_spread_bps_8h": 5.0,
                    "min_expected_net_bps": 10.0,
                },
                "scan": {
                    "candidates": [],
                    "rejections": [
                        {
                            "reason": "current_spread_below_threshold",
                            "details": {"current_spread_bps_8h": 1.25},
                        }
                    ],
                },
            },
            consensus={**_empty_state(), "health": "healthy"},
            runtime={"scheduler": {"state": "idle"}, "strategies": []},
        )
        funding = next(
            item for item in snapshot["strategies"] if item["id"] == "funding-neutral"
        )
        self.assertIn("1.25 bps", funding["context"]["why_now"])
        self.assertIn("8.00 bps", funding["context"]["waiting_for"])

    def test_atlas_is_blocked_without_canonical_target_producer(self) -> None:
        dyn = {
            "status": "ready",
            "paper": {
                "account": {"initialNavUsd": 10_000},
                "navUsd": 10_000,
            },
            "positions": [],
        }
        hub = StrategyHub(
            fin2_url="https://example.test/forward",
            cache=UpstreamSnapshotCache(lambda _url, _timeout: dyn),
        )
        snapshot = hub.snapshot(
            funding={
                "health": "healthy",
                "paper": {
                    "starting_balance_usdt": 10_000,
                    "equity_usdt": 10_000,
                },
            },
            consensus={**_empty_state(), "health": "healthy"},
            runtime={
                "scheduler": {"state": "idle"},
                "strategies": [
                    {
                        "strategy_id": "v75_atlas_nx",
                        "health": "healthy",
                        "observation_count": 0,
                        "account": {"equity": 10_000},
                    }
                ],
            },
        )
        atlas = next(
            item for item in snapshot["strategies"] if item["id"] == "atlas-nx"
        )
        self.assertEqual(atlas["status"], "blocked")
        self.assertIn("3303cd91511b", atlas["context"]["waiting_for"])
        self.assertEqual(snapshot["summary"]["running_count"], 3)

    def test_local_reconstructed_atlas_replaces_blocked_card(self) -> None:
        dyn = {
            "status": "ready",
            "paper": {"account": {"initialNavUsd": 10_000}, "navUsd": 10_000},
            "positions": [],
        }
        with TemporaryDirectory() as directory:
            atlas_path = Path(directory) / "atlas.json"
            atlas_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "strategyId": "atlas_nx_r1",
                        "predecessorStrategyId": "v75_atlas_nx",
                        "status": "ready",
                        "generatedAt": datetime.now(UTC).isoformat(),
                        "marketDataAt": datetime.now(UTC).isoformat(),
                        "paper": {
                            "account": {"initialNavUsd": 10_000},
                            "navUsd": 10_050,
                            "daily": [],
                            "totalExecutions": 1,
                        },
                        "positions": [{"asset": "BTC", "weight": 0.5}],
                        "candles": [],
                        "targetGross": 0.5,
                        "cashWeight": 0.5,
                        "ratchetStage": 0,
                        "defensiveWeight": 0,
                        "volatilityMultiplier": 1,
                        "onchainAcceleratorScale": 0,
                        "onchainStatus": "disabled_stale_or_missing",
                    }
                ),
                encoding="utf-8",
            )
            hub = StrategyHub(
                fin2_url="https://example.test/forward",
                cache=UpstreamSnapshotCache(lambda _url, _timeout: dyn),
                atlas_snapshot_path=atlas_path,
            )
            snapshot = hub.snapshot(
                funding={
                    "health": "healthy",
                    "paper": {
                        "starting_balance_usdt": 10_000,
                        "equity_usdt": 10_000,
                    },
                },
                consensus={**_empty_state(), "health": "healthy"},
                runtime={"scheduler": {"state": "idle"}, "strategies": []},
            )

        atlas = next(
            item for item in snapshot["strategies"] if item["id"] == "atlas-nx"
        )
        self.assertEqual(atlas["status"], "running")
        self.assertEqual(atlas["equity_usdt"], 10_050)
        self.assertEqual(atlas["open_positions"], 1)
        self.assertIn("новой identity", atlas["context"]["how_it_works"])
        self.assertIn("on-chain", atlas["context"]["waiting_for"])


if __name__ == "__main__":
    unittest.main()
