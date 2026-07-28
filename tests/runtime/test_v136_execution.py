from __future__ import annotations

import copy
import unittest
from decimal import Decimal

from finruntime.canonical import ContractError
from finruntime.execution.v136_filter import (
    FROZEN_V136_POLICY,
    V136Policy,
    apply_v136_policy,
    build_v136_shadow_snapshot,
)
from finruntime.models import MarketSnapshot, SourceObservation, StrategySnapshot


class V136ExecutionTests(unittest.TestCase):
    def test_small_change_inside_band_holds_target(self) -> None:
        result = apply_v136_policy(
            desired_targets={"spot": {"BTC/USDT": "0.23"}, "perp": {}},
            held_targets={"spot": {"BTC/USDT": "0.20"}, "perp": {}},
            target_age_days=4,
            state_initialized=True,
        )
        self.assertFalse(result.target_changed)
        self.assertEqual(result.targets["spot"]["BTC/USDT"], "0.2")
        self.assertEqual(result.target_age_days, 5)

    def test_l1_band_updates_full_target(self) -> None:
        result = apply_v136_policy(
            desired_targets={"spot": {"BTC/USDT": "0.28"}, "perp": {}},
            held_targets={"spot": {"BTC/USDT": "0.20"}, "perp": {}},
            target_age_days=4,
            state_initialized=True,
        )
        self.assertTrue(result.target_changed)
        self.assertEqual(result.targets["spot"]["BTC/USDT"], "0.28")
        self.assertIn("l1_band_exceeded", result.reasons)

    def test_strict_risk_reduction_buffer_boundary(self) -> None:
        boundary = apply_v136_policy(
            desired_targets={"spot": {"BTC/USDT": "0.48"}, "perp": {}},
            held_targets={"spot": {"BTC/USDT": "0.50"}, "perp": {}},
            target_age_days=1,
            state_initialized=True,
        )
        self.assertFalse(boundary.force_reduce)
        below = apply_v136_policy(
            desired_targets={"spot": {"BTC/USDT": "0.479"}, "perp": {}},
            held_targets={"spot": {"BTC/USDT": "0.50"}, "perp": {}},
            target_age_days=1,
            state_initialized=True,
        )
        self.assertTrue(below.force_reduce)
        self.assertTrue(below.target_changed)

    def test_global_zero_exit_is_immediate(self) -> None:
        result = apply_v136_policy(
            desired_targets={"spot": {}, "perp": {}},
            held_targets={"spot": {"BTC/USDT": "0.05"}, "perp": {}},
            target_age_days=1,
            state_initialized=True,
        )
        self.assertTrue(result.force_reduce)
        self.assertEqual(result.targets, {"spot": {}, "perp": {}})

    def test_perp_sign_change_is_immediate(self) -> None:
        result = apply_v136_policy(
            desired_targets={"spot": {}, "perp": {"BTC/USDT:USDT": "0.01"}},
            held_targets={"spot": {}, "perp": {"BTC/USDT:USDT": "-0.01"}},
            target_age_days=1,
            state_initialized=True,
        )
        self.assertTrue(result.force_reduce)
        self.assertEqual(
            result.perp_sign_change_instruments, ("BTC/USDT:USDT",)
        )

    def test_zero_to_nonzero_perp_matches_frozen_research_semantics(self) -> None:
        result = apply_v136_policy(
            desired_targets={"spot": {}, "perp": {"BTC/USDT:USDT": "0.01"}},
            held_targets={"spot": {}, "perp": {}},
            target_age_days=1,
            state_initialized=True,
        )
        self.assertTrue(result.force_reduce)
        self.assertTrue(result.target_changed)

    def test_maximum_age_forces_update(self) -> None:
        result = apply_v136_policy(
            desired_targets={"spot": {"BTC/USDT": "0.21"}, "perp": {}},
            held_targets={"spot": {"BTC/USDT": "0.20"}, "perp": {}},
            target_age_days=28,
            state_initialized=True,
        )
        self.assertTrue(result.target_changed)
        self.assertIn("maximum_target_age", result.reasons)

    def test_uninitialized_state_takes_first_target(self) -> None:
        result = apply_v136_policy(
            desired_targets={"spot": {"BTC/USDT": "0.01"}, "perp": {}},
            held_targets={"spot": {}, "perp": {}},
            target_age_days=0,
            state_initialized=False,
        )
        self.assertTrue(result.target_changed)
        self.assertIn("initialization", result.reasons)

    def test_half_step_policy_is_deterministic(self) -> None:
        policy = V136Policy(
            l1_no_trade_band=Decimal("0.08"),
            maximum_target_age_days=28,
            step_fraction=Decimal("0.50"),
            risk_reduction_buffer=Decimal("0.02"),
        )
        result = apply_v136_policy(
            desired_targets={"spot": {"BTC/USDT": "0.30"}, "perp": {}},
            held_targets={"spot": {"BTC/USDT": "0.20"}, "perp": {}},
            target_age_days=1,
            state_initialized=True,
            policy=policy,
        )
        self.assertEqual(result.targets["spot"]["BTC/USDT"], "0.25")

    def test_shadow_snapshot_does_not_mutate_primary(self) -> None:
        source = SourceObservation(
            source="spot_daily",
            source_timestamp_utc="2026-07-27T00:00:00Z",
            available_at_utc="2026-07-27T00:01:00Z",
            payload_sha256="sha256:" + "1" * 64,
        )
        market = MarketSnapshot.create(
            as_of_utc="2026-07-27T00:00:00Z",
            decision_time_utc="2026-07-27T00:05:00Z",
            sources={"spot_daily": source},
        )
        primary = StrategySnapshot.create(
            strategy_id="v75_atlas_nx",
            strategy_version="runtime-v1",
            decision_time_utc="2026-07-27T00:05:00Z",
            market_snapshot_id=market.snapshot_id,
            state_sequence=1,
            targets={"spot": {"BTC/USDT": "0.20"}, "perp": {}},
            gross_target="0.20",
            cash_target="0.80",
            risk={},
        )
        original = copy.deepcopy(primary.to_dict())
        shadow, decision = build_v136_shadow_snapshot(
            primary_snapshot=primary,
            held_targets={"spot": {}, "perp": {}},
            target_age_days=0,
            state_initialized=False,
            cash_target="0.80",
        )
        self.assertEqual(primary.to_dict(), original)
        self.assertEqual(shadow.strategy_id, "v136_execution_shadow")
        self.assertEqual(shadow.targets, decision.targets)
        self.assertNotEqual(shadow.target_hash, primary.target_hash)

    def test_negative_spot_target_rejected(self) -> None:
        with self.assertRaises(ContractError):
            apply_v136_policy(
                desired_targets={"spot": {"BTC/USDT": "-0.1"}, "perp": {}},
                held_targets={"spot": {}, "perp": {}},
                target_age_days=0,
                state_initialized=False,
            )

    def test_frozen_policy_parameters(self) -> None:
        self.assertEqual(FROZEN_V136_POLICY.l1_no_trade_band, Decimal("0.08"))
        self.assertEqual(FROZEN_V136_POLICY.maximum_target_age_days, 28)
        self.assertEqual(FROZEN_V136_POLICY.step_fraction, Decimal("1.00"))
        self.assertEqual(FROZEN_V136_POLICY.risk_reduction_buffer, Decimal("0.02"))


if __name__ == "__main__":
    unittest.main()
