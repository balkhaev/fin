from __future__ import annotations

import asyncio
import math
import statistics
import time
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .config import ExchangeSettings, ServiceSettings
from .models import FundingQuote, MarketSnapshot, OrderBook, OrderState, Side, now_ms


class GatewayError(RuntimeError):
    pass


class UnsupportedMarketError(GatewayError):
    pass


@runtime_checkable
class ExchangeGateway(Protocol):
    id: str
    markets: tuple[str, ...]

    async def initialize(self) -> None: ...

    async def fetch_snapshot(self, symbol: str) -> MarketSnapshot: ...

    async def prepare_market(self, symbol: str) -> None: ...

    async def fetch_free_collateral_usdt(self) -> float | None: ...

    async def place_post_only(
        self, symbol: str, side: Side, base_amount: float, price: float
    ) -> OrderState: ...

    async def place_market(
        self, symbol: str, side: Side, base_amount: float, *, reduce_only: bool = False
    ) -> OrderState: ...

    async def fetch_order_state(self, order_id: str, symbol: str) -> OrderState: ...

    async def cancel_order(self, order_id: str, symbol: str) -> None: ...

    async def fetch_position_base(self, symbol: str) -> float: ...

    async def close(self) -> None: ...


def parse_interval_hours(value: Any, default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        # CCXT interval can be milliseconds on some exchange-specific payloads.
        if numeric > 10_000:
            return numeric / 3_600_000.0
        return numeric
    if isinstance(value, str):
        text = value.strip().lower().replace("hours", "h").replace("hour", "h")
        units = (("ms", 1 / 3_600_000), ("s", 1 / 3_600), ("m", 1 / 60), ("h", 1), ("d", 24))
        for suffix, multiplier in units:
            if text.endswith(suffix):
                try:
                    return float(text[: -len(suffix)]) * multiplier
                except ValueError:
                    break
    return default


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _recursive_number(payload: Any, keys: set[str]) -> float | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).lower() in keys:
                parsed = _finite_float(value)
                if parsed is not None:
                    return parsed
        for value in payload.values():
            found = _recursive_number(value, keys)
            if found is not None:
                return found
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            found = _recursive_number(value, keys)
            if found is not None:
                return found
    return None


@dataclass(slots=True)
class _HistoryCacheEntry:
    expires_at: float
    rate: float
    source: str


class CCXTGateway:
    """Thin, defensive adapter over the CCXT asynchronous unified API.

    The adapter accepts and returns *base-asset amounts*. CCXT derivatives APIs
    frequently expect contract counts, so conversion via ``contractSize`` is
    centralized here to keep the execution engine venue-neutral.
    """

    def __init__(self, config: ExchangeSettings, service: ServiceSettings):
        self.config = config
        self.service = service
        self.id = config.id
        self.markets = config.markets
        self._history_cache: dict[str, _HistoryCacheEntry] = {}
        self._initialized = False
        try:
            import ccxt.async_support as ccxt  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - exercised in deployment
            raise GatewayError("ccxt is not installed; run pip install -e .") from exc
        exchange_class = getattr(ccxt, config.exchange_class, None)
        if exchange_class is None:
            raise GatewayError(f"unknown CCXT exchange class: {config.exchange_class}")
        constructor: dict[str, Any] = {
            "enableRateLimit": True,
            "options": dict(config.options),
        }
        constructor.update(config.credentials())
        self._exchange = exchange_class(constructor)
        if config.sandbox:
            self._exchange.set_sandbox_mode(True)

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self._exchange.load_markets()
        for symbol in self.markets:
            if symbol not in self._exchange.markets:
                raise UnsupportedMarketError(f"{self.id}: market not found: {symbol}")
            market = self._exchange.market(symbol)
            if not market.get("contract"):
                raise UnsupportedMarketError(f"{self.id}: not a derivative contract: {symbol}")
            if market.get("inverse") is True:
                raise UnsupportedMarketError(f"{self.id}: inverse contracts are not supported: {symbol}")
        self._initialized = True

    async def close(self) -> None:
        await self._exchange.close()

    async def prepare_market(self, symbol: str) -> None:
        await self.initialize()
        has = getattr(self._exchange, "has", {}) or {}
        if has.get("setLeverage"):
            leverage: int | float
            leverage = int(self.config.leverage) if self.config.leverage.is_integer() else self.config.leverage
            try:
                await self._exchange.set_leverage(leverage, symbol, dict(self.config.params))
            except Exception as exc:
                raise GatewayError(f"{self.id}: failed to set leverage for {symbol}: {exc}") from exc

    async def fetch_free_collateral_usdt(self) -> float | None:
        await self.initialize()
        has = getattr(self._exchange, "has", {}) or {}
        if not has.get("fetchBalance"):
            return None
        try:
            raw = await self._exchange.fetch_balance(dict(self.config.params))
        except Exception as exc:
            raise GatewayError(f"{self.id}: fetch balance failed: {exc}") from exc
        total = 0.0
        found = False
        free = raw.get("free") if isinstance(raw, dict) else None
        for code in ("USDT", "USDC"):
            value = None
            if isinstance(free, dict):
                value = _finite_float(free.get(code))
            if value is None and isinstance(raw, dict) and isinstance(raw.get(code), dict):
                value = _finite_float(raw[code].get("free"))
            if value is not None:
                total += max(0.0, value)
                found = True
        return total if found else None

    def _market(self, symbol: str) -> dict[str, Any]:
        if not self._initialized:
            raise GatewayError(f"{self.id}: gateway is not initialized")
        try:
            return self._exchange.market(symbol)
        except Exception as exc:
            raise UnsupportedMarketError(f"{self.id}: unknown market {symbol}") from exc

    def _contract_size(self, symbol: str) -> float:
        market = self._market(symbol)
        size = _finite_float(market.get("contractSize")) or 1.0
        if size <= 0:
            raise GatewayError(f"{self.id}: invalid contractSize for {symbol}")
        return size

    def _base_to_order_amount(self, symbol: str, base_amount: float) -> float:
        if base_amount <= 0:
            raise GatewayError("base_amount must be positive")
        contracts = base_amount / self._contract_size(symbol)
        try:
            precise = self._exchange.amount_to_precision(symbol, contracts)
            result = float(precise)
        except Exception as exc:
            raise GatewayError(f"{self.id}: cannot quantize amount for {symbol}: {exc}") from exc
        if result <= 0:
            raise GatewayError(f"{self.id}: amount rounds to zero for {symbol}")
        return result

    def _order_amount_to_base(self, symbol: str, amount: Any) -> float:
        parsed = _finite_float(amount) or 0.0
        return parsed * self._contract_size(symbol)

    async def _history_prediction(self, symbol: str, current_rate: float) -> tuple[float, str]:
        cached = self._history_cache.get(symbol)
        monotonic = time.monotonic()
        if cached and cached.expires_at > monotonic:
            return cached.rate, cached.source
        rate = current_rate
        source = "current"
        has = getattr(self._exchange, "has", {}) or {}
        if has.get("fetchFundingRateHistory"):
            try:
                rows = await self._exchange.fetch_funding_rate_history(
                    symbol, None, self.service.funding_history_limit
                )
                values = [
                    parsed
                    for row in rows or []
                    if (parsed := _finite_float(row.get("fundingRate"))) is not None
                ]
                if values:
                    # Median suppresses a single liquidation-driven outlier.
                    rate = float(statistics.median(values[-self.service.funding_history_limit :]))
                    source = "history_median"
            except Exception:
                rate, source = current_rate, "current"
        self._history_cache[symbol] = _HistoryCacheEntry(
            expires_at=monotonic + self.service.history_cache_seconds,
            rate=rate,
            source=source,
        )
        return rate, source

    async def fetch_snapshot(self, symbol: str) -> MarketSnapshot:
        await self.initialize()
        has = getattr(self._exchange, "has", {}) or {}
        if not has.get("fetchFundingRate"):
            raise GatewayError(f"{self.id}: fetchFundingRate is unsupported")

        funding_task = asyncio.create_task(self._exchange.fetch_funding_rate(symbol))
        book_task = asyncio.create_task(
            self._exchange.fetch_order_book(symbol, self.service.order_book_limit)
        )
        oi_task = None
        if has.get("fetchOpenInterest"):
            oi_task = asyncio.create_task(self._exchange.fetch_open_interest(symbol))

        try:
            funding, book_raw = await asyncio.gather(funding_task, book_task)
        except Exception as exc:
            if oi_task:
                oi_task.cancel()
            raise GatewayError(f"{self.id} {symbol}: public data fetch failed: {exc}") from exc

        open_interest_raw: dict[str, Any] | None = None
        if oi_task:
            try:
                open_interest_raw = await oi_task
            except Exception:
                open_interest_raw = None

        order_book = OrderBook.from_iterables(
            book_raw.get("bids") or [],
            book_raw.get("asks") or [],
            book_raw.get("timestamp"),
        )
        market = self._market(symbol)
        asset = str(market.get("base") or symbol.split("/")[0]).upper()
        current_rate = _finite_float(funding.get("fundingRate"))
        if current_rate is None:
            raise GatewayError(f"{self.id} {symbol}: fundingRate missing")

        info = funding.get("info") or {}
        predicted = _finite_float(funding.get("nextFundingRate"))
        prediction_source = "nextFundingRate"
        if predicted is None:
            predicted = _recursive_number(
                info,
                {
                    "predictedfundingrate",
                    "estimatedfundingrate",
                    "nextfundingrate",
                    "fundingrateprediction",
                },
            )
            prediction_source = "exchange_info"
        if predicted is None:
            predicted, prediction_source = await self._history_prediction(symbol, current_rate)

        interval_value = funding.get("interval")
        if interval_value is None:
            interval_value = _recursive_number(info, {"fundingintervalhours", "fundinginterval"})
        interval_hours = parse_interval_hours(
            interval_value, self.config.default_funding_interval_hours
        )
        mark_price = _finite_float(funding.get("markPrice")) or order_book.mid
        index_price = _finite_float(funding.get("indexPrice"))
        funding_timestamp = funding.get("fundingTimestamp") or funding.get("nextFundingTimestamp")
        funding_timestamp_ms = int(funding_timestamp) if _finite_float(funding_timestamp) else None

        open_interest_usdt: float | None = None
        if open_interest_raw:
            open_interest_usdt = _finite_float(open_interest_raw.get("openInterestValue"))
            if open_interest_usdt is None:
                amount = _finite_float(open_interest_raw.get("openInterestAmount"))
                if amount is not None:
                    open_interest_usdt = amount * mark_price

        observed = now_ms()
        quote = FundingQuote(
            exchange_id=self.id,
            symbol=symbol,
            asset=asset,
            funding_rate=current_rate,
            predicted_funding_rate=float(predicted),
            interval_hours=interval_hours,
            funding_timestamp_ms=funding_timestamp_ms,
            mark_price=mark_price,
            index_price=index_price,
            open_interest_usdt=open_interest_usdt,
            observed_at_ms=observed,
            prediction_source=prediction_source,
        )
        return MarketSnapshot(quote=quote, order_book=order_book)

    def _order_state(self, symbol: str, raw: dict[str, Any]) -> OrderState:
        side_raw = str(raw.get("side") or "buy").lower()
        side = Side.BUY if side_raw == "buy" else Side.SELL
        requested = self._order_amount_to_base(symbol, raw.get("amount"))
        filled = self._order_amount_to_base(symbol, raw.get("filled"))
        remaining_raw = raw.get("remaining")
        if remaining_raw is None:
            remaining = max(0.0, requested - filled)
        else:
            remaining = self._order_amount_to_base(symbol, remaining_raw)
        return OrderState(
            order_id=str(raw.get("id") or ""),
            symbol=symbol,
            side=side,
            status=str(raw.get("status") or "open"),
            requested_base=requested,
            filled_base=filled,
            remaining_base=remaining,
            average_price=_finite_float(raw.get("average")) or _finite_float(raw.get("price")),
            raw=raw,
        )

    async def place_post_only(
        self, symbol: str, side: Side, base_amount: float, price: float
    ) -> OrderState:
        await self.initialize()
        amount = self._base_to_order_amount(symbol, base_amount)
        precise_price = float(self._exchange.price_to_precision(symbol, price))
        params = dict(self.config.params)
        params["postOnly"] = True
        try:
            raw = await self._exchange.create_order(
                symbol, "limit", side.value, amount, precise_price, params
            )
        except Exception as exc:
            raise GatewayError(f"{self.id}: post-only order failed: {exc}") from exc
        return self._order_state(symbol, raw)

    async def place_market(
        self, symbol: str, side: Side, base_amount: float, *, reduce_only: bool = False
    ) -> OrderState:
        await self.initialize()
        amount = self._base_to_order_amount(symbol, base_amount)
        params = dict(self.config.params)
        if reduce_only:
            params["reduceOnly"] = True
        try:
            raw = await self._exchange.create_order(
                symbol, "market", side.value, amount, None, params
            )
        except Exception as exc:
            raise GatewayError(f"{self.id}: market order failed: {exc}") from exc
        return self._order_state(symbol, raw)

    async def fetch_order_state(self, order_id: str, symbol: str) -> OrderState:
        try:
            raw = await self._exchange.fetch_order(order_id, symbol)
        except Exception as exc:
            raise GatewayError(f"{self.id}: fetch order failed: {exc}") from exc
        return self._order_state(symbol, raw)

    async def cancel_order(self, order_id: str, symbol: str) -> None:
        try:
            await self._exchange.cancel_order(order_id, symbol)
        except Exception as exc:
            # An already-filled/already-cancelled order is reconciled by a final fetch.
            text = str(exc).lower()
            if not any(token in text for token in ("not found", "already", "closed", "filled", "cancel")):
                raise GatewayError(f"{self.id}: cancel order failed: {exc}") from exc

    async def fetch_position_base(self, symbol: str) -> float:
        await self.initialize()
        has = getattr(self._exchange, "has", {}) or {}
        if not has.get("fetchPositions"):
            raise GatewayError(f"{self.id}: fetchPositions is required for live mode")
        try:
            rows = await self._exchange.fetch_positions([symbol])
        except Exception as exc:
            raise GatewayError(f"{self.id}: fetch positions failed: {exc}") from exc
        total = 0.0
        for row in rows or []:
            if row.get("symbol") and row.get("symbol") != symbol:
                continue
            contracts = _finite_float(row.get("contracts")) or 0.0
            size = _finite_float(row.get("contractSize")) or self._contract_size(symbol)
            base = abs(contracts) * size
            side = str(row.get("side") or "").lower()
            if side == "long":
                total += base
            elif side == "short":
                total -= base
            else:
                # Some exchanges return signed contracts without a side field.
                total += contracts * size
        return total
