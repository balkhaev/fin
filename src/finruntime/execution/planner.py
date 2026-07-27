from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, localcontext
from typing import Mapping

from finruntime.canonical import (
    ContractError,
    format_utc,
    parse_utc,
    require_decimal_string,
    sha256_id,
)
from finruntime.models import (
    ExecutionIntent,
    ExecutionPlan,
    MarketSnapshot,
    PortfolioState,
    StrategySnapshot,
)
from finruntime.portfolio.risk import (
    ReferencePriceBook,
    RiskDecision,
    decimal_text,
    get_reference_price,
)
from finruntime.registry import assert_mode

_ZERO = Decimal("0")


class PlanningHalt(ContractError):
    """Raised when a deterministic plan cannot be generated safely."""


@dataclass(frozen=True, slots=True)
class PlannerPolicy:
    spot_max_slippage_bps: Decimal
    perp_max_slippage_bps: Decimal
    intent_ttl_seconds: int
    minimum_quantity: Decimal

    def validate(self) -> None:
        if self.spot_max_slippage_bps < 0 or self.perp_max_slippage_bps < 0:
            raise ContractError("maximum slippage must be non-negative")
        if self.intent_ttl_seconds < 1:
            raise ContractError("intent TTL must be positive")
        if self.minimum_quantity <= 0:
            raise ContractError("minimum quantity must be positive")


DEFAULT_PLANNER_POLICY = PlannerPolicy(
    spot_max_slippage_bps=Decimal("10"),
    perp_max_slippage_bps=Decimal("10"),
    intent_ttl_seconds=900,
    minimum_quantity=Decimal("0.000000000001"),
)


def _parse_book(
    book: Mapping[str, Mapping[str, str]],
    *,
    field_prefix: str,
    spot_non_negative: bool,
) -> dict[str, dict[str, Decimal]]:
    unsupported = set(book) - {"spot", "perp"}
    if unsupported:
        raise ContractError(f"unsupported book sections: {sorted(unsupported)}")
    output: dict[str, dict[str, Decimal]] = {"spot": {}, "perp": {}}
    for market_type in ("spot", "perp"):
        raw_side = book.get(market_type, {})
        if not isinstance(raw_side, Mapping):
            raise ContractError(f"{field_prefix}.{market_type} must be a mapping")
        for instrument, raw_value in raw_side.items():
            if not instrument:
                raise ContractError("instrument cannot be empty")
            value = require_decimal_string(
                raw_value,
                field=f"{field_prefix}.{market_type}.{instrument}",
            )
            if market_type == "spot" and spot_non_negative and value < 0:
                raise ContractError("spot quantities and target weights cannot be negative")
            if value != 0:
                output[market_type][str(instrument)] = value
    return output


def _intent_times(market_snapshot: MarketSnapshot, ttl_seconds: int) -> tuple[str, str]:
    start = parse_utc(market_snapshot.decision_time_utc)
    return format_utc(start), format_utc(start + timedelta(seconds=ttl_seconds))


def _side_for_signed_quantity(value: Decimal) -> str:
    if value > 0:
        return "buy"
    if value < 0:
        return "sell"
    raise ContractError("zero signed quantity has no execution side")


def _opposite_side(value: Decimal) -> str:
    if value > 0:
        return "sell"
    if value < 0:
        return "buy"
    raise ContractError("zero position has no reducing side")


def _target_base_quantity(weight: Decimal, equity: Decimal, price: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        return weight * equity / price


def _make_intent(
    *,
    instrument: str,
    market_type: str,
    side: str,
    reduce_only: bool,
    quantity: Decimal,
    reference_price: Decimal,
    reason: str,
    not_before_utc: str,
    expires_at_utc: str,
    max_slippage_bps: Decimal,
    parent_intent_id: str | None = None,
) -> ExecutionIntent:
    return ExecutionIntent.create(
        instrument=instrument,
        market_type="spot" if market_type == "spot" else "perpetual",
        side=side,
        reduce_only=reduce_only,
        quantity=decimal_text(quantity),
        quantity_unit="base",
        reference_price=decimal_text(reference_price),
        max_slippage_bps=decimal_text(max_slippage_bps),
        reason=reason,
        parent_intent_id=parent_intent_id,
        not_before_utc=not_before_utc,
        expires_at_utc=expires_at_utc,
    )


def _spot_intents(
    *,
    instrument: str,
    current: Decimal,
    target: Decimal,
    price: Decimal,
    policy: PlannerPolicy,
    not_before_utc: str,
    expires_at_utc: str,
) -> tuple[list[ExecutionIntent], list[ExecutionIntent]]:
    reductions: list[ExecutionIntent] = []
    increases: list[ExecutionIntent] = []
    delta = target - current
    if abs(delta) < policy.minimum_quantity:
        return reductions, increases
    if delta < 0:
        reductions.append(
            _make_intent(
                instrument=instrument,
                market_type="spot",
                side="sell",
                reduce_only=True,
                quantity=abs(delta),
                reference_price=price,
                reason="spot_reduce",
                not_before_utc=not_before_utc,
                expires_at_utc=expires_at_utc,
                max_slippage_bps=policy.spot_max_slippage_bps,
            )
        )
    else:
        increases.append(
            _make_intent(
                instrument=instrument,
                market_type="spot",
                side="buy",
                reduce_only=False,
                quantity=delta,
                reference_price=price,
                reason="spot_increase",
                not_before_utc=not_before_utc,
                expires_at_utc=expires_at_utc,
                max_slippage_bps=policy.spot_max_slippage_bps,
            )
        )
    return reductions, increases


def _perp_intents(
    *,
    instrument: str,
    current: Decimal,
    target: Decimal,
    price: Decimal,
    policy: PlannerPolicy,
    not_before_utc: str,
    expires_at_utc: str,
) -> tuple[list[ExecutionIntent], list[ExecutionIntent]]:
    reductions: list[ExecutionIntent] = []
    increases: list[ExecutionIntent] = []
    if current != 0 and target != 0 and (current > 0) != (target > 0):
        close = _make_intent(
            instrument=instrument,
            market_type="perp",
            side=_opposite_side(current),
            reduce_only=True,
            quantity=abs(current),
            reference_price=price,
            reason="perpetual_sign_flip_close",
            not_before_utc=not_before_utc,
            expires_at_utc=expires_at_utc,
            max_slippage_bps=policy.perp_max_slippage_bps,
        )
        open_intent = _make_intent(
            instrument=instrument,
            market_type="perp",
            side=_side_for_signed_quantity(target),
            reduce_only=False,
            quantity=abs(target),
            reference_price=price,
            reason="perpetual_sign_flip_open",
            parent_intent_id=close.intent_id,
            not_before_utc=not_before_utc,
            expires_at_utc=expires_at_utc,
            max_slippage_bps=policy.perp_max_slippage_bps,
        )
        reductions.append(close)
        increases.append(open_intent)
        return reductions, increases

    if current == target:
        return reductions, increases
    if current == 0:
        if abs(target) >= policy.minimum_quantity:
            increases.append(
                _make_intent(
                    instrument=instrument,
                    market_type="perp",
                    side=_side_for_signed_quantity(target),
                    reduce_only=False,
                    quantity=abs(target),
                    reference_price=price,
                    reason="perpetual_open",
                    not_before_utc=not_before_utc,
                    expires_at_utc=expires_at_utc,
                    max_slippage_bps=policy.perp_max_slippage_bps,
                )
            )
        return reductions, increases
    if target == 0:
        if abs(current) >= policy.minimum_quantity:
            reductions.append(
                _make_intent(
                    instrument=instrument,
                    market_type="perp",
                    side=_opposite_side(current),
                    reduce_only=True,
                    quantity=abs(current),
                    reference_price=price,
                    reason="perpetual_close",
                    not_before_utc=not_before_utc,
                    expires_at_utc=expires_at_utc,
                    max_slippage_bps=policy.perp_max_slippage_bps,
                )
            )
        return reductions, increases

    if (current > 0) != (target > 0):
        raise ContractError("unhandled perpetual sign transition")
    difference = abs(target) - abs(current)
    if abs(difference) < policy.minimum_quantity:
        return reductions, increases
    if difference < 0:
        reductions.append(
            _make_intent(
                instrument=instrument,
                market_type="perp",
                side=_opposite_side(current),
                reduce_only=True,
                quantity=abs(difference),
                reference_price=price,
                reason="perpetual_reduce",
                not_before_utc=not_before_utc,
                expires_at_utc=expires_at_utc,
                max_slippage_bps=policy.perp_max_slippage_bps,
            )
        )
    else:
        increases.append(
            _make_intent(
                instrument=instrument,
                market_type="perp",
                side=_side_for_signed_quantity(target),
                reduce_only=False,
                quantity=difference,
                reference_price=price,
                reason="perpetual_increase",
                not_before_utc=not_before_utc,
                expires_at_utc=expires_at_utc,
                max_slippage_bps=policy.perp_max_slippage_bps,
            )
        )
    return reductions, increases


def build_execution_plan(
    *,
    strategy_snapshot: StrategySnapshot,
    portfolio_state: PortfolioState,
    market_snapshot: MarketSnapshot,
    risk_decision: RiskDecision,
    reference_prices: ReferencePriceBook,
    mode: str | None = None,
    policy: PlannerPolicy = DEFAULT_PLANNER_POLICY,
) -> ExecutionPlan:
    """Build a deterministic plan; this function has no submit or fill side effect."""

    policy.validate()
    strategy_snapshot.validate()
    portfolio_state.validate()
    market_snapshot.validate()
    if strategy_snapshot.strategy_id != portfolio_state.strategy_id:
        raise PlanningHalt("strategy snapshot and portfolio state strategy_id mismatch")
    if strategy_snapshot.strategy_id != risk_decision.strategy_id:
        raise PlanningHalt("risk decision strategy_id mismatch")
    if strategy_snapshot.market_snapshot_id != market_snapshot.snapshot_id:
        raise PlanningHalt("strategy snapshot market_snapshot_id mismatch")

    selected_mode = mode or (
        "shadow" if strategy_snapshot.strategy_id == "v136_execution_shadow" else "paper"
    )
    assert_mode(strategy_snapshot.strategy_id, selected_mode)

    current = _parse_book(
        portfolio_state.positions,
        field_prefix="portfolio_state.positions",
        spot_non_negative=True,
    )
    target_weights = _parse_book(
        risk_decision.targets,
        field_prefix="risk_decision.targets",
        spot_non_negative=True,
    )
    equity = require_decimal_string(
        portfolio_state.equity,
        field="portfolio_state.equity",
        minimum=Decimal("0.00000001"),
    )
    not_before_utc, expires_at_utc = _intent_times(
        market_snapshot, policy.intent_ttl_seconds
    )

    reductions: list[ExecutionIntent] = []
    increases: list[ExecutionIntent] = []
    for market_type in ("spot", "perp"):
        instruments = sorted(set(current[market_type]) | set(target_weights[market_type]))
        for instrument in instruments:
            price = get_reference_price(reference_prices, market_type, instrument)
            current_quantity = current[market_type].get(instrument, _ZERO)
            target_weight = target_weights[market_type].get(instrument, _ZERO)
            target_quantity = _target_base_quantity(target_weight, equity, price)
            if market_type == "spot":
                reduced, increased = _spot_intents(
                    instrument=instrument,
                    current=current_quantity,
                    target=target_quantity,
                    price=price,
                    policy=policy,
                    not_before_utc=not_before_utc,
                    expires_at_utc=expires_at_utc,
                )
            else:
                reduced, increased = _perp_intents(
                    instrument=instrument,
                    current=current_quantity,
                    target=target_quantity,
                    price=price,
                    policy=policy,
                    not_before_utc=not_before_utc,
                    expires_at_utc=expires_at_utc,
                )
            reductions.extend(reduced)
            increases.extend(increased)

    reductions.sort(key=lambda intent: (intent.market_type, intent.instrument, intent.reason))
    increases.sort(key=lambda intent: (intent.market_type, intent.instrument, intent.reason))
    if increases and not risk_decision.risk_increase_permitted:
        raise PlanningHalt("risk-increasing intent escaped fail-closed risk decision")

    execution_target_hash = sha256_id(
        {
            "strategy_id": strategy_snapshot.strategy_id,
            "market_snapshot_id": market_snapshot.snapshot_id,
            "source_target_hash": strategy_snapshot.target_hash,
            "constrained_targets": risk_decision.targets,
            "risk_reasons": risk_decision.reasons,
        }
    )
    plan = ExecutionPlan.create(
        strategy_id=strategy_snapshot.strategy_id,
        mode=selected_mode,
        created_at_utc=market_snapshot.decision_time_utc,
        market_snapshot_id=market_snapshot.snapshot_id,
        state_sequence=strategy_snapshot.state_sequence,
        target_hash=execution_target_hash,
        intents=tuple(reductions + increases),
        risk_summary={
            "source_strategy_target_hash": strategy_snapshot.target_hash,
            "portfolio_state_hash": portfolio_state.state_hash,
            "gross_before": risk_decision.gross_before,
            "gross_requested": risk_decision.gross_requested,
            "gross_after_target": risk_decision.gross_after,
            "spot_gross_after": risk_decision.spot_gross_after,
            "perp_gross_after": risk_decision.perp_gross_after,
            "effective_gross_cap": risk_decision.effective_gross_cap,
            "required_fraction_after": risk_decision.required_fraction_after,
            "risk_increase_permitted": risk_decision.risk_increase_permitted,
            "accelerator_permitted": risk_decision.accelerator_permitted,
            "risk_reasons": risk_decision.reasons,
            "quality_flags": risk_decision.quality_flags,
            "risk_reducing_intents": len(reductions),
            "risk_increasing_intents": len(increases),
        },
    )

    if portfolio_state.pending_plan_id is not None and portfolio_state.pending_plan_id != plan.plan_id:
        raise PlanningHalt("a different pending execution plan already exists")
    if (
        portfolio_state.last_market_snapshot_id == market_snapshot.snapshot_id
        and portfolio_state.last_target_hash == execution_target_hash
        and portfolio_state.last_plan_hash is not None
        and portfolio_state.last_plan_hash != plan.plan_hash
    ):
        raise PlanningHalt("replanning identical state produced a different plan hash")
    return plan
