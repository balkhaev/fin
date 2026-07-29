from __future__ import annotations

import unittest
from decimal import Decimal

from finruntime.canonical import ContractError, sha256_id
from finruntime.data.availability import seal_sources
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

    def test_future_source_timestamp_is_rejected_by_snapshot_contract(self) -> None:
        observation = self.observation(
            source_time="2026-07-27T00:06:00Z",
            available_at="2026-07-27T00:04:00Z",
        )
        with self.assertRaises(ContractError):
            MarketSnapshot.create(
                as_of_utc="2026-07-27T00:00:00Z",
                decision_time_utc="2026-07-27T00:05:00Z",
                sources={"spot_daily": observation},
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


class PaperAccountCompatibilityTests(unittest.TestCase):
    def test_legacy_schema_1_0_hash_and_serialization_remain_valid(self) -> None:
        current = PaperAccountState.empty(
            strategy_id="v75_atlas_nx",
            as_of_utc="2026-07-27T00:05:00Z",
            starting_cash="10000",
        )
        payload = current.to_dict()
        payload["schema_version"] = "1.0"
        payload.pop("active_plan_filled_quantities")
        payload.pop("active_plan_fill_event_ids")
        payload["account_hash"] = sha256_id(
            {
                key: value
                for key, value in payload.items()
                if key != "account_hash"
            }
        )

        legacy = PaperAccountState(**payload)
        legacy.validate()
        self.assertEqual(legacy.to_dict(), payload)


class PaperExecutionIntegrityTests(unittest.TestCase):
    @staticmethod
    def reference_prices() -> dict[str, dict[str, object]]:
        return {
            "spot": {"BTC/USDT": {"reference_price": "100"}},
            "perp": {},
        }

    def test_partial_plan_resumes_without_overfill(self) -> None:
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
        intent_id = plan.intents[0].intent_id
        self.assertEqual(
            first.account_state.active_plan_filled_quantities[intent_id], "5"
        )

        old_quote = PaperQuote(
            instrument="BTC/USDT",
            market_type="spot",
            observed_at_utc="2026-07-27T00:05:30Z",
            source_observation_hash="sha256:" + "4" * 64,
            bid="99.95",
            ask="100.05",
            mid="100",
            available_quantity="1000",
        )
        with self.assertRaises(AccountingHalt):
            execute_paper_cycle(
                plan=plan,
                account_state=first.account_state,
                quotes=(old_quote,),
                mark_prices=self.reference_prices(),
                policy=policy,
            )

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
        second = execute_paper_cycle(
            plan=plan,
            account_state=first.account_state,
            quotes=(retry_quote,),
            mark_prices=self.reference_prices(),
            policy=policy,
        )
        self.assertEqual(second.fill_events[0].status, "filled")
        self.assertEqual(second.fill_events[0].filled_quantity, "15")
        self.assertEqual(second.account_state.spot_positions["BTC/USDT"], "20")
        self.assertTrue(second.execution_complete)

        repeated = execute_paper_cycle(
            plan=plan,
            account_state=second.account_state,
            quotes=(retry_quote,),
            mark_prices=self.reference_prices(),
            policy=policy,
        )
        self.assertEqual(repeated.fill_events, ())
        self.assertEqual(
            repeated.account_state.account_hash,
            second.account_state.account_hash,
        )


if __name__ == "__main__":
    unittest.main()
