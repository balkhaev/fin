from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from decimal import Decimal, localcontext
from typing import Any, Mapping, Sequence

from finruntime.canonical import (
    ContractError,
    format_utc,
    parse_utc,
    require_decimal_string,
    require_sha256,
    sha256_id,
)
from finruntime.models import ExecutionIntent, FillEvent, PortfolioState
from finruntime.portfolio.risk import ReferencePriceBook, decimal_text, get_reference_price

_ZERO = Decimal("0")


class AccountingHalt(ContractError):
    """Raised when a paper accounting transition would violate an invariant."""


def _hash_payload(instance: Any, excluded: set[str]) -> dict[str, Any]:
    return {
        key: value
        for key, value in asdict(instance).items()
        if key not in excluded
    }


def _decimal_mapping(
    value: Mapping[str, str] | None,
    *,
    field: str,
    non_negative: bool,
) -> dict[str, Decimal]:
    output: dict[str, Decimal] = {}
    for instrument, raw in (value or {}).items():
        if not instrument:
            raise ContractError(f"{field} instrument cannot be empty")
        number = require_decimal_string(raw, field=f"{field}.{instrument}")
        if non_negative and number < 0:
            raise ContractError(f"{field}.{instrument} cannot be negative")
        if number != 0:
            output[str(instrument)] = number
    return output


def _text_mapping(value: Mapping[str, Decimal]) -> dict[str, str]:
    return {
        instrument: decimal_text(number)
        for instrument, number in sorted(value.items())
        if number != 0
    }


@dataclass(frozen=True, slots=True)
class FundingEvent:
    schema_version: str
    event_id: str
    instrument: str
    occurred_at_utc: str
    funding_rate: str
    mark_price: str
    source_observation_hash: str

    @classmethod
    def create(
        cls,
        *,
        instrument: str,
        occurred_at_utc: str,
        funding_rate: str,
        mark_price: str,
        source_observation_hash: str,
    ) -> "FundingEvent":
        provisional = cls(
            schema_version="1.0",
            event_id="sha256:" + "0" * 64,
            instrument=instrument,
            occurred_at_utc=format_utc(occurred_at_utc),
            funding_rate=funding_rate,
            mark_price=mark_price,
            source_observation_hash=require_sha256(
                source_observation_hash, field="source_observation_hash"
            ),
        )
        result = replace(
            provisional,
            event_id=sha256_id(_hash_payload(provisional, {"event_id"})),
        )
        result.validate()
        return result

    def validate(self) -> None:
        if self.schema_version != "1.0" or not self.instrument:
            raise ContractError("invalid FundingEvent identity")
        require_sha256(self.event_id, field="event_id")
        require_sha256(self.source_observation_hash, field="source_observation_hash")
        parse_utc(self.occurred_at_utc)
        require_decimal_string(self.funding_rate, field="funding_rate")
        require_decimal_string(
            self.mark_price,
            field="mark_price",
            minimum=Decimal("0.000000000001"),
        )
        expected = sha256_id(_hash_payload(self, {"event_id"}))
        if self.event_id != expected:
            raise ContractError("FundingEvent hash mismatch")


@dataclass(frozen=True, slots=True)
class PaperAccountState:
    schema_version: str
    strategy_id: str
    sequence: int
    as_of_utc: str
    cash: str
    spot_positions: Mapping[str, str]
    perp_positions: Mapping[str, str]
    perp_entry_prices: Mapping[str, str]
    fees_paid: str
    realized_pnl: str
    funding_pnl: str
    equity: str
    high_water: str
    last_plan_id: str | None
    applied_event_ids: Sequence[str]
    account_hash: str

    @classmethod
    def create(
        cls,
        *,
        strategy_id: str,
        sequence: int,
        as_of_utc: str,
        cash: str,
        spot_positions: Mapping[str, str] | None = None,
        perp_positions: Mapping[str, str] | None = None,
        perp_entry_prices: Mapping[str, str] | None = None,
        fees_paid: str = "0",
        realized_pnl: str = "0",
        funding_pnl: str = "0",
        equity: str,
        high_water: str,
        last_plan_id: str | None = None,
        applied_event_ids: Sequence[str] = (),
    ) -> "PaperAccountState":
        provisional = cls(
            schema_version="1.0",
            strategy_id=strategy_id,
            sequence=int(sequence),
            as_of_utc=format_utc(as_of_utc),
            cash=cash,
            spot_positions=dict(spot_positions or {}),
            perp_positions=dict(perp_positions or {}),
            perp_entry_prices=dict(perp_entry_prices or {}),
            fees_paid=fees_paid,
            realized_pnl=realized_pnl,
            funding_pnl=funding_pnl,
            equity=equity,
            high_water=high_water,
            last_plan_id=last_plan_id,
            applied_event_ids=tuple(applied_event_ids),
            account_hash="sha256:" + "0" * 64,
        )
        result = replace(
            provisional,
            account_hash=sha256_id(_hash_payload(provisional, {"account_hash"})),
        )
        result.validate()
        return result

    @classmethod
    def empty(
        cls,
        *,
        strategy_id: str,
        as_of_utc: str,
        starting_cash: str,
    ) -> "PaperAccountState":
        require_decimal_string(
            starting_cash,
            field="starting_cash",
            minimum=Decimal("0.00000001"),
        )
        return cls.create(
            strategy_id=strategy_id,
            sequence=0,
            as_of_utc=as_of_utc,
            cash=starting_cash,
            equity=starting_cash,
            high_water=starting_cash,
        )

    def validate(self) -> None:
        if self.schema_version != "1.0" or not self.strategy_id:
            raise ContractError("invalid PaperAccountState identity")
        if self.sequence < 0:
            raise ContractError("paper account sequence must be non-negative")
        parse_utc(self.as_of_utc)
        require_decimal_string(self.cash, field="cash", minimum=_ZERO)
        require_decimal_string(self.fees_paid, field="fees_paid", minimum=_ZERO)
        require_decimal_string(self.realized_pnl, field="realized_pnl")
        require_decimal_string(self.funding_pnl, field="funding_pnl")
        equity = require_decimal_string(
            self.equity, field="equity", minimum=Decimal("0.00000001")
        )
        high_water = require_decimal_string(
            self.high_water, field="high_water", minimum=Decimal("0.00000001")
        )
        if high_water < equity:
            raise ContractError("paper account high_water must be >= equity")
        spot = _decimal_mapping(
            self.spot_positions,
            field="spot_positions",
            non_negative=True,
        )
        perp = _decimal_mapping(
            self.perp_positions,
            field="perp_positions",
            non_negative=False,
        )
        entries = _decimal_mapping(
            self.perp_entry_prices,
            field="perp_entry_prices",
            non_negative=True,
        )
        if set(entries) != set(perp):
            raise ContractError("each non-zero perpetual position requires exactly one entry price")
        if any(value <= 0 for value in entries.values()):
            raise ContractError("perpetual entry prices must be positive")
        if self.last_plan_id is not None:
            require_sha256(self.last_plan_id, field="last_plan_id")
        if len(set(self.applied_event_ids)) != len(self.applied_event_ids):
            raise ContractError("applied event ids must be unique")
        for event_id in self.applied_event_ids:
            require_sha256(event_id, field="applied_event_id")
        require_sha256(self.account_hash, field="account_hash")
        expected = sha256_id(_hash_payload(self, {"account_hash"}))
        if self.account_hash != expected:
            raise ContractError("PaperAccountState hash mismatch")
        # Force validation of normalized mappings even when their return values are unused.
        _ = spot, perp, entries

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_portfolio_state(
        self,
        *,
        held_targets: Mapping[str, Mapping[str, str]] | None = None,
        target_age_days: int = 0,
        pending_plan_id: str | None = None,
        last_market_snapshot_id: str | None = None,
        last_target_hash: str | None = None,
        last_plan_hash: str | None = None,
    ) -> PortfolioState:
        return PortfolioState.create(
            strategy_id=self.strategy_id,
            sequence=self.sequence,
            as_of_utc=self.as_of_utc,
            cash=self.cash,
            equity=self.equity,
            high_water=self.high_water,
            positions={
                "spot": dict(self.spot_positions),
                "perp": dict(self.perp_positions),
            },
            held_targets=held_targets or {"spot": {}, "perp": {}},
            target_age_days=target_age_days,
            pending_plan_id=pending_plan_id,
            last_market_snapshot_id=last_market_snapshot_id,
            last_target_hash=last_target_hash,
            last_plan_hash=last_plan_hash,
        )


def _state_values(state: PaperAccountState) -> tuple[
    Decimal,
    dict[str, Decimal],
    dict[str, Decimal],
    dict[str, Decimal],
    Decimal,
    Decimal,
    Decimal,
    Decimal,
    Decimal,
]:
    state.validate()
    return (
        require_decimal_string(state.cash, field="cash"),
        _decimal_mapping(state.spot_positions, field="spot_positions", non_negative=True),
        _decimal_mapping(state.perp_positions, field="perp_positions", non_negative=False),
        _decimal_mapping(state.perp_entry_prices, field="perp_entry_prices", non_negative=True),
        require_decimal_string(state.fees_paid, field="fees_paid"),
        require_decimal_string(state.realized_pnl, field="realized_pnl"),
        require_decimal_string(state.funding_pnl, field="funding_pnl"),
        require_decimal_string(state.equity, field="equity"),
        require_decimal_string(state.high_water, field="high_water"),
    )


def _rebuild(
    state: PaperAccountState,
    *,
    as_of_utc: str,
    cash: Decimal,
    spot: Mapping[str, Decimal],
    perp: Mapping[str, Decimal],
    entries: Mapping[str, Decimal],
    fees: Decimal,
    realized: Decimal,
    funding: Decimal,
    equity: Decimal,
    high_water: Decimal,
    last_plan_id: str | None,
    applied_event_ids: Sequence[str],
    sequence_increment: int = 1,
) -> PaperAccountState:
    if cash < 0:
        raise AccountingHalt("paper cash cannot become negative")
    if equity <= 0:
        raise AccountingHalt("paper equity cannot become non-positive")
    return PaperAccountState.create(
        strategy_id=state.strategy_id,
        sequence=state.sequence + sequence_increment,
        as_of_utc=as_of_utc,
        cash=decimal_text(cash),
        spot_positions=_text_mapping(spot),
        perp_positions=_text_mapping(perp),
        perp_entry_prices=_text_mapping(entries),
        fees_paid=decimal_text(fees),
        realized_pnl=decimal_text(realized),
        funding_pnl=decimal_text(funding),
        equity=decimal_text(equity),
        high_water=decimal_text(max(high_water, equity)),
        last_plan_id=last_plan_id,
        applied_event_ids=tuple(applied_event_ids),
    )


def _apply_perpetual_trade(
    *,
    current: Decimal,
    entry: Decimal | None,
    delta: Decimal,
    fill_price: Decimal,
) -> tuple[Decimal, Decimal | None, Decimal]:
    if delta == 0:
        return current, entry, _ZERO
    if current == 0:
        return delta, fill_price, _ZERO
    if entry is None:
        raise AccountingHalt("non-zero perpetual position lacks an entry price")
    if (current > 0) == (delta > 0):
        new_position = current + delta
        with localcontext() as context:
            context.prec = 50
            new_entry = (
                abs(current) * entry + abs(delta) * fill_price
            ) / abs(new_position)
        return new_position, new_entry, _ZERO

    close_quantity = min(abs(current), abs(delta))
    realized = close_quantity * (fill_price - entry) * (
        Decimal("1") if current > 0 else Decimal("-1")
    )
    new_position = current + delta
    if new_position == 0:
        return _ZERO, None, realized
    if (new_position > 0) == (current > 0):
        return new_position, entry, realized
    raise AccountingHalt("one fill cannot cross a perpetual position through zero")


def apply_fill_event(
    state: PaperAccountState,
    intent: ExecutionIntent,
    fill: FillEvent,
) -> PaperAccountState:
    state.validate()
    intent.validate()
    fill.validate()
    if fill.intent_id != intent.intent_id:
        raise AccountingHalt("fill intent_id mismatch")
    if fill.event_id in state.applied_event_ids:
        return state
    if state.last_plan_id is not None and state.last_plan_id != fill.plan_id:
        raise AccountingHalt("fill belongs to a different plan")

    cash, spot, perp, entries, fees, realized, funding, equity, high_water = _state_values(state)
    applied = tuple(state.applied_event_ids) + (fill.event_id,)
    if fill.status in {"rejected", "expired"}:
        return _rebuild(
            state,
            as_of_utc=fill.filled_at_utc,
            cash=cash,
            spot=spot,
            perp=perp,
            entries=entries,
            fees=fees,
            realized=realized,
            funding=funding,
            equity=equity,
            high_water=high_water,
            last_plan_id=fill.plan_id,
            applied_event_ids=applied,
        )

    quantity = require_decimal_string(
        fill.filled_quantity,
        field="filled_quantity",
        minimum=Decimal("0.000000000001"),
    )
    requested = require_decimal_string(
        intent.quantity,
        field="intent.quantity",
        minimum=Decimal("0.000000000001"),
    )
    if quantity > requested:
        raise AccountingHalt("filled quantity exceeds intent quantity")
    price = require_decimal_string(
        fill.price,
        field="fill.price",
        minimum=Decimal("0.000000000001"),
    )
    fee = require_decimal_string(fill.fee, field="fill.fee", minimum=_ZERO)
    signed_delta = quantity if intent.side == "buy" else -quantity

    if intent.market_type == "spot":
        current = spot.get(intent.instrument, _ZERO)
        if intent.side == "buy":
            if intent.reduce_only:
                raise AccountingHalt("spot buy cannot be reduce-only")
            cost = quantity * price + fee
            if cash < cost:
                raise AccountingHalt("insufficient paper cash for spot buy")
            cash -= cost
            spot[intent.instrument] = current + quantity
        else:
            if quantity > current:
                raise AccountingHalt("spot sell cannot exceed current position")
            cash += quantity * price - fee
            next_quantity = current - quantity
            if next_quantity == 0:
                spot.pop(intent.instrument, None)
            else:
                spot[intent.instrument] = next_quantity
    elif intent.market_type == "perpetual":
        current = perp.get(intent.instrument, _ZERO)
        if intent.reduce_only:
            if current == 0 or (current > 0) == (signed_delta > 0):
                raise AccountingHalt("reduce-only perpetual fill does not reduce current risk")
            if quantity > abs(current):
                raise AccountingHalt("reduce-only perpetual fill crosses through zero")
        next_position, next_entry, realized_delta = _apply_perpetual_trade(
            current=current,
            entry=entries.get(intent.instrument),
            delta=signed_delta,
            fill_price=price,
        )
        cash += realized_delta - fee
        realized += realized_delta
        if next_position == 0:
            perp.pop(intent.instrument, None)
            entries.pop(intent.instrument, None)
        else:
            perp[intent.instrument] = next_position
            if next_entry is None:
                raise AccountingHalt("non-zero perpetual position lost entry price")
            entries[intent.instrument] = next_entry
    else:
        raise AccountingHalt(f"unsupported fill market type: {intent.market_type}")

    fees += fee
    equity_after_fee = equity - fee
    return _rebuild(
        state,
        as_of_utc=fill.filled_at_utc,
        cash=cash,
        spot=spot,
        perp=perp,
        entries=entries,
        fees=fees,
        realized=realized,
        funding=funding,
        equity=equity_after_fee,
        high_water=high_water,
        last_plan_id=fill.plan_id,
        applied_event_ids=applied,
    )


def apply_funding_event(
    state: PaperAccountState,
    event: FundingEvent,
) -> PaperAccountState:
    state.validate()
    event.validate()
    if event.event_id in state.applied_event_ids:
        return state
    cash, spot, perp, entries, fees, realized, funding, equity, high_water = _state_values(state)
    position = perp.get(event.instrument, _ZERO)
    rate = require_decimal_string(event.funding_rate, field="funding_rate")
    mark = require_decimal_string(
        event.mark_price,
        field="mark_price",
        minimum=Decimal("0.000000000001"),
    )
    payment = -(position * mark * rate)
    cash += payment
    funding += payment
    equity += payment
    return _rebuild(
        state,
        as_of_utc=event.occurred_at_utc,
        cash=cash,
        spot=spot,
        perp=perp,
        entries=entries,
        fees=fees,
        realized=realized,
        funding=funding,
        equity=equity,
        high_water=high_water,
        last_plan_id=state.last_plan_id,
        applied_event_ids=tuple(state.applied_event_ids) + (event.event_id,),
    )


def mark_account(
    state: PaperAccountState,
    *,
    as_of_utc: str,
    reference_prices: ReferencePriceBook,
) -> PaperAccountState:
    state.validate()
    cash, spot, perp, entries, fees, realized, funding, _equity, high_water = _state_values(state)
    spot_value = sum(
        (
            quantity * get_reference_price(reference_prices, "spot", instrument)
            for instrument, quantity in spot.items()
        ),
        _ZERO,
    )
    perp_unrealized = sum(
        (
            quantity
            * (
                get_reference_price(reference_prices, "perp", instrument)
                - entries[instrument]
            )
            for instrument, quantity in perp.items()
        ),
        _ZERO,
    )
    equity = cash + spot_value + perp_unrealized
    return _rebuild(
        state,
        as_of_utc=as_of_utc,
        cash=cash,
        spot=spot,
        perp=perp,
        entries=entries,
        fees=fees,
        realized=realized,
        funding=funding,
        equity=equity,
        high_water=high_water,
        last_plan_id=state.last_plan_id,
        applied_event_ids=state.applied_event_ids,
    )


def margin_buffer_fraction(
    state: PaperAccountState,
    *,
    reference_prices: ReferencePriceBook,
    maintenance_margin_ratio: Decimal = Decimal("0.10"),
) -> Decimal:
    if not (_ZERO <= maintenance_margin_ratio <= Decimal("1")):
        raise ContractError("maintenance margin ratio must be in [0, 1]")
    state.validate()
    cash, spot, perp, entries, _fees, _realized, _funding, equity, _high_water = _state_values(state)
    spot_value = sum(
        (
            quantity * get_reference_price(reference_prices, "spot", instrument)
            for instrument, quantity in spot.items()
        ),
        _ZERO,
    )
    perp_unrealized = sum(
        (
            quantity
            * (
                get_reference_price(reference_prices, "perp", instrument)
                - entries[instrument]
            )
            for instrument, quantity in perp.items()
        ),
        _ZERO,
    )
    perp_notional = sum(
        (
            abs(quantity)
            * get_reference_price(reference_prices, "perp", instrument)
            for instrument, quantity in perp.items()
        ),
        _ZERO,
    )
    available = cash + spot_value + perp_unrealized - maintenance_margin_ratio * perp_notional
    return available / equity
