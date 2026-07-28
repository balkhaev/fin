from __future__ import annotations

import unittest
from decimal import Decimal

from finruntime.canonical import ContractError
from finruntime.execution import (
    PaperBrokerPolicy,
    PaperQuote,
    build_execution_plan,
    execute_paper_cycle,
)
from finruntime.models import (
    ExecutionIntent,
    ExecutionPlan,
    FillEvent,
    MarketSnapshot,
    PortfolioState,
    SourceObservation,
    StrategySnapshot,
)
from finruntime.portfolio import (
    AccountingHalt,
    FundingEvent,
    PaperAccountState,
    apply_fill_event,
    apply_funding_event,
    apply_pretrade_risk,
    build_forward_telemetry_row,
    build_reconciliation_report,
)


class PaperLedgerTests(unittest.TestCase):
    def market(self) -> MarketSnapshot:
        source = SourceObservation(
            source="spot_daily",
            source_timestamp_utc="2026-07-27T00:00:00Z",
            available_at_utc="2026-07-27T00:01:00Z",
            payload_sha256="sha256:" + "1" * 64,
        )
        return MarketSnapshot.create(
            as_of_utc="2026-07-27T00:00:00Z",
            decision_time_utc="2026-07-27T00:05:00Z",
            sources={"spot_daily": source},
            spot={"BTC/USDT": {"reference_price": "100"}},
            perp={"BTC/USDT:USDT": {"reference_price": "100"}},
        )

    def prices(self, *, btc: str = "100") -> dict[str, dict[str, object]]:
        return {
            "spot": {"BTC/USDT": {"reference_price": btc}},
            "perp": {"BTC/USDT:USDT": {"reference_price": btc}},
        }

    def strategy(
        self,
        market: MarketSnapshot,
        *,
        spot: str = "0",
        perp: str = "0",
    ) -> StrategySnapshot:
        targets = {
            "spot": {"BTC/USDT": spot} if Decimal(spot) != 0 else {},
            "perp": {"BTC/USDT:USDT": perp} if Decimal(perp) != 0 else {},
        }
        gross = abs(Decimal(spot)) + abs(Decimal(perp))
        return StrategySnapshot.create(
            strategy_id="v75_atlas_nx",
            strategy_version="runtime-v1",
            decision_time_utc=market.decision_time_utc,
            market_snapshot_id=market.snapshot_id,
            state_sequence=1,
            targets=targets,
            gross_target=str(gross),
            cash_target="0.8",
            risk={"gross_cap": "1.05"},
        )

    def portfolio(
        self,
        *,
        spot_quantity: str = "0",
        perp_quantity: str = "0",
    ) -> PortfolioState:
        return PortfolioState.create(
            strategy_id="v75_atlas_nx",
            sequence=1,
            as_of_utc="2026-07-27T00:05:00Z",
            cash="10000",
            equity="10000",
            high_water="10000",
            positions={
                "spot": {"BTC/USDT": spot_quantity}
                if Decimal(spot_quantity) != 0
                else {},
                "perp": {"BTC/USDT:USDT": perp_quantity}
                if Decimal(perp_quantity) != 0
                else {},
            },
            held_targets={"spot": {}, "perp": {}},
        )

    def account(
        self,
        *,
        spot_quantity: str = "0",
        perp_quantity: str = "0",
        perp_entry: str = "100",
    ) -> PaperAccountState:
        return PaperAccountState.create(
            strategy_id="v75_atlas_nx",
            sequence=1,
            as_of_utc="2026-07-27T00:05:00Z",
            cash="10000",
            spot_positions={"BTC/USDT": spot_quantity}
            if Decimal(spot_quantity) != 0
            else {},
            perp_positions={"BTC/USDT:USDT": perp_quantity}
            if Decimal(perp_quantity) != 0
            else {},
            perp_entry_prices={"BTC/USDT:USDT": perp_entry}
            if Decimal(perp_quantity) != 0
            else {},
            equity="10000",
            high_water="10000",
        )

    def quote(
        self,
        *,
        market_type: str,
        bid: str | None = "99.95",
        ask: str | None = "100.05",
        mid: str | None = "100",
        available: str = "1000",
        quality: str = "ok",
        time: str = "2026-07-27T00:06:00Z",
    ) -> PaperQuote:
        return PaperQuote(
            instrument=(
                "BTC/USDT" if market_type == "spot" else "BTC/USDT:USDT"
            ),
            market_type=market_type,
            observed_at_utc=time,
            source_observation_hash="sha256:" + "2" * 64,
            bid=bid,
            ask=ask,
            mid=mid,
            available_quantity=available,
            quality=quality,
        )

    def plan(
        self,
        market: MarketSnapshot,
        strategy: StrategySnapshot,
        portfolio: PortfolioState,
    ) -> ExecutionPlan:
        risk = apply_pretrade_risk(
            strategy_snapshot=strategy,
            portfolio_state=portfolio,
            market_snapshot=market,
            reference_prices=self.prices(),
            critical_sources=("spot_daily",),
        )
        return build_execution_plan(
            strategy_snapshot=strategy,
            portfolio_state=portfolio,
            market_snapshot=market,
            risk_decision=risk,
            reference_prices=self.prices(),
        )

    def full_policy(self) -> PaperBrokerPolicy:
        return PaperBrokerPolicy(
            spot_commission_bps=Decimal("10"),
            perp_commission_bps=Decimal("6"),
            proxy_half_spread_bps=Decimal("4"),
            impact_bps=Decimal("2"),
            participation_rate=Decimal("1"),
        )

    def test_spot_buy_fill_updates_cash_position_and_equity(self) -> None:
        market = self.market()
        strategy = self.strategy(market, spot="0.2")
        portfolio = self.portfolio()
        plan = self.plan(market, strategy, portfolio)
        result = execute_paper_cycle(
            plan=plan,
            account_state=self.account(),
            quotes=[self.quote(market_type="spot")],
            mark_prices=self.prices(),
            policy=self.full_policy(),
        )
        self.assertTrue(result.execution_complete)
        self.assertEqual(result.fill_events[0].status, "filled")
        self.assertEqual(result.fill_events[0].filled_quantity, "20")
        self.assertEqual(result.account_state.spot_positions["BTC/USDT"], "20")
        self.assertLess(Decimal(result.account_state.cash), Decimal("8000"))
        self.assertLess(Decimal(result.account_state.equity), Decimal("10000"))

    def test_liquidity_cap_creates_partial_fill(self) -> None:
        market = self.market()
        strategy = self.strategy(market, spot="0.2")
        portfolio = self.portfolio()
        plan = self.plan(market, strategy, portfolio)
        policy = PaperBrokerPolicy(
            spot_commission_bps=Decimal("10"),
            perp_commission_bps=Decimal("6"),
            proxy_half_spread_bps=Decimal("4"),
            impact_bps=Decimal("2"),
            participation_rate=Decimal("0.10"),
        )
        result = execute_paper_cycle(
            plan=plan,
            account_state=self.account(),
            quotes=[self.quote(market_type="spot", available="50")],
            mark_prices=self.prices(),
            policy=policy,
        )
        self.assertFalse(result.execution_complete)
        self.assertEqual(result.fill_events[0].status, "partial")
        self.assertEqual(result.fill_events[0].filled_quantity, "5")

    def test_outage_rejects_without_changing_position(self) -> None:
        market = self.market()
        strategy = self.strategy(market, spot="0.2")
        portfolio = self.portfolio()
        plan = self.plan(market, strategy, portfolio)
        result = execute_paper_cycle(
            plan=plan,
            account_state=self.account(),
            quotes=[
                self.quote(
                    market_type="spot",
                    bid=None,
                    ask=None,
                    mid=None,
                    quality="outage",
                )
            ],
            mark_prices=self.prices(),
            policy=self.full_policy(),
        )
        self.assertEqual(result.fill_events[0].status, "rejected")
        self.assertEqual(result.account_state.spot_positions, {})

    def test_maximum_slippage_rejects_fill(self) -> None:
        market = self.market()
        strategy = self.strategy(market, spot="0.2")
        portfolio = self.portfolio()
        plan = self.plan(market, strategy, portfolio)
        result = execute_paper_cycle(
            plan=plan,
            account_state=self.account(),
            quotes=[
                self.quote(
                    market_type="spot",
                    bid="109",
                    ask="110",
                    mid="109.5",
                )
            ],
            mark_prices=self.prices(),
            policy=self.full_policy(),
        )
        self.assertEqual(result.fill_events[0].status, "rejected")
        self.assertEqual(result.outcomes[0].reason, "maximum_slippage_exceeded")

    def test_partial_sign_flip_close_blocks_child_open(self) -> None:
        market = self.market()
        strategy = self.strategy(market, perp="-0.03")
        portfolio = self.portfolio(perp_quantity="2")
        plan = self.plan(market, strategy, portfolio)
        account = self.account(perp_quantity="2")
        policy = PaperBrokerPolicy(
            spot_commission_bps=Decimal("10"),
            perp_commission_bps=Decimal("6"),
            proxy_half_spread_bps=Decimal("4"),
            impact_bps=Decimal("2"),
            participation_rate=Decimal("0.10"),
        )
        result = execute_paper_cycle(
            plan=plan,
            account_state=account,
            quotes=[self.quote(market_type="perpetual", available="10")],
            mark_prices=self.prices(),
            policy=policy,
        )
        self.assertEqual([fill.status for fill in result.fill_events], ["partial", "rejected"])
        self.assertEqual(result.outcomes[1].reason, "parent_intent_not_fully_filled")
        self.assertEqual(result.account_state.perp_positions["BTC/USDT:USDT"], "1")

    def test_funding_long_pays_positive_rate_idempotently(self) -> None:
        state = self.account(perp_quantity="2")
        event = FundingEvent.create(
            instrument="BTC/USDT:USDT",
            occurred_at_utc="2026-07-27T08:00:00Z",
            funding_rate="0.001",
            mark_price="100",
            source_observation_hash="sha256:" + "3" * 64,
        )
        first = apply_funding_event(state, event)
        second = apply_funding_event(first, event)
        self.assertEqual(first.account_hash, second.account_hash)
        self.assertEqual(first.funding_pnl, "-0.2")
        self.assertEqual(first.cash, "9999.8")

    def test_perpetual_reduction_realizes_pnl(self) -> None:
        state = self.account(perp_quantity="2")
        intent = ExecutionIntent.create(
            instrument="BTC/USDT:USDT",
            market_type="perpetual",
            side="sell",
            reduce_only=True,
            quantity="1",
            quantity_unit="base",
            reference_price="110",
            max_slippage_bps="10",
            reason="perpetual_reduce",
            not_before_utc="2026-07-27T00:05:00Z",
            expires_at_utc="2026-07-27T00:20:00Z",
        )
        plan = ExecutionPlan.create(
            strategy_id="v75_atlas_nx",
            mode="paper",
            created_at_utc="2026-07-27T00:05:00Z",
            market_snapshot_id="sha256:" + "4" * 64,
            state_sequence=1,
            target_hash="sha256:" + "5" * 64,
            intents=[intent],
        )
        state = PaperAccountState.create(
            strategy_id=state.strategy_id,
            sequence=state.sequence + 1,
            as_of_utc=state.as_of_utc,
            cash=state.cash,
            spot_positions=state.spot_positions,
            perp_positions=state.perp_positions,
            perp_entry_prices=state.perp_entry_prices,
            fees_paid=state.fees_paid,
            realized_pnl=state.realized_pnl,
            funding_pnl=state.funding_pnl,
            equity=state.equity,
            high_water=state.high_water,
            last_plan_id=plan.plan_id,
            applied_event_ids=state.applied_event_ids,
        )
        fill = FillEvent.create(
            plan_id=plan.plan_id,
            intent_id=intent.intent_id,
            filled_at_utc="2026-07-27T00:06:00Z",
            status="filled",
            filled_quantity="1",
            price="110",
            fee="0",
            fee_currency="USDT",
            slippage_bps="0",
            source_observation_hash="sha256:" + "6" * 64,
        )
        result = apply_fill_event(state, intent, fill)
        self.assertEqual(result.perp_positions["BTC/USDT:USDT"], "1")
        self.assertEqual(result.perp_entry_prices["BTC/USDT:USDT"], "100")
        self.assertEqual(result.realized_pnl, "10")
        self.assertEqual(result.cash, "10010")
        self.assertEqual(apply_fill_event(result, intent, fill).account_hash, result.account_hash)

    def test_successive_plans_activate_new_context(self) -> None:
        market = self.market()
        account = self.account()
        first_strategy = self.strategy(market, spot="0.1")
        first_plan = self.plan(market, first_strategy, self.portfolio())
        first = execute_paper_cycle(
            plan=first_plan,
            account_state=account,
            quotes=[self.quote(market_type="spot")],
            mark_prices=self.prices(),
            policy=self.full_policy(),
        )
        second_strategy = StrategySnapshot.create(
            strategy_id="v75_atlas_nx",
            strategy_version="runtime-v1",
            decision_time_utc=market.decision_time_utc,
            market_snapshot_id=market.snapshot_id,
            state_sequence=2,
            targets={"spot": {}, "perp": {}},
            gross_target="0",
            cash_target="1",
            risk={"gross_cap": "1.05"},
        )
        second_portfolio = first.account_state.to_portfolio_state()
        second_plan = self.plan(market, second_strategy, second_portfolio)
        second = execute_paper_cycle(
            plan=second_plan,
            account_state=first.account_state,
            quotes=[self.quote(market_type="spot")],
            mark_prices=self.prices(),
            policy=self.full_policy(),
        )
        self.assertNotEqual(first_plan.plan_id, second_plan.plan_id)
        self.assertEqual(second.account_state.spot_positions, {})

    def test_partial_fill_reconciliation_warns(self) -> None:
        market = self.market()
        strategy = self.strategy(market, spot="0.2")
        portfolio = self.portfolio()
        plan = self.plan(market, strategy, portfolio)
        policy = PaperBrokerPolicy(
            spot_commission_bps=Decimal("10"),
            perp_commission_bps=Decimal("6"),
            proxy_half_spread_bps=Decimal("4"),
            impact_bps=Decimal("2"),
            participation_rate=Decimal("0.10"),
        )
        execution = execute_paper_cycle(
            plan=plan,
            account_state=self.account(),
            quotes=[self.quote(market_type="spot", available="50")],
            mark_prices=self.prices(),
            policy=policy,
        )
        report = build_reconciliation_report(
            plan=plan,
            starting_positions=portfolio.positions,
            model_targets=strategy.targets,
            account_state=execution.account_state,
            reference_prices=self.prices(),
            modelled_cost="2",
            realized_paper_cost=execution.total_fees,
            source_hash_match=True,
            data_stale=False,
            execution_complete=execution.execution_complete,
        )
        self.assertEqual(report.status, "warn")
        self.assertIn("execution_incomplete", report.alerts)
        self.assertGreater(Decimal(report.tracking_error_fraction), Decimal("0.02"))

    def test_source_hash_mismatch_halts_reconciliation(self) -> None:
        market = self.market()
        strategy = self.strategy(market, spot="0.1")
        portfolio = self.portfolio()
        plan = self.plan(market, strategy, portfolio)
        execution = execute_paper_cycle(
            plan=plan,
            account_state=self.account(),
            quotes=[self.quote(market_type="spot")],
            mark_prices=self.prices(),
            policy=self.full_policy(),
        )
        report = build_reconciliation_report(
            plan=plan,
            starting_positions=portfolio.positions,
            model_targets=strategy.targets,
            account_state=execution.account_state,
            reference_prices=self.prices(),
            modelled_cost="2",
            realized_paper_cost=execution.total_fees,
            source_hash_match=False,
            data_stale=False,
            execution_complete=True,
        )
        self.assertEqual(report.status, "halt")
        self.assertIn("source_hash_mismatch", report.alerts)

    def test_forward_telemetry_row_matches_v429_contract(self) -> None:
        market = self.market()
        strategy = self.strategy(market, spot="0.1")
        portfolio = self.portfolio()
        plan = self.plan(market, strategy, portfolio)
        execution = execute_paper_cycle(
            plan=plan,
            account_state=self.account(),
            quotes=[self.quote(market_type="spot")],
            mark_prices=self.prices(),
            policy=self.full_policy(),
        )
        report = build_reconciliation_report(
            plan=plan,
            starting_positions=portfolio.positions,
            model_targets=strategy.targets,
            account_state=execution.account_state,
            reference_prices=self.prices(),
            modelled_cost="2",
            realized_paper_cost=execution.total_fees,
            source_hash_match=True,
            data_stale=False,
            execution_complete=True,
        )
        row = build_forward_telemetry_row(
            market_snapshot=market,
            plan=plan,
            execution=execution,
            reconciliation=report,
            prior_equity="10000",
            modelled_slippage_bps="8",
            source_hash_match=True,
            data_stale=False,
        )
        expected = {
            "timestamp",
            "strategy_id",
            "source_bundle_sha256",
            "target_hash",
            "realized_position_hash",
            "gross_target",
            "gross_realized",
            "turnover",
            "modelled_slippage_bps",
            "paper_slippage_bps",
            "net_return",
            "equity",
            "drawdown",
            "reconciliation_ok",
            "source_hash_match",
            "data_stale",
            "execution_complete",
        }
        self.assertEqual(set(row), expected)
        self.assertEqual(row["strategy_id"], "v75_atlas_nx")
        self.assertTrue(row["source_hash_match"])

    def test_paper_account_rejects_reduce_only_cross_through_zero(self) -> None:
        state = self.account(perp_quantity="1")
        intent = ExecutionIntent.create(
            instrument="BTC/USDT:USDT",
            market_type="perpetual",
            side="sell",
            reduce_only=True,
            quantity="2",
            quantity_unit="base",
            reference_price="100",
            max_slippage_bps="10",
            reason="bad_reduce",
            not_before_utc="2026-07-27T00:05:00Z",
            expires_at_utc="2026-07-27T00:20:00Z",
        )
        plan_id = "sha256:" + "7" * 64
        state = PaperAccountState.create(
            strategy_id=state.strategy_id,
            sequence=state.sequence + 1,
            as_of_utc=state.as_of_utc,
            cash=state.cash,
            spot_positions=state.spot_positions,
            perp_positions=state.perp_positions,
            perp_entry_prices=state.perp_entry_prices,
            fees_paid=state.fees_paid,
            realized_pnl=state.realized_pnl,
            funding_pnl=state.funding_pnl,
            equity=state.equity,
            high_water=state.high_water,
            last_plan_id=plan_id,
            applied_event_ids=state.applied_event_ids,
        )
        fill = FillEvent.create(
            plan_id=plan_id,
            intent_id=intent.intent_id,
            filled_at_utc="2026-07-27T00:06:00Z",
            status="filled",
            filled_quantity="2",
            price="100",
            fee="0",
            fee_currency="USDT",
            slippage_bps="0",
            source_observation_hash="sha256:" + "8" * 64,
        )
        with self.assertRaises(AccountingHalt):
            apply_fill_event(state, intent, fill)


if __name__ == "__main__":
    unittest.main()
