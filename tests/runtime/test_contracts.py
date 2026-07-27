from __future__ import annotations

import unittest
from dataclasses import replace

from finruntime.canonical import ContractError, canonical_json_text
from finruntime.models import (
    ExecutionIntent,
    ExecutionPlan,
    MarketSnapshot,
    PortfolioState,
    SourceObservation,
    StrategySnapshot,
)
from finruntime.registry import assert_mode


class ContractTests(unittest.TestCase):
    def source(self) -> SourceObservation:
        return SourceObservation(
            source="spot_daily",
            source_timestamp_utc="2026-07-27T00:00:00Z",
            available_at_utc="2026-07-27T00:01:00Z",
            payload_sha256="sha256:" + "1" * 64,
        )

    def snapshot(self) -> MarketSnapshot:
        return MarketSnapshot.create(
            as_of_utc="2026-07-27T00:00:00Z",
            decision_time_utc="2026-07-27T00:05:00Z",
            sources={"spot_daily": self.source()},
            spot={"BTC/USDT": {"close": "100000"}},
        )

    def test_canonical_key_order_is_stable(self) -> None:
        self.assertEqual(
            canonical_json_text({"b": 1, "a": 2}),
            canonical_json_text({"a": 2, "b": 1}),
        )

    def test_snapshot_hash_is_deterministic(self) -> None:
        first = self.snapshot()
        second = self.snapshot()
        self.assertEqual(first.snapshot_id, second.snapshot_id)

    def test_snapshot_hash_mismatch_fails(self) -> None:
        snapshot = replace(self.snapshot(), spot={"BTC/USDT": {"close": "1"}})
        with self.assertRaises(ContractError):
            snapshot.validate()

    def test_strategy_target_hash_is_deterministic(self) -> None:
        snapshot = self.snapshot()
        first = StrategySnapshot.create(
            strategy_id="v75_atlas_nx",
            strategy_version="runtime-v1",
            decision_time_utc="2026-07-27T00:05:00Z",
            market_snapshot_id=snapshot.snapshot_id,
            state_sequence=1,
            targets={"spot": {"BTC/USDT": "0.20"}, "perp": {}},
            gross_target="0.20",
            cash_target="0.80",
            risk={"gross_cap": "1.05"},
        )
        second = StrategySnapshot.create(
            strategy_id="v75_atlas_nx",
            strategy_version="runtime-v1",
            decision_time_utc="2026-07-27T00:05:00Z",
            market_snapshot_id=snapshot.snapshot_id,
            state_sequence=1,
            targets={"perp": {}, "spot": {"BTC/USDT": "0.20"}},
            gross_target="0.20",
            cash_target="0.80",
            risk={"gross_cap": "1.05"},
        )
        self.assertEqual(first.target_hash, second.target_hash)

    def test_portfolio_high_water_cannot_decrease_below_equity(self) -> None:
        with self.assertRaises(ContractError):
            PortfolioState.create(
                strategy_id="v75_atlas_nx",
                sequence=1,
                as_of_utc="2026-07-27T00:05:00Z",
                cash="100",
                equity="110",
                high_water="105",
                positions={"spot": {}, "perp": {}},
                held_targets={"spot": {}, "perp": {}},
            )

    def test_live_mode_is_rejected(self) -> None:
        with self.assertRaises(ContractError):
            assert_mode("v75_atlas_nx", "live")

    def test_execution_plan_requires_reductions_first(self) -> None:
        snapshot = self.snapshot()
        target_hash = "sha256:" + "2" * 64
        open_intent = ExecutionIntent.create(
            instrument="BTC/USDT:USDT",
            market_type="perpetual",
            side="buy",
            reduce_only=False,
            quantity="0.01",
            quantity_unit="base",
            reference_price="100000",
            max_slippage_bps="10",
            reason="risk_increase",
            not_before_utc="2026-07-27T00:06:00Z",
            expires_at_utc="2026-07-27T00:16:00Z",
        )
        close_intent = ExecutionIntent.create(
            instrument="ETH/USDT:USDT",
            market_type="perpetual",
            side="buy",
            reduce_only=True,
            quantity="0.01",
            quantity_unit="base",
            reference_price="4000",
            max_slippage_bps="10",
            reason="risk_reduction",
            not_before_utc="2026-07-27T00:06:00Z",
            expires_at_utc="2026-07-27T00:16:00Z",
        )
        with self.assertRaises(ContractError):
            ExecutionPlan.create(
                strategy_id="v75_atlas_nx",
                mode="paper",
                created_at_utc="2026-07-27T00:05:30Z",
                market_snapshot_id=snapshot.snapshot_id,
                state_sequence=1,
                target_hash=target_hash,
                intents=[open_intent, close_intent],
            )


if __name__ == "__main__":
    unittest.main()
