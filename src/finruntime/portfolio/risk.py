from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Sequence

from finruntime.canonical import ContractError, require_decimal_string
from finruntime.data.availability import AvailabilityDecision, evaluate_availability
from finruntime.models import MarketSnapshot, PortfolioState, StrategySnapshot

TargetBook = Mapping[str, Mapping[str, str]]
ReferencePriceBook = Mapping[str, Mapping[str, object]]
_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class RiskLimits:
    gross_cap: Decimal
    initial_margin_ratio: Decimal
    operational_reserve: Decimal

    def validate(self) -> None:
        if self.gross_cap <= 0:
            raise ContractError("gross cap must be positive")
        if not (_ZERO <= self.initial_margin_ratio <= _ONE):
            raise ContractError("initial margin ratio must be in [0, 1]")
        if not (_ZERO <= self.operational_reserve < _ONE):
            raise ContractError("operational reserve must be in [0, 1)")


DEFAULT_RISK_LIMITS = RiskLimits(
    gross_cap=Decimal("1.10"),
    initial_margin_ratio=Decimal("0.25"),
    operational_reserve=Decimal("0.20"),
)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    strategy_id: str
    targets: dict[str, dict[str, str]]
    gross_before: str
    gross_requested: str
    gross_after: str
    spot_gross_after: str
    perp_gross_after: str
    effective_gross_cap: str
    required_fraction_after: str
    risk_increase_permitted: bool
    accelerator_permitted: bool
    reasons: tuple[str, ...]
    quality_flags: tuple[str, ...]


def decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value.normalize(), "f")
    return text if text != "-0" else "0"


def _parse_book(
    book: TargetBook,
    *,
    spot_must_be_non_negative: bool,
    field_prefix: str,
) -> dict[str, dict[str, Decimal]]:
    unsupported = set(book) - {"spot", "perp"}
    if unsupported:
        raise ContractError(f"unsupported target sections: {sorted(unsupported)}")
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
            if market_type == "spot" and spot_must_be_non_negative and value < 0:
                raise ContractError("spot weights and quantities cannot be negative")
            if value != 0:
                output[market_type][str(instrument)] = value
    return output


def _canonical_book(
    book: Mapping[str, Mapping[str, Decimal]],
) -> dict[str, dict[str, str]]:
    return {
        market_type: {
            instrument: decimal_text(value)
            for instrument, value in sorted(book.get(market_type, {}).items())
            if value != 0
        }
        for market_type in ("spot", "perp")
    }


def _gross(book: Mapping[str, Mapping[str, Decimal]]) -> Decimal:
    return sum((abs(value) for side in book.values() for value in side.values()), _ZERO)


def _side_gross(
    book: Mapping[str, Mapping[str, Decimal]], market_type: str
) -> Decimal:
    return sum((abs(value) for value in book[market_type].values()), _ZERO)


def _scale_book(
    book: Mapping[str, Mapping[str, Decimal]], factor: Decimal
) -> dict[str, dict[str, Decimal]]:
    if factor < 0:
        raise ContractError("risk scale factor cannot be negative")
    return {
        market_type: {
            instrument: value * factor
            for instrument, value in book[market_type].items()
            if value * factor != 0
        }
        for market_type in ("spot", "perp")
    }


def get_reference_price(
    reference_prices: ReferencePriceBook,
    market_type: str,
    instrument: str,
) -> Decimal:
    side = reference_prices.get(market_type, {})
    if not isinstance(side, Mapping):
        raise ContractError(f"reference prices {market_type!r} must be a mapping")
    raw = side.get(instrument)
    if isinstance(raw, Mapping):
        raw = raw.get("reference_price")
    if raw is None:
        raise ContractError(f"missing reference price for {market_type}:{instrument}")
    return require_decimal_string(
        raw,
        field=f"reference_prices.{market_type}.{instrument}",
        minimum=Decimal("0.000000000001"),
    )


def current_position_weights(
    portfolio_state: PortfolioState,
    reference_prices: ReferencePriceBook,
) -> dict[str, dict[str, Decimal]]:
    portfolio_state.validate()
    equity = require_decimal_string(
        portfolio_state.equity,
        field="portfolio_state.equity",
        minimum=Decimal("0.00000001"),
    )
    positions = _parse_book(
        portfolio_state.positions,
        spot_must_be_non_negative=True,
        field_prefix="portfolio_state.positions",
    )
    output: dict[str, dict[str, Decimal]] = {"spot": {}, "perp": {}}
    for market_type in ("spot", "perp"):
        for instrument, quantity in positions[market_type].items():
            price = get_reference_price(reference_prices, market_type, instrument)
            weight = quantity * price / equity
            if weight != 0:
                output[market_type][instrument] = weight
    return output


def _same_sign_or_zero(left: Decimal, right: Decimal) -> bool:
    return left == 0 or right == 0 or (left > 0) == (right > 0)


def _block_instrument_risk_increase(
    requested: Mapping[str, Mapping[str, Decimal]],
    current: Mapping[str, Mapping[str, Decimal]],
) -> dict[str, dict[str, Decimal]]:
    """Permit only same-sign reductions/closures when critical data is unavailable."""

    output: dict[str, dict[str, Decimal]] = {"spot": {}, "perp": {}}
    for market_type in ("spot", "perp"):
        instruments = set(requested[market_type]) | set(current[market_type])
        for instrument in instruments:
            old = current[market_type].get(instrument, _ZERO)
            new = requested[market_type].get(instrument, _ZERO)
            if old == 0:
                selected = _ZERO
            elif new == 0:
                selected = _ZERO
            elif not _same_sign_or_zero(old, new):
                selected = _ZERO
            elif abs(new) <= abs(old):
                selected = new
            else:
                selected = old
            if selected != 0:
                output[market_type][instrument] = selected
    return output


def _contains_instrument_risk_increase(
    candidate: Mapping[str, Mapping[str, Decimal]],
    current: Mapping[str, Mapping[str, Decimal]],
) -> bool:
    for market_type in ("spot", "perp"):
        instruments = set(candidate[market_type]) | set(current[market_type])
        for instrument in instruments:
            old = current[market_type].get(instrument, _ZERO)
            new = candidate[market_type].get(instrument, _ZERO)
            if old == 0 and new != 0:
                return True
            if old != 0 and new != 0 and (old > 0) != (new > 0):
                return True
            if abs(new) > abs(old):
                return True
    return False


def _effective_gross_cap(
    strategy_snapshot: StrategySnapshot,
    limits: RiskLimits,
) -> Decimal:
    raw = strategy_snapshot.risk.get("gross_cap")
    if raw is None:
        return limits.gross_cap
    configured = require_decimal_string(
        raw,
        field="strategy_snapshot.risk.gross_cap",
        minimum=Decimal("0.00000001"),
    )
    return min(configured, limits.gross_cap)


def apply_pretrade_risk(
    *,
    strategy_snapshot: StrategySnapshot,
    portfolio_state: PortfolioState,
    market_snapshot: MarketSnapshot,
    reference_prices: ReferencePriceBook,
    critical_sources: Sequence[str],
    onchain_sources: Sequence[str] = (),
    limits: RiskLimits = DEFAULT_RISK_LIMITS,
) -> RiskDecision:
    """Apply fail-closed availability, gross and collateral constraints to target weights.

    Strategy targets are equity fractions. Portfolio positions are signed base quantities
    converted to current weights with the supplied deterministic reference-price book.
    The function never creates orders and never changes strategy parameters.
    """

    limits.validate()
    strategy_snapshot.validate()
    portfolio_state.validate()
    market_snapshot.validate()
    if strategy_snapshot.strategy_id != portfolio_state.strategy_id:
        raise ContractError("strategy snapshot and portfolio state must have the same strategy_id")
    if strategy_snapshot.market_snapshot_id != market_snapshot.snapshot_id:
        raise ContractError("strategy snapshot must reference the supplied market snapshot")

    requested = _parse_book(
        strategy_snapshot.targets,
        spot_must_be_non_negative=True,
        field_prefix="strategy_snapshot.targets",
    )
    requested_gross = _gross(requested)
    declared_gross = require_decimal_string(
        strategy_snapshot.gross_target,
        field="strategy_snapshot.gross_target",
        minimum=_ZERO,
    )
    if requested_gross != declared_gross:
        raise ContractError(
            f"declared gross target {declared_gross} does not match target book {requested_gross}"
        )

    current = current_position_weights(portfolio_state, reference_prices)
    gross_before = _gross(current)
    availability: AvailabilityDecision = evaluate_availability(
        market_snapshot,
        critical_sources=critical_sources,
        onchain_sources=onchain_sources,
    )

    selected = {
        "spot": dict(requested["spot"]),
        "perp": dict(requested["perp"]),
    }
    reasons: list[str] = []
    if not availability.risk_increase_permitted:
        selected = _block_instrument_risk_increase(selected, current)
        reasons.append("critical_data_blocks_risk_increase")
        reasons.extend(availability.blocking_reasons)

    effective_cap = _effective_gross_cap(strategy_snapshot, limits)
    gross = _gross(selected)
    if gross > effective_cap:
        selected = _scale_book(selected, effective_cap / gross)
        reasons.append("gross_cap_scaled")

    spot_gross = _side_gross(selected, "spot")
    perp_gross = _side_gross(selected, "perp")
    market_requirement = spot_gross + limits.initial_margin_ratio * perp_gross
    required_fraction = market_requirement + limits.operational_reserve
    if required_fraction > _ONE:
        if market_requirement <= 0:
            raise ContractError("invalid positive collateral requirement without market exposure")
        selected = _scale_book(
            selected,
            (_ONE - limits.operational_reserve) / market_requirement,
        )
        reasons.append("collateral_budget_scaled")
        spot_gross = _side_gross(selected, "spot")
        perp_gross = _side_gross(selected, "perp")
        market_requirement = spot_gross + limits.initial_margin_ratio * perp_gross
        required_fraction = market_requirement + limits.operational_reserve

    if not availability.risk_increase_permitted and _contains_instrument_risk_increase(
        selected, current
    ):
        raise ContractError("fail-closed risk layer created an instrument risk increase")

    gross_after = _gross(selected)
    return RiskDecision(
        strategy_id=strategy_snapshot.strategy_id,
        targets=_canonical_book(selected),
        gross_before=decimal_text(gross_before),
        gross_requested=decimal_text(requested_gross),
        gross_after=decimal_text(gross_after),
        spot_gross_after=decimal_text(spot_gross),
        perp_gross_after=decimal_text(perp_gross),
        effective_gross_cap=decimal_text(effective_cap),
        required_fraction_after=decimal_text(required_fraction),
        risk_increase_permitted=availability.risk_increase_permitted,
        accelerator_permitted=availability.accelerator_permitted,
        reasons=tuple(reasons),
        quality_flags=availability.quality_flags,
    )
