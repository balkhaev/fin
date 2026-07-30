from __future__ import annotations

import unittest
from decimal import Decimal

from finruntime.canonical import ContractError
from finruntime.execution import PaperBrokerPolicy, PaperQuote, execute_paper_cycle
from finruntime.models import ExecutionIntent, ExecutionPlan
from finruntime.portfolio import PaperAccountState
from finruntime.provenance import StrategyMigrationRecord

HASH_A = "sha256:" + "a" * 64


class SharedQuoteLiquidityTests(unittest.TestCase):
    @staticmethod
    def policy() -> PaperBrokerPolicy:
        return PaperBrokerPolicy(
            spot_commission_bps=Decimal("0"),
            perp_commission_bps=Decimal("0"),
            proxy_half_spread_bps=Decimal("0"),
            impact_bps=Decimal("0"),
            participation_rate=Decimal("1"),
        )

    @staticmethod
    def plan() -> ExecutionPlan:
        close = ExecutionIntent.create(
            instrument="BTC/USDT:USDT",
            market_type="perpetual",
            side="sell",
            reduce_only=True,
            quantity="2",
            quantity_unit="base",
            reference_price="100",
            max_slippage_bps="0",
            reason="perpetual_sign_flip_close",
            not_before_utc="2026-07-30T08:00:00Z",
            expires_at_utc="2026-07-30T09:00:00Z",
        )
        child = ExecutionIntent.create(
            instrument="BTC/USDT:USDT",
            market_type="perpetual",
            side="sell",
            reduce_only=False,
            quantity="3",
            quantity_unit="base",
            reference_price="100",
            max_slippage_bps="0",
            reason="perpetual_sign_flip_open",
            parent_intent_id=close.intent_id,
            not_before_utc="2026-07-30T08:00:00Z",
            expires_at_utc="2026-07-30T09:00:00Z",
        )
        return ExecutionPlan.create(
            strategy_id="v75_atlas_nx",
            mode="paper",
            created_at_utc="2026-07-30T08:00:00Z",
            market_snapshot_id="sha256:" + "1" * 64,
            state_sequence=1,
            target_hash="sha256:" + "2" * 64,
            intents=(close, child),
        )

    @staticmethod
    def account() -> PaperAccountState:
        return PaperAccountState.create(
            strategy_id="v75_atlas_nx",
            sequence=0,
            as_of_utc="2026-07-30T08:00:00Z",
            cash="10000",
            perp_positions={"BTC/USDT:USDT": "2"},
            perp_entry_prices={"BTC/USDT:USDT": "100"},
            equity="10000",
            high_water="10000",
        )

    @staticmethod
    def quote(*, available: str, time: str) -> PaperQuote:
        return PaperQuote(
            instrument="BTC/USDT:USDT",
            market_type="perpetual",
            observed_at_utc=time,
            source_observation_hash="sha256:" + "3" * 64,
            bid="100",
            ask="100",
            mid="100",
            available_quantity=available,
        )

    @staticmethod
    def prices() -> dict[str, dict[str, object]]:
        return {
            "spot": {},
            "perp": {"BTC/USDT:USDT": {"reference_price": "100"}},
        }

    def test_sign_flip_intents_share_one_quote_capacity(self) -> None:
        plan = self.plan()
        first = execute_paper_cycle(
            plan=plan,
            account_state=self.account(),
            quotes=(
                self.quote(
                    available="3",
                    time="2026-07-30T08:01:00Z",
                ),
            ),
            mark_prices=self.prices(),
            policy=self.policy(),
        )

        self.assertEqual(
            [fill.status for fill in first.fill_events],
            ["filled", "partial"],
        )
        self.assertEqual(
            [fill.filled_quantity for fill in first.fill_events],
            ["2", "1"],
        )
        self.assertEqual(first.total_filled_notional, "300")
        self.assertEqual(
            first.account_state.perp_positions["BTC/USDT:USDT"],
            "-1",
        )
        self.assertFalse(first.execution_complete)

        resumed = execute_paper_cycle(
            plan=plan,
            account_state=first.account_state,
            quotes=(
                self.quote(
                    available="2",
                    time="2026-07-30T08:02:00Z",
                ),
            ),
            mark_prices=self.prices(),
            policy=self.policy(),
        )
        self.assertEqual(len(resumed.fill_events), 1)
        self.assertEqual(resumed.fill_events[0].filled_quantity, "2")
        self.assertEqual(
            resumed.account_state.perp_positions["BTC/USDT:USDT"],
            "-3",
        )
        self.assertTrue(resumed.execution_complete)


class MigrationBuilderStrictTypeTests(unittest.TestCase):
    @staticmethod
    def values() -> dict[str, object]:
        return {
            "migration_kind": "reconstruction",
            "status": "planned",
            "predecessor_strategy_id": "v75_atlas_nx",
            "successor_strategy_id": "v75_reconstructed_v1",
            "created_at_utc": "2026-07-30T08:00:00Z",
            "reason": "Exact predecessor artifacts are unavailable.",
            "source_audits": {"docs/audit.json": HASH_A},
            "changed_components": ("source_reconstruction",),
            "allowed_modes": ("paper", "shadow"),
            "forward_clock_reset": True,
            "successor_provenance_complete": False,
        }

    def test_create_rejects_non_boolean_values(self) -> None:
        for field, value in (
            ("forward_clock_reset", 1),
            ("successor_provenance_complete", 0),
            ("capital_authorization_carried_forward", "false"),
            ("live_ready", None),
            ("real_leverage_authorized", []),
            ("exchange_submission_available", {}),
        ):
            with self.subTest(field=field):
                values = self.values()
                values[field] = value
                with self.assertRaises(ContractError):
                    StrategyMigrationRecord.create(**values)  # type: ignore[arg-type]

    def test_create_rejects_non_string_json_fields(self) -> None:
        cases: tuple[tuple[str, object], ...] = (
            ("migration_kind", ["reconstruction"]),
            ("status", 1),
            ("created_at_utc", 0),
            ("reason", 7),
            ("changed_components", ("source_reconstruction", 7)),
            ("allowed_modes", ("paper", False)),
            ("source_audits", {7: HASH_A}),
            ("inherited_parameters", {7: "1.10"}),
        )
        for field, value in cases:
            with self.subTest(field=field):
                values = self.values()
                values[field] = value
                with self.assertRaises(ContractError):
                    StrategyMigrationRecord.create(**values)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
