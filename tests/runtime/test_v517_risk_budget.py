from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from finruntime.canonical import ContractError
from finruntime.models import StrategySnapshot
from finruntime.profiles.v517_guard import (
    CompletedEquityObservation,
    FROZEN_V517_POLICY,
    V517RuntimeState,
    apply_v517_policy,
    build_v517_shadow_snapshot,
    evaluate_v517_market_state,
)

UTC = timezone.utc


def observations(values: list[Decimal]) -> tuple[CompletedEquityObservation, ...]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return tuple(
        CompletedEquityObservation(
            as_of_utc=(start + timedelta(days=number)).isoformat().replace("+00:00", "Z"),
            equity=format(value, "f"),
            source_sha256="sha256:" + format(number + 1, "064x"),
        )
        for number, value in enumerate(values)
    )


def decision_time(count: int) -> str:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return (start + timedelta(days=count, hours=1)).isoformat().replace("+00:00", "Z")


def growth_values(count: int, daily: Decimal = Decimal("1.01")) -> list[Decimal]:
    value = Decimal("10000")
    output: list[Decimal] = []
    for _ in range(count):
        value *= daily
        output.append(value)
    return output


class V517RiskBudgetTests(unittest.TestCase):
    def test_frozen_policy_parameters(self) -> None:
        policy = FROZEN_V517_POLICY
        self.assertEqual(policy.high_leverage, Decimal("2.075"))
        self.assertEqual(policy.base_leverage, Decimal("0.97"))
        self.assertEqual(policy.low_leverage, Decimal("0.60"))
        self.assertEqual(policy.rebalance_days, 10)
        self.assertEqual(policy.no_trade_band, Decimal("0.04"))
        self.assertEqual(policy.guard_enter_drawdown, Decimal("-0.245"))
        self.assertEqual(policy.guard_exit_drawdown, Decimal("-0.18"))

    def test_warmup_remains_base(self) -> None:
        points = observations(growth_values(20))
        result = evaluate_v517_market_state(
            points,
            decision_time_utc=decision_time(len(points)),
        )
        self.assertEqual(result.state_name, "base")
        self.assertIsNone(result.momentum20)
        self.assertIsNone(result.momentum60)

    def test_sustained_growth_enters_high_state(self) -> None:
        points = observations(growth_values(90))
        result = evaluate_v517_market_state(
            points,
            decision_time_utc=decision_time(len(points)),
        )
        self.assertEqual(result.state_name, "high")
        self.assertGreater(Decimal(result.momentum20 or "0"), Decimal("0.05"))
        self.assertGreater(Decimal(result.momentum60 or "0"), Decimal("-0.04"))

    def test_low_state_requires_three_confirmations(self) -> None:
        values = growth_values(90)
        value = values[-1]
        for _ in range(25):
            value *= Decimal("0.96")
            values.append(value)
        points = observations(values)
        states = []
        for length in range(90, len(points) + 1):
            state = evaluate_v517_market_state(
                points[:length],
                decision_time_utc=decision_time(length),
            )
            states.append(state.state_name)
        self.assertIn("low", states)
        first_low = states.index("low")
        self.assertGreaterEqual(first_low, 2)

    def test_future_append_does_not_change_prior_decision(self) -> None:
        base_values = growth_values(90)
        base_points = observations(base_values)
        first = evaluate_v517_market_state(
            base_points,
            decision_time_utc=decision_time(len(base_points)),
        )
        extended = observations(base_values + [base_values[-1] * Decimal("0.10")])
        repeated = evaluate_v517_market_state(
            extended[:-1],
            decision_time_utc=decision_time(len(base_points)),
        )
        self.assertEqual(first, repeated)

    def test_noncontiguous_or_future_history_fails_closed(self) -> None:
        points = list(observations(growth_values(3)))
        points[1] = CompletedEquityObservation(
            as_of_utc="2026-01-03T00:00:00Z",
            equity=points[1].equity,
            source_sha256=points[1].source_sha256,
        )
        with self.assertRaises(ContractError):
            evaluate_v517_market_state(
                tuple(points),
                decision_time_utc="2026-01-05T01:00:00Z",
            )
        with self.assertRaises(ContractError):
            evaluate_v517_market_state(
                observations(growth_values(3)),
                decision_time_utc="2026-01-03T00:00:00Z",
            )

    def test_runtime_cap_is_explicit(self) -> None:
        points = observations(growth_values(90))
        decision = apply_v517_policy(
            observations=points,
            decision_time_utc=decision_time(len(points)),
            profile_equity="10000",
            profile_high_water="10000",
            runtime_state=V517RuntimeState(),
            maximum_runtime_leverage="1.10",
        )
        self.assertEqual(decision.requested_leverage, "2.075")
        self.assertEqual(decision.selected_leverage, "1.1")
        self.assertTrue(decision.runtime_cap_applied)
        self.assertIn("runtime_leverage_cap", decision.reasons)

    def test_guard_enters_and_caps_risk(self) -> None:
        points = observations(growth_values(90))
        state = V517RuntimeState(
            held_leverage="1.5",
            previous_target_leverage="1.5",
            initialized=True,
            guard_active=False,
            guard_age_days=7,
        )
        decision = apply_v517_policy(
            observations=points,
            decision_time_utc=decision_time(len(points)),
            profile_equity="7500",
            profile_high_water="10000",
            runtime_state=state,
        )
        self.assertTrue(decision.guard_active)
        self.assertEqual(decision.requested_leverage, "1")
        self.assertTrue(decision.risk_reduction)
        self.assertEqual(decision.selected_leverage, "1")

    def test_guard_exit_requires_hold_and_recovery(self) -> None:
        points = observations(growth_values(90))
        held = V517RuntimeState(
            held_leverage="1",
            previous_target_leverage="1",
            initialized=True,
            guard_active=True,
            guard_age_days=3,
        )
        still_guarded = apply_v517_policy(
            observations=points,
            decision_time_utc=decision_time(len(points)),
            profile_equity="8500",
            profile_high_water="10000",
            runtime_state=held,
        )
        self.assertTrue(still_guarded.guard_active)
        mature = V517RuntimeState(
            held_leverage="1",
            previous_target_leverage="1",
            initialized=True,
            guard_active=True,
            guard_age_days=7,
        )
        released = apply_v517_policy(
            observations=points,
            decision_time_utc=decision_time(len(points)),
            profile_equity="8300",
            profile_high_water="10000",
            runtime_state=mature,
        )
        self.assertFalse(released.guard_active)

    def test_inside_band_holds_until_schedule(self) -> None:
        points = observations(growth_values(91))
        state = V517RuntimeState(
            held_leverage="2.05",
            previous_target_leverage="2.05",
            initialized=True,
            guard_active=False,
            guard_age_days=7,
        )
        decision = apply_v517_policy(
            observations=points,
            decision_time_utc=decision_time(len(points)),
            profile_equity="10000",
            profile_high_water="10000",
            runtime_state=state,
        )
        self.assertFalse(decision.target_changed)
        self.assertEqual(decision.selected_leverage, "2.05")

    def test_shadow_snapshot_scales_targets_and_preserves_primary(self) -> None:
        primary = StrategySnapshot.create(
            strategy_id="v75_atlas_nx",
            strategy_version="runtime-v1",
            decision_time_utc=decision_time(90),
            market_snapshot_id="sha256:" + "a" * 64,
            state_sequence=90,
            targets={
                "spot": {"BTC/USDT": "0.20"},
                "perp": {"ETH/USDT:USDT": "-0.10"},
            },
            gross_target="0.30",
            cash_target="0.70",
            risk={"source": "test"},
        )
        original = copy.deepcopy(primary.to_dict())
        points = observations(growth_values(90))
        shadow, decision = build_v517_shadow_snapshot(
            primary_snapshot=primary,
            observations=points,
            profile_equity="10000",
            profile_high_water="10000",
            runtime_state=V517RuntimeState(),
            maximum_runtime_leverage="1.10",
        )
        self.assertEqual(primary.to_dict(), original)
        self.assertEqual(shadow.strategy_id, "v517_tristate_guard_shadow")
        self.assertEqual(shadow.targets["spot"]["BTC/USDT"], "0.22")
        self.assertEqual(shadow.targets["perp"]["ETH/USDT:USDT"], "-0.11")
        self.assertEqual(shadow.gross_target, "0.33")
        self.assertEqual(shadow.risk["source_primary_target_hash"], primary.target_hash)
        self.assertEqual(shadow.risk["v517_decision_hash"], decision.decision_hash)
        self.assertIn("position_margin_unverified", shadow.quality_flags)
        self.assertIn("runtime_leverage_capped", shadow.quality_flags)

    def test_non_v75_primary_rejected(self) -> None:
        primary = StrategySnapshot.create(
            strategy_id="v28_growth_control",
            strategy_version="runtime-v1",
            decision_time_utc=decision_time(90),
            market_snapshot_id="sha256:" + "b" * 64,
            state_sequence=1,
            targets={"spot": {}, "perp": {}},
            gross_target="0",
            cash_target="1",
        )
        with self.assertRaises(ContractError):
            build_v517_shadow_snapshot(
                primary_snapshot=primary,
                observations=observations(growth_values(90)),
                profile_equity="10000",
                profile_high_water="10000",
                runtime_state=V517RuntimeState(),
            )

    def test_decision_hash_is_deterministic(self) -> None:
        points = observations(growth_values(90))
        kwargs = dict(
            observations=points,
            decision_time_utc=decision_time(len(points)),
            profile_equity="10000",
            profile_high_water="10000",
            runtime_state=V517RuntimeState(),
        )
        first = apply_v517_policy(**kwargs)
        second = apply_v517_policy(**kwargs)
        self.assertEqual(first, second)
        self.assertEqual(first.decision_hash, second.decision_hash)


if __name__ == "__main__":
    unittest.main()
