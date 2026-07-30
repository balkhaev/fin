from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Mapping, Sequence

from finruntime.canonical import (
    ContractError,
    format_utc,
    parse_utc,
    require_decimal_string,
    require_sha256,
)
from finruntime.models import ExecutionIntent, ExecutionPlan, FillEvent
from finruntime.portfolio.accounting import (
    AccountingHalt,
    PaperAccountState,
    apply_fill_event,
    mark_account,
)
from finruntime.portfolio.risk import ReferencePriceBook, decimal_text

_ZERO = Decimal("0")
_MINIMUM_QUANTITY = Decimal("0.000000000001")
_QUOTE_QUALITIES = {"ok", "stale", "outage", "invalid"}


@dataclass(frozen=True, slots=True)
class PaperQuote:
    instrument: str
    market_type: str
    observed_at_utc: str
    source_observation_hash: str
    bid: str | None
    ask: str | None
    mid: str | None
    available_quantity: str
    quality: str = "ok"

    def validate(self) -> None:
        if not self.instrument:
            raise ContractError("paper quote instrument is required")
        if self.market_type not in {"spot", "perpetual"}:
            raise ContractError("paper quote market_type must be spot or perpetual")
        parse_utc(self.observed_at_utc)
        require_sha256(
            self.source_observation_hash,
            field="source_observation_hash",
        )
        if self.quality not in _QUOTE_QUALITIES:
            raise ContractError(f"unsupported paper quote quality: {self.quality}")
        require_decimal_string(
            self.available_quantity,
            field="available_quantity",
            minimum=_ZERO,
        )
        bid = (
            require_decimal_string(
                self.bid,
                field="bid",
                minimum=_MINIMUM_QUANTITY,
            )
            if self.bid is not None
            else None
        )
        ask = (
            require_decimal_string(
                self.ask,
                field="ask",
                minimum=_MINIMUM_QUANTITY,
            )
            if self.ask is not None
            else None
        )
        mid = (
            require_decimal_string(
                self.mid,
                field="mid",
                minimum=_MINIMUM_QUANTITY,
            )
            if self.mid is not None
            else None
        )
        if (bid is None) != (ask is None):
            raise ContractError("paper quote must contain both bid and ask or neither")
        if bid is not None and ask is not None and bid > ask:
            raise ContractError("paper quote bid cannot exceed ask")
        if bid is None and mid is None and self.quality not in {"outage", "invalid"}:
            raise ContractError("paper quote requires bid/ask or mid")


@dataclass(frozen=True, slots=True)
class PaperBrokerPolicy:
    spot_commission_bps: Decimal
    perp_commission_bps: Decimal
    proxy_half_spread_bps: Decimal
    impact_bps: Decimal
    participation_rate: Decimal
    permit_stale_quotes: bool = False

    def validate(self) -> None:
        for name, value in (
            ("spot_commission_bps", self.spot_commission_bps),
            ("perp_commission_bps", self.perp_commission_bps),
            ("proxy_half_spread_bps", self.proxy_half_spread_bps),
            ("impact_bps", self.impact_bps),
        ):
            if value < 0:
                raise ContractError(f"{name} must be non-negative")
        if not (_ZERO < self.participation_rate <= Decimal("1")):
            raise ContractError("participation_rate must be in (0, 1]")
        if type(self.permit_stale_quotes) is not bool:
            raise ContractError("permit_stale_quotes must be a boolean")


DEFAULT_PAPER_BROKER_POLICY = PaperBrokerPolicy(
    spot_commission_bps=Decimal("10"),
    perp_commission_bps=Decimal("6"),
    proxy_half_spread_bps=Decimal("4"),
    impact_bps=Decimal("2"),
    participation_rate=Decimal("0.10"),
    permit_stale_quotes=False,
)


@dataclass(frozen=True, slots=True)
class PaperFillOutcome:
    intent_id: str
    event_id: str
    status: str
    reason: str
    requested_quantity: str
    filled_quantity: str


@dataclass(frozen=True, slots=True)
class PaperExecutionResult:
    account_state: PaperAccountState
    fill_events: tuple[FillEvent, ...]
    outcomes: tuple[PaperFillOutcome, ...]
    total_filled_notional: str
    total_fees: str
    weighted_slippage_bps: str
    execution_complete: bool


QuoteKey = tuple[str, str]


def _quote_key(intent: ExecutionIntent) -> QuoteKey:
    return intent.market_type, intent.instrument


def _quote_map(
    quotes: Sequence[PaperQuote] | Mapping[QuoteKey, PaperQuote],
) -> dict[QuoteKey, PaperQuote]:
    values = quotes.values() if isinstance(quotes, Mapping) else quotes
    output: dict[QuoteKey, PaperQuote] = {}
    for quote in values:
        quote.validate()
        key = (quote.market_type, quote.instrument)
        existing = output.get(key)
        if existing is not None and existing != quote:
            raise ContractError(f"conflicting paper quotes for {key}")
        output[key] = quote
    return output


def _quote_capacities(
    quotes: Mapping[QuoteKey, PaperQuote],
    policy: PaperBrokerPolicy,
) -> dict[QuoteKey, Decimal]:
    """Return the shared executable quantity available from each sealed quote.

    A quote is one liquidity observation. Its participation allowance is shared by all
    intents using that quote (for example, a perpetual sign-flip close and child open),
    rather than being reset independently for every intent.
    """

    return {
        key: require_decimal_string(
            quote.available_quantity,
            field=f"quote.available_quantity.{key[0]}:{key[1]}",
            minimum=_ZERO,
        )
        * policy.participation_rate
        for key, quote in quotes.items()
    }


def _rejection_fill(
    *,
    plan: ExecutionPlan,
    intent: ExecutionIntent,
    status: str,
    event_time_utc: str,
    source_hash: str,
) -> FillEvent:
    return FillEvent.create(
        plan_id=plan.plan_id,
        intent_id=intent.intent_id,
        filled_at_utc=event_time_utc,
        status=status,
        filled_quantity="0",
        price="0",
        fee="0",
        fee_currency="USDT",
        slippage_bps="0",
        source_observation_hash=source_hash,
    )


def _commission_bps(
    intent: ExecutionIntent,
    policy: PaperBrokerPolicy,
) -> Decimal:
    return (
        policy.spot_commission_bps
        if intent.market_type == "spot"
        else policy.perp_commission_bps
    )


def _base_fill_price(
    intent: ExecutionIntent,
    quote: PaperQuote,
    policy: PaperBrokerPolicy,
) -> Decimal:
    if quote.bid is not None and quote.ask is not None:
        return require_decimal_string(
            quote.ask if intent.side == "buy" else quote.bid,
            field="quote side price",
            minimum=_MINIMUM_QUANTITY,
        )
    if quote.mid is None:
        raise ContractError("paper quote has no executable price")
    mid = require_decimal_string(
        quote.mid,
        field="mid",
        minimum=_MINIMUM_QUANTITY,
    )
    spread = policy.proxy_half_spread_bps / Decimal("10000")
    return mid * (
        Decimal("1") + spread
        if intent.side == "buy"
        else Decimal("1") - spread
    )


def _adverse_price(
    intent: ExecutionIntent,
    quote: PaperQuote,
    policy: PaperBrokerPolicy,
) -> Decimal:
    base = _base_fill_price(intent, quote, policy)
    impact = policy.impact_bps / Decimal("10000")
    return base * (
        Decimal("1") + impact
        if intent.side == "buy"
        else Decimal("1") - impact
    )


def _slippage_bps(intent: ExecutionIntent, fill_price: Decimal) -> Decimal:
    reference = require_decimal_string(
        intent.reference_price,
        field="intent.reference_price",
        minimum=_MINIMUM_QUANTITY,
    )
    if intent.side == "buy":
        adverse = (fill_price - reference) / reference
    else:
        adverse = (reference - fill_price) / reference
    return max(_ZERO, adverse * Decimal("10000"))


def _current_reduce_capacity(
    account: PaperAccountState,
    intent: ExecutionIntent,
) -> Decimal | None:
    if not intent.reduce_only:
        return None
    if intent.market_type == "spot":
        if intent.side != "sell":
            raise AccountingHalt("reduce-only spot intent must sell")
        raw = account.spot_positions.get(intent.instrument, "0")
        return require_decimal_string(
            raw,
            field="spot reduce capacity",
            minimum=_ZERO,
        )
    raw = account.perp_positions.get(intent.instrument, "0")
    current = require_decimal_string(raw, field="perp reduce capacity")
    if current == 0:
        return _ZERO
    expected_side = "sell" if current > 0 else "buy"
    if intent.side != expected_side:
        raise AccountingHalt(
            "reduce-only perpetual intent points in the wrong direction"
        )
    return abs(current)


def _intent_quantity(intent: ExecutionIntent) -> Decimal:
    return require_decimal_string(
        intent.quantity,
        field=f"intent.quantity.{intent.intent_id}",
        minimum=_MINIMUM_QUANTITY,
    )


def _plan_progress(
    plan: ExecutionPlan,
    account: PaperAccountState,
) -> tuple[dict[str, ExecutionIntent], dict[str, Decimal]]:
    if account.last_plan_id != plan.plan_id:
        raise AccountingHalt("paper account is not activated for this plan")
    if account.schema_version == "1.0":
        raise AccountingHalt("legacy account state has no active plan progress")
    intents: dict[str, ExecutionIntent] = {}
    for intent in plan.intents:
        if intent.intent_id in intents:
            raise AccountingHalt("execution plan contains duplicate intent ids")
        intents[intent.intent_id] = intent
    progress: dict[str, Decimal] = {}
    for intent_id, raw in account.active_plan_filled_quantities.items():
        intent = intents.get(intent_id)
        if intent is None:
            raise AccountingHalt(
                "account progress references an intent outside the plan"
            )
        quantity = require_decimal_string(
            raw,
            field=f"active_plan_filled_quantities.{intent_id}",
            minimum=_MINIMUM_QUANTITY,
        )
        if quantity > _intent_quantity(intent):
            raise AccountingHalt("account progress exceeds an intent quantity")
        progress[intent_id] = quantity
    return intents, progress


def _plan_complete(
    intents: Mapping[str, ExecutionIntent],
    progress: Mapping[str, Decimal],
) -> bool:
    return all(
        progress.get(intent_id, _ZERO) == _intent_quantity(intent)
        for intent_id, intent in intents.items()
    )


def _make_outcome(
    intent: ExecutionIntent,
    fill: FillEvent,
    reason: str,
    requested_quantity: Decimal,
) -> PaperFillOutcome:
    return PaperFillOutcome(
        intent_id=intent.intent_id,
        event_id=fill.event_id,
        status=fill.status,
        reason=reason,
        requested_quantity=decimal_text(requested_quantity),
        filled_quantity=fill.filled_quantity,
    )


def execute_paper_plan(
    *,
    plan: ExecutionPlan,
    account_state: PaperAccountState,
    quotes: Sequence[PaperQuote] | Mapping[QuoteKey, PaperQuote],
    mark_prices: ReferencePriceBook,
    policy: PaperBrokerPolicy = DEFAULT_PAPER_BROKER_POLICY,
) -> PaperExecutionResult:
    """Execute a plan against immutable paper observations; no order API exists."""

    plan.validate()
    account_state.validate()
    policy.validate()
    if plan.strategy_id != account_state.strategy_id:
        raise AccountingHalt("paper plan and account strategy_id mismatch")

    intents_by_id, progress = _plan_progress(plan, account_state)
    if _plan_complete(intents_by_id, progress):
        return PaperExecutionResult(
            account_state=account_state,
            fill_events=(),
            outcomes=(),
            total_filled_notional="0",
            total_fees="0",
            weighted_slippage_bps="0",
            execution_complete=True,
        )

    quote_by_key = _quote_map(quotes)
    remaining_liquidity = _quote_capacities(quote_by_key, policy)
    state = account_state
    fills: list[FillEvent] = []
    outcomes: list[PaperFillOutcome] = []
    total_notional = _ZERO
    total_fees = _ZERO
    slippage_notional = _ZERO
    latest_time = max(
        parse_utc(plan.created_at_utc),
        parse_utc(state.as_of_utc),
    )

    for intent in plan.intents:
        intent.validate()
        total_requested = _intent_quantity(intent)
        already_filled = progress.get(intent.intent_id, _ZERO)
        requested = total_requested - already_filled
        if requested == 0:
            continue

        quote_key = _quote_key(intent)
        quote = quote_by_key.get(quote_key)
        source_hash = (
            quote.source_observation_hash
            if quote is not None
            else plan.market_snapshot_id
        )
        event_time = (
            quote.observed_at_utc
            if quote is not None
            else state.as_of_utc
        )
        latest_time = max(latest_time, parse_utc(event_time))

        parent = (
            intents_by_id.get(intent.parent_intent_id)
            if intent.parent_intent_id is not None
            else None
        )
        if intent.parent_intent_id is not None and parent is None:
            raise AccountingHalt("execution intent parent is outside the plan")

        filled_quantity = _ZERO
        if (
            parent is not None
            and progress.get(parent.intent_id, _ZERO) != _intent_quantity(parent)
        ):
            fill = _rejection_fill(
                plan=plan,
                intent=intent,
                status="rejected",
                event_time_utc=event_time,
                source_hash=source_hash,
            )
            reason = "parent_intent_not_fully_filled"
        elif quote is None:
            fill = _rejection_fill(
                plan=plan,
                intent=intent,
                status="rejected",
                event_time_utc=event_time,
                source_hash=source_hash,
            )
            reason = "missing_quote"
        elif parse_utc(quote.observed_at_utc) > parse_utc(intent.expires_at_utc):
            fill = _rejection_fill(
                plan=plan,
                intent=intent,
                status="expired",
                event_time_utc=quote.observed_at_utc,
                source_hash=source_hash,
            )
            reason = "quote_after_expiry"
        elif parse_utc(quote.observed_at_utc) < parse_utc(intent.not_before_utc):
            fill = _rejection_fill(
                plan=plan,
                intent=intent,
                status="rejected",
                event_time_utc=quote.observed_at_utc,
                source_hash=source_hash,
            )
            reason = "quote_before_not_before"
        elif quote.quality in {"outage", "invalid"} or (
            quote.quality == "stale" and not policy.permit_stale_quotes
        ):
            fill = _rejection_fill(
                plan=plan,
                intent=intent,
                status="rejected",
                event_time_utc=quote.observed_at_utc,
                source_hash=source_hash,
            )
            reason = f"quote_quality_{quote.quality}"
        else:
            capacity = remaining_liquidity[quote_key]
            reduce_capacity = _current_reduce_capacity(state, intent)
            fill_quantity = min(requested, capacity)
            if reduce_capacity is not None:
                fill_quantity = min(fill_quantity, reduce_capacity)
            if fill_quantity < _MINIMUM_QUANTITY:
                fill = _rejection_fill(
                    plan=plan,
                    intent=intent,
                    status="rejected",
                    event_time_utc=quote.observed_at_utc,
                    source_hash=source_hash,
                )
                reason = "insufficient_executable_liquidity"
            else:
                fill_price = _adverse_price(intent, quote, policy)
                slippage = _slippage_bps(intent, fill_price)
                max_slippage = require_decimal_string(
                    intent.max_slippage_bps,
                    field="intent.max_slippage_bps",
                    minimum=_ZERO,
                )
                if slippage > max_slippage:
                    fill = _rejection_fill(
                        plan=plan,
                        intent=intent,
                        status="rejected",
                        event_time_utc=quote.observed_at_utc,
                        source_hash=source_hash,
                    )
                    reason = "maximum_slippage_exceeded"
                else:
                    with localcontext() as context:
                        context.prec = 50
                        notional = fill_quantity * fill_price
                        fee = (
                            notional
                            * _commission_bps(intent, policy)
                            / Decimal("10000")
                        )
                    status = (
                        "filled"
                        if fill_quantity == requested
                        else "partial"
                    )
                    fill = FillEvent.create(
                        plan_id=plan.plan_id,
                        intent_id=intent.intent_id,
                        filled_at_utc=quote.observed_at_utc,
                        status=status,
                        filled_quantity=decimal_text(fill_quantity),
                        price=decimal_text(fill_price),
                        fee=decimal_text(fee),
                        fee_currency="USDT",
                        slippage_bps=decimal_text(slippage),
                        source_observation_hash=source_hash,
                    )
                    reason = (
                        "executed"
                        if status == "filled"
                        else "partial_liquidity_fill"
                    )
                    filled_quantity = fill_quantity
                    total_notional += notional
                    total_fees += fee
                    slippage_notional += slippage * notional

        state = apply_fill_event(state, intent, fill)
        fills.append(fill)
        outcomes.append(_make_outcome(intent, fill, reason, requested))
        if fill.status in {"partial", "filled"}:
            progress[intent.intent_id] = already_filled + filled_quantity
            remaining = remaining_liquidity[quote_key] - filled_quantity
            if remaining < 0:
                raise AccountingHalt("paper quote liquidity was over-consumed")
            remaining_liquidity[quote_key] = remaining

    marked = mark_account(
        state,
        as_of_utc=format_utc(latest_time),
        reference_prices=mark_prices,
    )
    weighted_slippage = (
        slippage_notional / total_notional
        if total_notional > 0
        else _ZERO
    )
    final_progress = {
        key: require_decimal_string(
            value,
            field=f"active_plan_filled_quantities.{key}",
        )
        for key, value in marked.active_plan_filled_quantities.items()
    }
    return PaperExecutionResult(
        account_state=marked,
        fill_events=tuple(fills),
        outcomes=tuple(outcomes),
        total_filled_notional=decimal_text(total_notional),
        total_fees=decimal_text(total_fees),
        weighted_slippage_bps=decimal_text(weighted_slippage),
        execution_complete=_plan_complete(intents_by_id, final_progress),
    )
