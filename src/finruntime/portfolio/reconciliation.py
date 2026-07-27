from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any, Mapping

from finruntime.canonical import ContractError, require_decimal_string, sha256_id
from finruntime.models import ExecutionPlan, MarketSnapshot, ReconciliationReport
from finruntime.portfolio.accounting import (
    AccountingHalt,
    PaperAccountState,
    margin_buffer_fraction,
)
from finruntime.portfolio.risk import ReferencePriceBook, decimal_text, get_reference_price

if TYPE_CHECKING:
    from finruntime.execution.paper_broker import PaperExecutionResult

_ZERO = Decimal("0")


def _parse_positions(
    value: Mapping[str, Mapping[str, str]],
) -> dict[str, dict[str, Decimal]]:
    unsupported = set(value) - {"spot", "perp"}
    if unsupported:
        raise ContractError(f"unsupported position sections: {sorted(unsupported)}")
    output: dict[str, dict[str, Decimal]] = {"spot": {}, "perp": {}}
    for market_type in ("spot", "perp"):
        side = value.get(market_type, {})
        if not isinstance(side, Mapping):
            raise ContractError(f"positions.{market_type} must be a mapping")
        for instrument, raw in side.items():
            number = require_decimal_string(
                raw, field=f"positions.{market_type}.{instrument}"
            )
            if market_type == "spot" and number < 0:
                raise ContractError("spot position cannot be negative")
            if number != 0:
                output[market_type][str(instrument)] = number
    return output


def _position_text(
    value: Mapping[str, Mapping[str, Decimal]],
) -> dict[str, dict[str, str]]:
    return {
        market_type: {
            instrument: decimal_text(quantity)
            for instrument, quantity in sorted(value[market_type].items())
            if quantity != 0
        }
        for market_type in ("spot", "perp")
    }


def project_plan_positions(
    *,
    starting_positions: Mapping[str, Mapping[str, str]],
    plan: ExecutionPlan,
) -> dict[str, dict[str, str]]:
    """Project the full-fill position book implied by the immutable plan."""

    plan.validate()
    positions = _parse_positions(starting_positions)
    for intent in plan.intents:
        intent.validate()
        market_type = "spot" if intent.market_type == "spot" else "perp"
        current = positions[market_type].get(intent.instrument, _ZERO)
        quantity = require_decimal_string(
            intent.quantity,
            field="intent.quantity",
            minimum=Decimal("0.000000000001"),
        )
        delta = quantity if intent.side == "buy" else -quantity
        if intent.reduce_only:
            if market_type == "spot":
                if intent.side != "sell" or quantity > current:
                    raise AccountingHalt("invalid reduce-only spot intent in plan projection")
            else:
                if current == 0 or (current > 0) == (delta > 0) or quantity > abs(current):
                    raise AccountingHalt("invalid reduce-only perpetual intent in plan projection")
        next_quantity = current + delta
        if market_type == "spot" and next_quantity < 0:
            raise AccountingHalt("plan projection creates a short spot position")
        if intent.reduce_only and current != 0 and next_quantity != 0:
            if (next_quantity > 0) != (current > 0):
                raise AccountingHalt("reduce-only plan intent crosses through zero")
        if next_quantity == 0:
            positions[market_type].pop(intent.instrument, None)
        else:
            positions[market_type][intent.instrument] = next_quantity
    return _position_text(positions)


def _tracking_error_fraction(
    *,
    planned_positions: Mapping[str, Mapping[str, str]],
    paper_positions: Mapping[str, Mapping[str, str]],
    reference_prices: ReferencePriceBook,
    equity: Decimal,
) -> Decimal:
    planned = _parse_positions(planned_positions)
    paper = _parse_positions(paper_positions)
    error_notional = _ZERO
    for market_type in ("spot", "perp"):
        instruments = set(planned[market_type]) | set(paper[market_type])
        for instrument in instruments:
            difference = paper[market_type].get(instrument, _ZERO) - planned[
                market_type
            ].get(instrument, _ZERO)
            price = get_reference_price(reference_prices, market_type, instrument)
            error_notional += abs(difference) * price
    return error_notional / equity


def _gross_fraction(
    *,
    positions: Mapping[str, Mapping[str, str]],
    reference_prices: ReferencePriceBook,
    equity: Decimal,
) -> Decimal:
    parsed = _parse_positions(positions)
    notional = sum(
        (
            abs(quantity)
            * get_reference_price(reference_prices, market_type, instrument)
            for market_type in ("spot", "perp")
            for instrument, quantity in parsed[market_type].items()
        ),
        _ZERO,
    )
    return notional / equity


def build_reconciliation_report(
    *,
    plan: ExecutionPlan,
    starting_positions: Mapping[str, Mapping[str, str]],
    model_targets: Mapping[str, Any],
    account_state: PaperAccountState,
    reference_prices: ReferencePriceBook,
    modelled_cost: str,
    realized_paper_cost: str,
    source_hash_match: bool,
    data_stale: bool,
    execution_complete: bool,
    tracking_error_warn: Decimal = Decimal("0.02"),
    maintenance_margin_ratio: Decimal = Decimal("0.10"),
) -> ReconciliationReport:
    plan.validate()
    account_state.validate()
    if plan.strategy_id != account_state.strategy_id:
        raise AccountingHalt("plan and paper account strategy_id mismatch")
    planned = project_plan_positions(
        starting_positions=starting_positions,
        plan=plan,
    )
    paper = {
        "spot": dict(account_state.spot_positions),
        "perp": dict(account_state.perp_positions),
    }
    equity = require_decimal_string(
        account_state.equity,
        field="account_state.equity",
        minimum=Decimal("0.00000001"),
    )
    tracking = _tracking_error_fraction(
        planned_positions=planned,
        paper_positions=paper,
        reference_prices=reference_prices,
        equity=equity,
    )
    margin = margin_buffer_fraction(
        account_state,
        reference_prices=reference_prices,
        maintenance_margin_ratio=maintenance_margin_ratio,
    )
    model_cost = require_decimal_string(
        modelled_cost, field="modelled_cost", minimum=_ZERO
    )
    realized_cost = require_decimal_string(
        realized_paper_cost, field="realized_paper_cost", minimum=_ZERO
    )

    alerts: list[str] = []
    status = "ok"
    if not source_hash_match:
        alerts.append("source_hash_mismatch")
        status = "halt"
    if data_stale:
        alerts.append("stale_market_data")
        status = "halt"
    if margin < 0:
        alerts.append("negative_margin_buffer")
        status = "halt"
    if tracking > tracking_error_warn:
        alerts.append("tracking_error_above_tolerance")
        if status != "halt":
            status = "warn"
    if not execution_complete:
        alerts.append("execution_incomplete")
        if status != "halt":
            status = "warn"
    if model_cost > 0 and realized_cost > model_cost * Decimal("1.5"):
        alerts.append("paper_cost_above_1_5x_model")
        if status != "halt":
            status = "warn"

    return ReconciliationReport.create(
        strategy_id=plan.strategy_id,
        as_of_utc=account_state.as_of_utc,
        model_targets=dict(model_targets),
        planned_positions=planned,
        paper_positions=paper,
        tracking_error_fraction=decimal_text(tracking),
        modelled_cost=decimal_text(model_cost),
        realized_paper_cost=decimal_text(realized_cost),
        funding_pnl=account_state.funding_pnl,
        margin_buffer=decimal_text(margin),
        alerts=alerts,
        status=status,
    )


def build_forward_telemetry_row(
    *,
    market_snapshot: MarketSnapshot,
    plan: ExecutionPlan,
    execution: PaperExecutionResult,
    reconciliation: ReconciliationReport,
    prior_equity: str,
    modelled_slippage_bps: str,
    source_hash_match: bool,
    data_stale: bool,
) -> dict[str, object]:
    """Build one V429-compatible reconciled forward telemetry row."""

    market_snapshot.validate()
    plan.validate()
    execution.account_state.validate()
    reconciliation.validate()
    if not (
        plan.strategy_id
        == execution.account_state.strategy_id
        == reconciliation.strategy_id
    ):
        raise AccountingHalt("telemetry strategy_id mismatch")
    if plan.market_snapshot_id != market_snapshot.snapshot_id:
        raise AccountingHalt("telemetry market snapshot mismatch")

    previous = require_decimal_string(
        prior_equity,
        field="prior_equity",
        minimum=Decimal("0.00000001"),
    )
    current = require_decimal_string(
        execution.account_state.equity,
        field="account_state.equity",
        minimum=Decimal("0.00000001"),
    )
    high_water = require_decimal_string(
        execution.account_state.high_water,
        field="account_state.high_water",
        minimum=Decimal("0.00000001"),
    )
    filled_notional = require_decimal_string(
        execution.total_filled_notional,
        field="total_filled_notional",
        minimum=_ZERO,
    )
    modelled_slippage = require_decimal_string(
        modelled_slippage_bps,
        field="modelled_slippage_bps",
        minimum=_ZERO,
    )
    gross_realized = _gross_fraction(
        positions={
            "spot": execution.account_state.spot_positions,
            "perp": execution.account_state.perp_positions,
        },
        reference_prices={
            "spot": {
                instrument: market_snapshot.spot[instrument]
                for instrument in execution.account_state.spot_positions
                if instrument in market_snapshot.spot
            },
            "perp": {
                instrument: market_snapshot.perp[instrument]
                for instrument in execution.account_state.perp_positions
                if instrument in market_snapshot.perp
            },
        },
        equity=current,
    )
    realized_position_hash = sha256_id(
        {
            "strategy_id": execution.account_state.strategy_id,
            "spot": execution.account_state.spot_positions,
            "perp": execution.account_state.perp_positions,
            "account_hash": execution.account_state.account_hash,
        }
    )
    return {
        "timestamp": execution.account_state.as_of_utc,
        "strategy_id": plan.strategy_id,
        "source_bundle_sha256": market_snapshot.snapshot_id,
        "target_hash": plan.target_hash,
        "realized_position_hash": realized_position_hash,
        "gross_target": str(plan.risk_summary.get("gross_after_target", "0")),
        "gross_realized": decimal_text(gross_realized),
        "turnover": decimal_text(filled_notional / previous),
        "modelled_slippage_bps": decimal_text(modelled_slippage),
        "paper_slippage_bps": execution.weighted_slippage_bps,
        "net_return": decimal_text(current / previous - Decimal("1")),
        "equity": execution.account_state.equity,
        "drawdown": decimal_text(current / high_water - Decimal("1")),
        "reconciliation_ok": reconciliation.status == "ok",
        "source_hash_match": bool(source_hash_match),
        "data_stale": bool(data_stale),
        "execution_complete": bool(execution.execution_complete),
    }
