from __future__ import annotations

import unittest
from datetime import UTC, datetime

from finruntime.observability.strategy_hub import (
    StrategyHub,
    UpstreamSnapshotCache,
)
from finruntime.strategies.consensus_paper import (
    _empty_state,
    evaluate_signals,
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
        self.assertFalse(state["exchange_submission_available"])


class StrategyHubTests(unittest.TestCase):
    def test_normalizes_all_repository_strategies(self) -> None:
        dyn = {
            "status": "ready",
            "marketDataAt": "2026-07-29T12:00:00Z",
            "paper": {
                "account": {"initialNavUsd": 100_000},
                "navUsd": 100_100,
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
                    "starting_balance_usdt": 3_000,
                    "equity_usdt": 3_010,
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
        self.assertEqual(snapshot["summary"]["strategy_count"], 4)
        self.assertEqual(
            {item["repository"] for item in snapshot["strategies"]},
            {"fin", "trader", "fin2"},
        )
        self.assertEqual(snapshot["summary"]["paper_equity_usdt"], 123_130)
        self.assertFalse(snapshot["exchange_submission_available"])


if __name__ == "__main__":
    unittest.main()
