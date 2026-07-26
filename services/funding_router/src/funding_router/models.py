from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Iterable


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class PositionStatus(StrEnum):
    OPENING = "opening"
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    ERROR = "error"


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True, slots=True)
class FundingQuote:
    exchange_id: str
    symbol: str
    asset: str
    funding_rate: float
    predicted_funding_rate: float
    interval_hours: float
    funding_timestamp_ms: int | None
    mark_price: float
    index_price: float | None
    open_interest_usdt: float | None
    observed_at_ms: int
    prediction_source: str = "current"

    def __post_init__(self) -> None:
        if not self.exchange_id:
            raise ValueError("exchange_id is required")
        if not self.symbol:
            raise ValueError("symbol is required")
        if not self.asset:
            raise ValueError("asset is required")
        if not math.isfinite(self.funding_rate):
            raise ValueError("funding_rate must be finite")
        if not math.isfinite(self.predicted_funding_rate):
            raise ValueError("predicted_funding_rate must be finite")
        if self.interval_hours <= 0 or not math.isfinite(self.interval_hours):
            raise ValueError("interval_hours must be positive")
        if self.mark_price <= 0 or not math.isfinite(self.mark_price):
            raise ValueError("mark_price must be positive")

    @property
    def current_rate_per_hour(self) -> float:
        return self.funding_rate / self.interval_hours

    @property
    def predicted_rate_per_hour(self) -> float:
        return self.predicted_funding_rate / self.interval_hours

    @property
    def prediction_confirmed(self) -> bool:
        return self.prediction_source != "current"


@dataclass(frozen=True, slots=True)
class OrderBook:
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]
    timestamp_ms: int | None = None

    def __post_init__(self) -> None:
        for name, levels in (("bids", self.bids), ("asks", self.asks)):
            for price, amount in levels:
                if price <= 0 or amount < 0 or not math.isfinite(price) or not math.isfinite(amount):
                    raise ValueError(f"invalid {name} level: {(price, amount)!r}")

    @classmethod
    def from_iterables(
        cls,
        bids: Iterable[Iterable[float]],
        asks: Iterable[Iterable[float]],
        timestamp_ms: int | None = None,
    ) -> "OrderBook":
        return cls(
            bids=tuple((float(level[0]), float(level[1])) for level in bids if len(level) >= 2),
            asks=tuple((float(level[0]), float(level[1])) for level in asks if len(level) >= 2),
            timestamp_ms=timestamp_ms,
        )

    @property
    def best_bid(self) -> float:
        if not self.bids:
            raise ValueError("empty bid side")
        return self.bids[0][0]

    @property
    def best_ask(self) -> float:
        if not self.asks:
            raise ValueError("empty ask side")
        return self.asks[0][0]

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2.0

    def vwap(self, side: Side, base_amount: float) -> tuple[float, float, float]:
        """Return (VWAP, quote notional, slippage bps) for exact base amount.

        BUY consumes asks. SELL consumes bids. Raises ValueError on insufficient depth.
        """
        if base_amount <= 0 or not math.isfinite(base_amount):
            raise ValueError("base_amount must be positive")
        levels = self.asks if side == Side.BUY else self.bids
        if not levels:
            raise ValueError("empty order book side")
        remaining = base_amount
        quote = 0.0
        for price, available in levels:
            take = min(remaining, available)
            quote += take * price
            remaining -= take
            if remaining <= 1e-15:
                break
        if remaining > max(base_amount * 1e-9, 1e-12):
            raise ValueError("insufficient order-book depth")
        vwap = quote / base_amount
        best = levels[0][0]
        if side == Side.BUY:
            slippage_bps = max(0.0, (vwap / best - 1.0) * 10_000.0)
        else:
            slippage_bps = max(0.0, (1.0 - vwap / best) * 10_000.0)
        return vwap, quote, slippage_bps

    def available_base(self, side: Side, limit_levels: int | None = None) -> float:
        levels = self.asks if side == Side.BUY else self.bids
        if limit_levels is not None:
            levels = levels[:limit_levels]
        return sum(amount for _, amount in levels)


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    quote: FundingQuote
    order_book: OrderBook

    @property
    def exchange_id(self) -> str:
        return self.quote.exchange_id

    @property
    def symbol(self) -> str:
        return self.quote.symbol

    @property
    def asset(self) -> str:
        return self.quote.asset


@dataclass(frozen=True, slots=True)
class Candidate:
    asset: str
    long_exchange: str
    long_symbol: str
    short_exchange: str
    short_symbol: str
    base_amount: float
    matched_notional_usdt: float
    long_entry_price: float
    short_entry_price: float
    current_spread_bps_8h: float
    predicted_spread_bps_8h: float
    gross_funding_bps: float
    entry_basis_bps: float
    fee_bps: float
    slippage_bps: float
    safety_bps: float
    expected_net_bps: float
    evaluation_hold_hours: float
    long_open_interest_usdt: float | None
    short_open_interest_usdt: float | None
    long_depth_multiple: float
    short_depth_multiple: float
    long_funding_timestamp_ms: int | None
    short_funding_timestamp_ms: int | None
    long_interval_hours: float
    short_interval_hours: float
    observed_at_ms: int
    maker_exchange: str
    maker_side: Side
    maker_reference_price: float
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def candidate_id(self) -> str:
        raw = (
            f"{self.asset}|{self.long_exchange}|{self.long_symbol}|"
            f"{self.short_exchange}|{self.short_symbol}|{self.observed_at_ms}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["maker_side"] = self.maker_side.value
        payload["candidate_id"] = self.candidate_id
        payload["current_simple_apr_percent"] = self.current_spread_bps_8h * 3.0 * 365.0 / 100.0
        payload["predicted_simple_apr_percent"] = self.predicted_spread_bps_8h * 3.0 * 365.0 / 100.0
        payload["expected_net_simple_apr_percent"] = (
            self.expected_net_bps * (24.0 / self.evaluation_hold_hours) * 365.0 / 100.0
        )
        return payload


@dataclass(frozen=True, slots=True)
class Rejection:
    asset: str
    long_exchange: str
    short_exchange: str
    reason: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ScanResult:
    observed_at_ms: int
    candidates: tuple[Candidate, ...]
    rejections: tuple[Rejection, ...]
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "observed_at_ms": self.observed_at_ms,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "rejections": [rejection.to_dict() for rejection in self.rejections],
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class OrderState:
    order_id: str
    symbol: str
    side: Side
    status: str
    requested_base: float
    filled_base: float
    remaining_base: float
    average_price: float | None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def done(self) -> bool:
        return self.status.lower() in {"closed", "canceled", "cancelled", "rejected", "expired"}


@dataclass(slots=True)
class PositionLeg:
    exchange_id: str
    symbol: str
    side: Side
    base_amount: float
    entry_price: float
    order_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["side"] = self.side.value
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PositionLeg":
        return cls(
            exchange_id=str(payload["exchange_id"]),
            symbol=str(payload["symbol"]),
            side=Side(payload["side"]),
            base_amount=float(payload["base_amount"]),
            entry_price=float(payload["entry_price"]),
            order_ids=[str(item) for item in payload.get("order_ids", [])],
        )


@dataclass(slots=True)
class PositionState:
    position_id: str
    candidate_id: str
    asset: str
    status: PositionStatus
    long_leg: PositionLeg
    short_leg: PositionLeg
    opened_at_ms: int
    updated_at_ms: int
    expected_net_bps_at_open: float
    realized_funding_usdt: float = 0.0
    realized_costs_usdt: float = 0.0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "position_id": self.position_id,
            "candidate_id": self.candidate_id,
            "asset": self.asset,
            "status": self.status.value,
            "long_leg": self.long_leg.to_dict(),
            "short_leg": self.short_leg.to_dict(),
            "opened_at_ms": self.opened_at_ms,
            "updated_at_ms": self.updated_at_ms,
            "expected_net_bps_at_open": self.expected_net_bps_at_open,
            "realized_funding_usdt": self.realized_funding_usdt,
            "realized_costs_usdt": self.realized_costs_usdt,
            "error": self.error,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PositionState":
        return cls(
            position_id=str(payload["position_id"]),
            candidate_id=str(payload["candidate_id"]),
            asset=str(payload["asset"]),
            status=PositionStatus(payload["status"]),
            long_leg=PositionLeg.from_dict(payload["long_leg"]),
            short_leg=PositionLeg.from_dict(payload["short_leg"]),
            opened_at_ms=int(payload["opened_at_ms"]),
            updated_at_ms=int(payload["updated_at_ms"]),
            expected_net_bps_at_open=float(payload["expected_net_bps_at_open"]),
            realized_funding_usdt=float(payload.get("realized_funding_usdt", 0.0)),
            realized_costs_usdt=float(payload.get("realized_costs_usdt", 0.0)),
            error=payload.get("error"),
            metadata=dict(payload.get("metadata", {})),
        )

    @classmethod
    def from_json(cls, raw: str) -> "PositionState":
        return cls.from_dict(json.loads(raw))
