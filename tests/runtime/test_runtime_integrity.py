from __future__ import annotations

import unittest
from decimal import Decimal

from finruntime.canonical import ContractError
from finruntime.data.availability import evaluate_availability, seal_sources
from finruntime.execution import (
    PaperBrokerPolicy,
    PaperQuote,
    build_execution_plan,
    execute_paper_cycle,
)
from finruntime.models import (
    MarketSnapshot,
    SourceObservation,
    StrategySnapshot,
)
from finruntime.portfolio import AccountingHalt, PaperAccountState, apply_pretrade_risk
from scripts.verify_runtime import (
    load_source_registry,
    provenance_completeness_issues,
)


class ProvenanceIntegrityTests(unittest.TestCase):
    def test_incomplete_registry_profiles_have_explicit_issues(self) -> None:
        registry = load_source_registry()
        profiles = registry["profiles"]

        v75_issues = provenance_completeness_issues(profiles["v75_atlas_nx"])
        v28_issues = provenance_completeness_issues(profiles["v28_growth_control"])
        v136_issues = provenance_completeness_issues(
            profiles["v136_execution_shadow"]
        )

        self.assertIn("provenance_complete=false", v75_issues)
        self.assertIn("provenance_complete=false", v28_issues)
        self.assertEqual(v136_issues, ())


class AvailabilityIntegrityTests(unittest.TestCase):
    @staticmethod
    def observation(
        *,
        source_time: str,
        available_at: str,
        digest: str = "1",
    ) -> SourceObservation:
        return SourceObservation(
            source="spot_daily",
            source_timestamp_utc=source_time,
            available_at_utc=available_at,
            payload_sha256="sha256:" + digest * 64,
            quality="ok",
        )

    def test_future_source_timestamp_blocks_risk_increase(self) -> None:
        observation = self.observation(
            source_time="2026-07-27T00:06:00Z",
            available_at="2026-07-27T00:04:00Z",
        )
        snapshot = MarketSnapshot.create(
            as_of_utc="2026-07-27T00:00:00Z",
            decision_time_utc="2026-07-27T00:05:00Z",
            sources={"spot_daily": observation},
        )

        decision = evaluate_availability(
            snapshot,
            critical_sources=("spot_daily",),
        )

        self.assertFalse(decision.risk_increase_permitted)
        self.assertIn(
            "source_timestamp_after_available_at:spot_daily",
            decision.blocking_reasons,
        )
        self.assertIn(
            "source_timestamp_after_decision_time:spot_daily",
            decision.blocking_reasons,
        )

    def test_same_payload_with_different_metadata_is_rejected(self) -> None:
        first = self.observation(
            source_time="2026-07-27T00:00:00Z",
            available_at="2026-07-27T00:01:00Z",
        )
        second = self.observation(
            source_time="2026-07-27T00:00:00Z",
            available_at="2026-07-27T00:02:00Z",
        )

        with self.assertRaises(ContractError):
            seal_sources((first, second))


class PaperExecutionIntegrityTests(unittest.TestCase):
    @staticmethod
    def reference_prices() -> dict[str, dict[str, object]]:
        return {
            "spot": {"BTC/USDT": {"reference_price": "100"}},
            "perp": {},
        }

    def test_partial_plan_cannot_be_executed_twice(self) -> None:
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
            spot={"BTC/USDT": {"reference_price": "100"}},
        )
        strategy = StrategySnapshot.create(
            strategy_id="v75_atlas_nx",
            strategy_version="runtime-v1",
            decision_time_utc=market.decision_time_utc,
            market_snapshot_id=market.snapshot_id,
            state_sequence=1,
            targets={"spot": {"BTC/USDT": "0.2"}, "perp": {}},
            gross_target="0.2",
            cash_target="0.8",
            risk={"gross_cap": "1.05"},
        )
        account = PaperAccountState.empty(
            strategy_id="v75_atlas_nx",
            as_of_utc=market.decision_time_utc,
            starting_cash="10000",
        )
        portfolio = account.to_portfolio_state()
        risk = apply_pretrade_risk(
            strategy_snapshot=strategy,
            portfolio_state=portfolio,
            market_snapshot=market,
            reference_prices=self.reference_prices(),
            critical_sources=("spot_daily",),
        )
        plan = build_execution_plan(
            strategy_snapshot=strategy,
            portfolio_state=portfolio,
            market_snapshot=market,
            risk_decision=risk,
            reference_prices=self.reference_prices(),
        )
        policy = PaperBrokerPolicy(
            spot_commission_bps=Decimal("10"),
            perp_commission_bps=Decimal("6"),
            proxy_half_spread_bps=Decimal("4"),
            impact_bps=Decimal("2"),
            participation_rate=Decimal("0.10"),
        )
        first_quote = PaperQuote(
            instrument="BTC/USDT",
            market_type="spot",
            observed_at_utc="2026-07-27T00:06:00Z",
            source_observation_hash="sha256:" + "2" * 64,
            bid="99.95",
            ask="100.05",
            mid="100",
            available_quantity="50",
        )

        first = execute_paper_cycle(
            plan=plan,
            account_state=account,
            quotes=(first_quote,),
            mark_prices=self.reference_prices(),
            policy=policy,
        )
        self.assertEqual(first.fill_events[0].status, "partial")
        self.assertEqual(first.fill_events[0].filled_quantity, "5")

        retry_quote = PaperQuote(
            instrument="BTC/USDT",
            market_type="spot",
            observed_at_utc="2026-07-27T00:07:00Z",
            source_observation_hash="sha256:" + "3" * 64,
            bid="99.95",
            ask="100.05",
            mid="100",
            available_quantity="1000",
        )
        with self.assertRaises(AccountingHalt):
            execute_paper_cycle(
                plan=plan,
                account_state=first.account_state,
                quotes=(retry_quote,),
                mark_prices=self.reference_prices(),
                policy=policy,
            )


if __name__ == "__main__":
    unittest.main()
