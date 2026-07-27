from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

from finruntime.canonical import ContractError
from finruntime.execution.planner import PlanningHalt, build_execution_plan
from finruntime.models import MarketSnapshot, PortfolioState, SourceObservation, StrategySnapshot
from finruntime.portfolio.risk import RiskLimits, apply_pretrade_risk


class RiskPlannerTests(unittest.TestCase):
    def market(self, *, quality: str = "ok") -> MarketSnapshot:
        source = SourceObservation(
            source="spot_daily",
            source_timestamp_utc="2026-07-27T00:00:00Z",
            available_at_utc="2026-07-27T00:01:00Z",
            payload_sha256="sha256:" + "1" * 64,
            quality=quality,
        )
        return MarketSnapshot.create(
            as_of_utc="2026-07-27T00:00:00Z",
            decision_time_utc="2026-07-27T00:05:00Z",
            sources={"spot_daily": source},
            spot={"BTC/USDT": {"reference_price": "100"}},
            perp={
                "BTC/USDT:USDT": {"reference_price": "100"},
                "ETH/USDT:USDT": {"reference_price": "100"},
            },
        )

    def strategy(
        self,
        market: MarketSnapshot,
        *,
        targets: dict[str, dict[str, str]],
        gross: str,
        strategy_id: str = "v75_atlas_nx",
        gross_cap: str = "1.05",
    ) -> StrategySnapshot:
        return StrategySnapshot.create(
            strategy_id=strategy_id,
            strategy_version="runtime-v1",
            decision_time_utc=market.decision_time_utc,
            market_snapshot_id=market.snapshot_id,
            state_sequence=7,
            targets=targets,
            gross_target=gross,
            cash_target="0.20",
            risk={"gross_cap": gross_cap},
        )

    def portfolio(
        self,
        *,
        strategy_id: str = "v75_atlas_nx",
        positions: dict[str, dict[str, str]] | None = None,
        pending_plan_id: str | None = None,
        last_market_snapshot_id: str | None = None,
        last_target_hash: str | None = None,
        last_plan_hash: str | None = None,
    ) -> PortfolioState:
        return PortfolioState.create(
            strategy_id=strategy_id,
            sequence=7,
            as_of_utc="2026-07-27T00:05:00Z",
            cash="8000",
            equity="10000",
            high_water="10000",
            positions=positions or {"spot": {}, "perp": {}},
            held_targets={"spot": {}, "perp": {}},
            pending_plan_id=pending_plan_id,
            last_market_snapshot_id=last_market_snapshot_id,
            last_target_hash=last_target_hash,
            last_plan_hash=last_plan_hash,
        )

    def prices(self) -> dict[str, dict[str, str]]:
        return {
            "spot": {"BTC/USDT": "100"},
            "perp": {
                "BTC/USDT:USDT": "100",
                "ETH/USDT:USDT": "100",
            },
        }

    def risk(
        self,
        strategy: StrategySnapshot,
        portfolio: PortfolioState,
        market: MarketSnapshot,
        *,
        limits: RiskLimits | None = None,
    ):
        kwargs = {}
        if limits is not None:
            kwargs["limits"] = limits
        return apply_pretrade_risk(
            strategy_snapshot=strategy,
            portfolio_state=portfolio,
            market_snapshot=market,
            reference_prices=self.prices(),
            critical_sources=("spot_daily",),
            **kwargs,
        )

    def test_gross_cap_scales_pro_rata(self) -> None:
        market = self.market()
        strategy = self.strategy(
            market,
            targets={"spot": {"BTC/USDT": "0.8"}, "perp": {"ETH/USDT:USDT": "0.8"}},
            gross="1.6",
            gross_cap="1.0",
        )
        decision = self.risk(strategy, self.portfolio(), market)
        self.assertEqual(decision.gross_after, "1")
        self.assertEqual(decision.targets["spot"]["BTC/USDT"], "0.5")
        self.assertEqual(decision.targets["perp"]["ETH/USDT:USDT"], "0.5")
        self.assertIn("gross_cap_scaled", decision.reasons)

    def test_collateral_budget_scales_market_exposure(self) -> None:
        market = self.market()
        strategy = self.strategy(
            market,
            targets={"spot": {"BTC/USDT": "0.9"}, "perp": {"ETH/USDT:USDT": "0.4"}},
            gross="1.3",
            gross_cap="2",
        )
        limits = RiskLimits(
            gross_cap=Decimal("2"),
            initial_margin_ratio=Decimal("0.25"),
            operational_reserve=Decimal("0.20"),
        )
        decision = self.risk(strategy, self.portfolio(), market, limits=limits)
        self.assertEqual(decision.targets["spot"]["BTC/USDT"], "0.72")
        self.assertEqual(decision.targets["perp"]["ETH/USDT:USDT"], "0.32")
        self.assertEqual(decision.required_fraction_after, "1")
        self.assertIn("collateral_budget_scaled", decision.reasons)

    def test_stale_critical_source_blocks_instrument_risk_increase(self) -> None:
        market = self.market(quality="stale")
        portfolio = self.portfolio(positions={"spot": {"BTC/USDT": "20"}, "perp": {}})
        strategy = self.strategy(
            market,
            targets={"spot": {"BTC/USDT": "0.3"}, "perp": {"ETH/USDT:USDT": "0.1"}},
            gross="0.4",
        )
        decision = self.risk(strategy, portfolio, market)
        self.assertFalse(decision.risk_increase_permitted)
        self.assertEqual(decision.targets, {"spot": {"BTC/USDT": "0.2"}, "perp": {}})
        self.assertIn("critical_data_blocks_risk_increase", decision.reasons)

    def test_stale_source_still_allows_reduction(self) -> None:
        market = self.market(quality="stale")
        portfolio = self.portfolio(positions={"spot": {"BTC/USDT": "20"}, "perp": {}})
        strategy = self.strategy(
            market,
            targets={"spot": {"BTC/USDT": "0.1"}, "perp": {}},
            gross="0.1",
        )
        decision = self.risk(strategy, portfolio, market)
        self.assertEqual(decision.targets["spot"]["BTC/USDT"], "0.1")

    def test_stale_source_sign_flip_becomes_close_only(self) -> None:
        market = self.market(quality="stale")
        portfolio = self.portfolio(
            positions={"spot": {}, "perp": {"BTC/USDT:USDT": "2"}}
        )
        strategy = self.strategy(
            market,
            targets={"spot": {}, "perp": {"BTC/USDT:USDT": "-0.02"}},
            gross="0.02",
        )
        decision = self.risk(strategy, portfolio, market)
        self.assertEqual(decision.targets, {"spot": {}, "perp": {}})

    def test_declared_gross_mismatch_fails(self) -> None:
        market = self.market()
        strategy = self.strategy(
            market,
            targets={"spot": {"BTC/USDT": "0.2"}, "perp": {}},
            gross="0.3",
        )
        with self.assertRaises(ContractError):
            self.risk(strategy, self.portfolio(), market)

    def test_planner_is_deterministic_and_uses_base_quantity(self) -> None:
        market = self.market()
        strategy = self.strategy(
            market,
            targets={"spot": {"BTC/USDT": "0.2"}, "perp": {}},
            gross="0.2",
        )
        portfolio = self.portfolio()
        decision = self.risk(strategy, portfolio, market)
        first = build_execution_plan(
            strategy_snapshot=strategy,
            portfolio_state=portfolio,
            market_snapshot=market,
            risk_decision=decision,
            reference_prices=self.prices(),
        )
        second = build_execution_plan(
            strategy_snapshot=strategy,
            portfolio_state=portfolio,
            market_snapshot=market,
            risk_decision=decision,
            reference_prices=self.prices(),
        )
        self.assertEqual(first.plan_id, second.plan_id)
        self.assertEqual(len(first.intents), 1)
        self.assertEqual(first.intents[0].quantity, "20")
        self.assertEqual(first.intents[0].side, "buy")
        self.assertFalse(first.intents[0].reduce_only)

    def test_risk_reductions_precede_increases(self) -> None:
        market = self.market()
        portfolio = self.portfolio(positions={"spot": {"BTC/USDT": "20"}, "perp": {}})
        strategy = self.strategy(
            market,
            targets={"spot": {"BTC/USDT": "0.1"}, "perp": {"ETH/USDT:USDT": "0.1"}},
            gross="0.2",
        )
        decision = self.risk(strategy, portfolio, market)
        plan = build_execution_plan(
            strategy_snapshot=strategy,
            portfolio_state=portfolio,
            market_snapshot=market,
            risk_decision=decision,
            reference_prices=self.prices(),
        )
        self.assertEqual([intent.reduce_only for intent in plan.intents], [True, False])
        self.assertEqual(plan.intents[0].reason, "spot_reduce")
        self.assertEqual(plan.intents[1].reason, "perpetual_open")

    def test_perpetual_sign_flip_is_split_close_then_open(self) -> None:
        market = self.market()
        portfolio = self.portfolio(
            positions={"spot": {}, "perp": {"BTC/USDT:USDT": "2"}}
        )
        strategy = self.strategy(
            market,
            targets={"spot": {}, "perp": {"BTC/USDT:USDT": "-0.03"}},
            gross="0.03",
        )
        decision = self.risk(strategy, portfolio, market)
        plan = build_execution_plan(
            strategy_snapshot=strategy,
            portfolio_state=portfolio,
            market_snapshot=market,
            risk_decision=decision,
            reference_prices=self.prices(),
        )
        self.assertEqual(len(plan.intents), 2)
        close, open_intent = plan.intents
        self.assertTrue(close.reduce_only)
        self.assertEqual(close.side, "sell")
        self.assertEqual(close.quantity, "2")
        self.assertEqual(close.reason, "perpetual_sign_flip_close")
        self.assertFalse(open_intent.reduce_only)
        self.assertEqual(open_intent.side, "sell")
        self.assertEqual(open_intent.quantity, "3")
        self.assertEqual(open_intent.parent_intent_id, close.intent_id)

    def test_missing_reference_price_halts_planning(self) -> None:
        market = self.market()
        strategy = self.strategy(
            market,
            targets={"spot": {}, "perp": {"ETH/USDT:USDT": "0.1"}},
            gross="0.1",
        )
        portfolio = self.portfolio()
        decision = self.risk(strategy, portfolio, market)
        with self.assertRaises(ContractError):
            build_execution_plan(
                strategy_snapshot=strategy,
                portfolio_state=portfolio,
                market_snapshot=market,
                risk_decision=decision,
                reference_prices={"spot": {}, "perp": {}},
            )

    def test_stale_context_generates_reduction_only_plan(self) -> None:
        market = self.market(quality="stale")
        portfolio = self.portfolio(positions={"spot": {"BTC/USDT": "20"}, "perp": {}})
        strategy = self.strategy(
            market,
            targets={"spot": {"BTC/USDT": "0.3"}, "perp": {"ETH/USDT:USDT": "0.1"}},
            gross="0.4",
        )
        decision = self.risk(strategy, portfolio, market)
        plan = build_execution_plan(
            strategy_snapshot=strategy,
            portfolio_state=portfolio,
            market_snapshot=market,
            risk_decision=decision,
            reference_prices=self.prices(),
        )
        self.assertEqual(plan.intents, ())

    def test_v136_defaults_to_shadow_mode(self) -> None:
        market = self.market()
        strategy = self.strategy(
            market,
            strategy_id="v136_execution_shadow",
            targets={"spot": {"BTC/USDT": "0.1"}, "perp": {}},
            gross="0.1",
        )
        portfolio = self.portfolio(strategy_id="v136_execution_shadow")
        decision = self.risk(strategy, portfolio, market)
        plan = build_execution_plan(
            strategy_snapshot=strategy,
            portfolio_state=portfolio,
            market_snapshot=market,
            risk_decision=decision,
            reference_prices=self.prices(),
        )
        self.assertEqual(plan.mode, "shadow")

    def test_different_pending_plan_fails_closed(self) -> None:
        market = self.market()
        strategy = self.strategy(
            market,
            targets={"spot": {"BTC/USDT": "0.1"}, "perp": {}},
            gross="0.1",
        )
        portfolio = self.portfolio(pending_plan_id="sha256:" + "9" * 64)
        decision = self.risk(strategy, portfolio, market)
        with self.assertRaises(PlanningHalt):
            build_execution_plan(
                strategy_snapshot=strategy,
                portfolio_state=portfolio,
                market_snapshot=market,
                risk_decision=decision,
                reference_prices=self.prices(),
            )

    def test_strategy_identity_mismatch_fails(self) -> None:
        market = self.market()
        strategy = self.strategy(
            market,
            targets={"spot": {}, "perp": {}},
            gross="0",
        )
        portfolio = self.portfolio(strategy_id="v28_growth_control")
        with self.assertRaises(ContractError):
            self.risk(strategy, portfolio, market)


if __name__ == "__main__":
    unittest.main()
