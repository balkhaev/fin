from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from typing import Mapping

from .config import Settings
from .gateways import ExchangeGateway, GatewayError
from .models import (
    Candidate,
    OrderState,
    PositionLeg,
    PositionState,
    PositionStatus,
    Side,
    now_ms,
)
from .store import SQLiteStore


class LiveAuthorizationError(RuntimeError):
    pass


class ExecutionError(RuntimeError):
    pass


class ExternalPositionError(ExecutionError):
    pass


class UnclosedPositionError(ExecutionError):
    pass


def authorize_live(settings: Settings, cli_confirmed: bool) -> None:
    """Require three independent signals before any private trading call."""
    if not settings.live.enabled:
        raise LiveAuthorizationError("live.enabled is false in configuration")
    if not cli_confirmed:
        raise LiveAuthorizationError("--confirm-live was not supplied")
    actual = os.getenv(settings.live.confirmation_env, "")
    if actual != settings.live.confirmation_phrase:
        raise LiveAuthorizationError(
            f"environment variable {settings.live.confirmation_env} does not match the confirmation phrase"
        )


@dataclass(slots=True)
class _FillAccumulator:
    filled_base: float = 0.0
    weighted_quote: float = 0.0
    order_ids: list[str] | None = None

    def __post_init__(self) -> None:
        if self.order_ids is None:
            self.order_ids = []

    def add(self, order: OrderState) -> None:
        if order.order_id and order.order_id not in self.order_ids:
            self.order_ids.append(order.order_id)
        if order.filled_base <= 0:
            return
        price = order.average_price or 0.0
        self.filled_base += order.filled_base
        self.weighted_quote += order.filled_base * price

    @property
    def average_price(self) -> float:
        if self.filled_base <= 0:
            return 0.0
        return self.weighted_quote / self.filled_base


class LiveExecutor:
    def __init__(
        self,
        settings: Settings,
        gateways: Mapping[str, ExchangeGateway],
        store: SQLiteStore,
    ):
        self.settings = settings
        self.gateways = dict(gateways)
        self.store = store

    def _tolerance(self, base_amount: float) -> float:
        return max(base_amount * self.settings.risk.delta_tolerance_fraction, 1e-10)

    async def _assert_clean_market(self, candidate: Candidate) -> None:
        long_gateway = self.gateways[candidate.long_exchange]
        short_gateway = self.gateways[candidate.short_exchange]
        if not self.settings.live.allow_external_positions:
            long_actual, short_actual = await asyncio.gather(
                long_gateway.fetch_position_base(candidate.long_symbol),
                short_gateway.fetch_position_base(candidate.short_symbol),
            )
            tolerance = self._tolerance(candidate.base_amount)
            if abs(long_actual) > tolerance or abs(short_actual) > tolerance:
                raise ExternalPositionError(
                    "refusing to trade over unmanaged positions: "
                    f"long venue={long_actual:.12g}, short venue={short_actual:.12g}"
                )
        if self.settings.live.require_balance_check:
            long_free, short_free = await asyncio.gather(
                long_gateway.fetch_free_collateral_usdt(),
                short_gateway.fetch_free_collateral_usdt(),
            )
            configs = self.settings.exchange_map()
            long_required = (
                candidate.matched_notional_usdt
                / configs[candidate.long_exchange].leverage
                * self.settings.live.margin_safety_multiplier
            )
            short_required = (
                candidate.matched_notional_usdt
                / configs[candidate.short_exchange].leverage
                * self.settings.live.margin_safety_multiplier
            )
            if long_free is None or short_free is None:
                raise ExecutionError("free collateral could not be verified on both venues")
            if long_free < long_required or short_free < short_required:
                raise ExecutionError(
                    "insufficient verified free collateral: "
                    f"long {long_free:.2f}/{long_required:.2f}, "
                    f"short {short_free:.2f}/{short_required:.2f}"
                )
        # Leverage is changed only after proving there are no unmanaged positions.
        await asyncio.gather(
            long_gateway.prepare_market(candidate.long_symbol),
            short_gateway.prepare_market(candidate.short_symbol),
        )

    async def reconcile_position(self, position: PositionState) -> None:
        long_gateway = self.gateways[position.long_leg.exchange_id]
        short_gateway = self.gateways[position.short_leg.exchange_id]
        long_actual, short_actual = await asyncio.gather(
            long_gateway.fetch_position_base(position.long_leg.symbol),
            short_gateway.fetch_position_base(position.short_leg.symbol),
        )
        tolerance = self._tolerance(
            max(position.long_leg.base_amount, position.short_leg.base_amount)
        )
        expected_long = position.long_leg.base_amount
        expected_short = -position.short_leg.base_amount
        if abs(long_actual - expected_long) > tolerance:
            raise ExternalPositionError(
                f"long position mismatch: expected {expected_long}, got {long_actual}"
            )
        if abs(short_actual - expected_short) > tolerance:
            raise ExternalPositionError(
                f"short position mismatch: expected {expected_short}, got {short_actual}"
            )

    async def _await_market_fill(
        self,
        gateway: ExchangeGateway,
        symbol: str,
        initial: OrderState,
        requested_base: float,
    ) -> OrderState:
        state = initial
        deadline = asyncio.get_running_loop().time() + self.settings.risk.max_unhedged_seconds
        fetched_once = False
        while (
            state.filled_base + self._tolerance(requested_base) < requested_base
            and state.order_id
            and asyncio.get_running_loop().time() < deadline
        ):
            # Some venues return a sparse createOrder response marked closed.
            # Fetch at least once before interpreting a zero fill as final.
            if fetched_once and state.done:
                break
            await asyncio.sleep(min(self.settings.execution.order_poll_seconds, 0.25))
            state = await gateway.fetch_order_state(state.order_id, symbol)
            fetched_once = True
        return state

    async def _confirm_position_delta(
        self,
        gateway: ExchangeGateway,
        symbol: str,
        side: Side,
        position_before: float,
        requested_base: float,
    ) -> float:
        direction = 1.0 if side == Side.BUY else -1.0
        tolerance = self._tolerance(requested_base)
        deadline = asyncio.get_running_loop().time() + self.settings.risk.max_unhedged_seconds
        confirmed = 0.0
        while True:
            position_after = await gateway.fetch_position_base(symbol)
            confirmed = max(confirmed, (position_after - position_before) * direction)
            confirmed = min(requested_base, max(0.0, confirmed))
            if confirmed + tolerance >= requested_base:
                return confirmed
            if asyncio.get_running_loop().time() >= deadline:
                return confirmed
            await asyncio.sleep(min(self.settings.execution.order_poll_seconds, 0.25))

    async def _hedge_exact(
        self,
        gateway: ExchangeGateway,
        symbol: str,
        side: Side,
        required_base: float,
        position_id: str,
    ) -> _FillAccumulator:
        accumulator = _FillAccumulator()
        tolerance = self._tolerance(required_base)
        for attempt in range(1, self.settings.risk.max_retries + 1):
            remaining = required_base - accumulator.filled_base
            if remaining <= tolerance:
                break
            position_before = await gateway.fetch_position_base(symbol)
            order = await gateway.place_market(symbol, side, remaining, reduce_only=False)
            order = await self._await_market_fill(gateway, symbol, order, remaining)
            confirmed_base = await self._confirm_position_delta(
                gateway, symbol, side, position_before, remaining
            )
            if confirmed_base <= tolerance:
                safe_no_fill = order.status.lower() in {
                    "canceled",
                    "cancelled",
                    "rejected",
                    "expired",
                }
                if not safe_no_fill:
                    raise ExecutionError(
                        f"ambiguous market fill on {gateway.id}: "
                        f"order={order.order_id!r}, status={order.status!r}, "
                        f"reported={order.filled_base}, confirmed={confirmed_base}"
                    )
            confirmed_order = OrderState(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                status=order.status,
                requested_base=remaining,
                filled_base=confirmed_base,
                remaining_base=max(0.0, remaining - confirmed_base),
                average_price=order.average_price,
                raw=order.raw,
            )
            accumulator.add(confirmed_order)
            self.store.append_event(
                "hedge_order",
                {
                    "exchange": gateway.id,
                    "symbol": symbol,
                    "side": side.value,
                    "attempt": attempt,
                    "requested_base": remaining,
                    "reported_filled_base": order.filled_base,
                    "confirmed_filled_base": confirmed_base,
                    "status": order.status,
                    "order_id": order.order_id,
                },
                position_id,
            )
        if required_base - accumulator.filled_base > tolerance:
            raise ExecutionError(
                f"hedge incomplete on {gateway.id}: required={required_base}, filled={accumulator.filled_base}"
            )
        return accumulator

    async def open_candidate(self, candidate: Candidate) -> PositionState:
        if self.store.load_active_positions():
            raise ExecutionError("an active position already exists")
        await self._assert_clean_market(candidate)

        long_gateway = self.gateways[candidate.long_exchange]
        short_gateway = self.gateways[candidate.short_exchange]
        if candidate.maker_exchange == candidate.long_exchange:
            maker_gateway = long_gateway
            maker_symbol = candidate.long_symbol
            maker_side = Side.BUY
            hedge_gateway = short_gateway
            hedge_symbol = candidate.short_symbol
            hedge_side = Side.SELL
        else:
            maker_gateway = short_gateway
            maker_symbol = candidate.short_symbol
            maker_side = Side.SELL
            hedge_gateway = long_gateway
            hedge_symbol = candidate.long_symbol
            hedge_side = Side.BUY

        position_id = uuid.uuid4().hex
        opened = now_ms()
        placeholder = PositionState(
            position_id=position_id,
            candidate_id=candidate.candidate_id,
            asset=candidate.asset,
            status=PositionStatus.OPENING,
            long_leg=PositionLeg(
                exchange_id=candidate.long_exchange,
                symbol=candidate.long_symbol,
                side=Side.BUY,
                base_amount=0.0,
                entry_price=0.0,
            ),
            short_leg=PositionLeg(
                exchange_id=candidate.short_exchange,
                symbol=candidate.short_symbol,
                side=Side.SELL,
                base_amount=0.0,
                entry_price=0.0,
            ),
            opened_at_ms=opened,
            updated_at_ms=opened,
            expected_net_bps_at_open=candidate.expected_net_bps,
            metadata={"candidate": candidate.to_dict()},
        )
        self.store.save_position(placeholder)
        self.store.append_event("opening_started", candidate.to_dict(), position_id)

        offset = self.settings.execution.maker_offset_bps / 10_000.0
        if maker_side == Side.BUY:
            maker_price = candidate.maker_reference_price * (1.0 - offset)
        else:
            maker_price = candidate.maker_reference_price * (1.0 + offset)

        maker_state: OrderState | None = None
        maker_filled = 0.0
        maker_weighted_quote = 0.0
        hedge_total = _FillAccumulator()
        try:
            maker_state = await maker_gateway.place_post_only(
                maker_symbol, maker_side, candidate.base_amount, maker_price
            )
            self.store.append_event(
                "maker_order_placed",
                {
                    "exchange": maker_gateway.id,
                    "symbol": maker_symbol,
                    "side": maker_side.value,
                    "order_id": maker_state.order_id,
                    "requested_base": candidate.base_amount,
                    "price": maker_price,
                },
                position_id,
            )
            deadline = (
                asyncio.get_running_loop().time()
                + self.settings.execution.maker_timeout_seconds
            )
            previous_filled = 0.0
            while True:
                incremental = max(0.0, maker_state.filled_base - previous_filled)
                if incremental > self._tolerance(candidate.base_amount):
                    hedge = await self._hedge_exact(
                        hedge_gateway,
                        hedge_symbol,
                        hedge_side,
                        incremental,
                        position_id,
                    )
                    hedge_total.filled_base += hedge.filled_base
                    hedge_total.weighted_quote += hedge.weighted_quote
                    hedge_total.order_ids.extend(hedge.order_ids)
                    previous_filled = maker_state.filled_base
                    maker_filled = maker_state.filled_base
                    maker_weighted_quote = maker_filled * (
                        maker_state.average_price or maker_price
                    )
                if maker_state.done or maker_state.remaining_base <= self._tolerance(candidate.base_amount):
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    break
                await asyncio.sleep(self.settings.execution.order_poll_seconds)
                maker_state = await maker_gateway.fetch_order_state(
                    maker_state.order_id, maker_symbol
                )

            if not maker_state.done and maker_state.order_id:
                await maker_gateway.cancel_order(maker_state.order_id, maker_symbol)
                self.store.append_event(
                    "maker_order_cancel_requested",
                    {"order_id": maker_state.order_id},
                    position_id,
                )
                maker_state = await maker_gateway.fetch_order_state(
                    maker_state.order_id, maker_symbol
                )

            final_incremental = max(0.0, maker_state.filled_base - previous_filled)
            if final_incremental > self._tolerance(candidate.base_amount):
                hedge = await self._hedge_exact(
                    hedge_gateway,
                    hedge_symbol,
                    hedge_side,
                    final_incremental,
                    position_id,
                )
                hedge_total.filled_base += hedge.filled_base
                hedge_total.weighted_quote += hedge.weighted_quote
                hedge_total.order_ids.extend(hedge.order_ids)
                maker_filled = maker_state.filled_base
                maker_weighted_quote = maker_filled * (
                    maker_state.average_price or maker_price
                )

            if maker_filled < candidate.base_amount * self.settings.risk.min_fill_fraction:
                raise ExecutionError(
                    f"maker fill below minimum: {maker_filled}/{candidate.base_amount}"
                )
            if abs(maker_filled - hedge_total.filled_base) > self._tolerance(candidate.base_amount):
                raise ExecutionError(
                    f"leg mismatch after entry: maker={maker_filled}, hedge={hedge_total.filled_base}"
                )

            maker_average = maker_weighted_quote / maker_filled if maker_filled else maker_price
            hedge_average = hedge_total.average_price
            if hedge_average <= 0:
                hedge_average = (
                    candidate.short_entry_price
                    if hedge_side == Side.SELL
                    else candidate.long_entry_price
                )

            if maker_side == Side.BUY:
                long_amount = maker_filled
                short_amount = hedge_total.filled_base
                long_price = maker_average
                short_price = hedge_average
                long_ids = [maker_state.order_id]
                short_ids = list(hedge_total.order_ids)
            else:
                long_amount = hedge_total.filled_base
                short_amount = maker_filled
                long_price = hedge_average
                short_price = maker_average
                long_ids = list(hedge_total.order_ids)
                short_ids = [maker_state.order_id]

            position = PositionState(
                position_id=position_id,
                candidate_id=candidate.candidate_id,
                asset=candidate.asset,
                status=PositionStatus.OPEN,
                long_leg=PositionLeg(
                    exchange_id=candidate.long_exchange,
                    symbol=candidate.long_symbol,
                    side=Side.BUY,
                    base_amount=long_amount,
                    entry_price=long_price,
                    order_ids=long_ids,
                ),
                short_leg=PositionLeg(
                    exchange_id=candidate.short_exchange,
                    symbol=candidate.short_symbol,
                    side=Side.SELL,
                    base_amount=short_amount,
                    entry_price=short_price,
                    order_ids=short_ids,
                ),
                opened_at_ms=opened,
                updated_at_ms=now_ms(),
                expected_net_bps_at_open=candidate.expected_net_bps,
                metadata={"candidate": candidate.to_dict()},
            )
            self.store.save_position(position)
            await self.reconcile_position(position)
            self.store.append_event("position_opened", position.to_dict(), position_id)
            return position
        except Exception as exc:
            placeholder.status = PositionStatus.ERROR
            placeholder.updated_at_ms = now_ms()
            placeholder.error = f"{type(exc).__name__}: {exc}"
            self.store.save_position(placeholder)
            self.store.append_event(
                "opening_failed",
                {"error": placeholder.error},
                position_id,
            )
            try:
                await self.flatten_symbols(
                    candidate.long_exchange,
                    candidate.long_symbol,
                    candidate.short_exchange,
                    candidate.short_symbol,
                    position_id,
                )
            except Exception as flatten_exc:
                self.store.append_event(
                    "emergency_flatten_failed",
                    {"error": f"{type(flatten_exc).__name__}: {flatten_exc}"},
                    position_id,
                )
                raise UnclosedPositionError(
                    f"entry failed ({exc}); emergency flatten also failed ({flatten_exc})"
                ) from flatten_exc
            placeholder.status = PositionStatus.CLOSED
            placeholder.updated_at_ms = now_ms()
            self.store.save_position(placeholder)
            raise

    async def _flatten_one(
        self,
        gateway: ExchangeGateway,
        symbol: str,
        position_id: str | None,
    ) -> None:
        tolerance = 1e-10
        last = 0.0
        for attempt in range(1, self.settings.risk.max_retries + 1):
            actual = await gateway.fetch_position_base(symbol)
            last = actual
            if abs(actual) <= tolerance:
                return
            side = Side.SELL if actual > 0 else Side.BUY
            order = await gateway.place_market(
                symbol, side, abs(actual), reduce_only=True
            )
            self.store.append_event(
                "reduce_only_close",
                {
                    "exchange": gateway.id,
                    "symbol": symbol,
                    "attempt": attempt,
                    "position_before": actual,
                    "order_id": order.order_id,
                    "filled_base": order.filled_base,
                    "status": order.status,
                },
                position_id,
            )
            await asyncio.sleep(self.settings.execution.close_retry_delay_seconds)
        last = await gateway.fetch_position_base(symbol)
        if abs(last) > tolerance:
            raise UnclosedPositionError(
                f"could not flatten {gateway.id} {symbol}; remaining base={last}"
            )

    async def flatten_symbols(
        self,
        long_exchange: str,
        long_symbol: str,
        short_exchange: str,
        short_symbol: str,
        position_id: str | None = None,
    ) -> None:
        results = await asyncio.gather(
            self._flatten_one(
                self.gateways[long_exchange], long_symbol, position_id
            ),
            self._flatten_one(
                self.gateways[short_exchange], short_symbol, position_id
            ),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, BaseException)]
        if errors:
            raise UnclosedPositionError("; ".join(str(error) for error in errors))

    async def close_position(self, position: PositionState, reason: str) -> PositionState:
        position.status = PositionStatus.CLOSING
        position.updated_at_ms = now_ms()
        self.store.save_position(position)
        self.store.append_event("closing_started", {"reason": reason}, position.position_id)
        try:
            await self.flatten_symbols(
                position.long_leg.exchange_id,
                position.long_leg.symbol,
                position.short_leg.exchange_id,
                position.short_leg.symbol,
                position.position_id,
            )
        except Exception as exc:
            position.status = PositionStatus.ERROR
            position.error = f"{type(exc).__name__}: {exc}"
            position.updated_at_ms = now_ms()
            self.store.save_position(position)
            raise
        position.status = PositionStatus.CLOSED
        position.updated_at_ms = now_ms()
        position.metadata["close_reason"] = reason
        self.store.save_position(position)
        self.store.append_event("position_closed", position.to_dict(), position.position_id)
        return position
