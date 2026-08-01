"""Public OKX market-data loader for DS-40/180 paper trading."""

from __future__ import annotations

import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ._ds40180_common import (
    ASSETS,
    HISTORY_LIMIT,
    INSTRUMENTS,
    MAX_CANDLE_PAGES,
    MAX_FUNDING_PAGES,
    MINIMUM_COMMON_DAYS,
    OKX_API_BASE,
    OKX_BAR,
    _timestamp_ms,
)

_REQUEST_LOCK = threading.Lock()
_NEXT_REQUEST_AT = 0.0
# The strictest public endpoint used here is budgeted below five requests/sec.
# A process-wide slot protects all four loader threads from bursting the same IP.
_MIN_REQUEST_INTERVAL_SECONDS = 0.22


def _wait_for_request_slot() -> None:
    global _NEXT_REQUEST_AT
    with _REQUEST_LOCK:
        now = time.monotonic()
        delay = max(0.0, _NEXT_REQUEST_AT - now)
        if delay:
            time.sleep(delay)
        _NEXT_REQUEST_AT = time.monotonic() + _MIN_REQUEST_INTERVAL_SECONDS


def _fetch_okx(
    path: str,
    params: dict[str, object],
    *,
    timeout_seconds: float = 12.0,
) -> list[Any]:
    query = urlencode({key: str(value) for key, value in params.items() if value is not None})
    request = Request(
        f"{OKX_API_BASE}{path}?{query}",
        headers={"Accept": "application/json", "User-Agent": "FIN-DS40180-PAPER/1.0"},
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            _wait_for_request_slot()
            with urlopen(request, timeout=timeout_seconds) as response:
                payload = json.load(response)
            if not isinstance(payload, dict):
                raise TypeError("OKX returned a non-object payload")
            if str(payload.get("code")) != "0":
                raise RuntimeError(
                    f"OKX {path} failed with code={payload.get('code')}: {payload.get('msg')}"
                )
            data = payload.get("data")
            if not isinstance(data, list):
                raise TypeError(f"OKX {path} returned a non-list data field")
            return data
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            last_error = error
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"OKX request failed for {path}: {last_error}")


def _parse_candle(row: Any, instrument_id: str) -> dict[str, Any]:
    if not isinstance(row, list) or len(row) < 9:
        raise ValueError(f"OKX returned an invalid candle for {instrument_id}")
    candle = {
        "openTime": int(row[0]),
        "open": float(row[1]),
        "high": float(row[2]),
        "low": float(row[3]),
        "close": float(row[4]),
        "volume": float(row[5]),
        "quoteVolume": (
            float(row[7])
            if row[7] not in (None, "")
            else float(row[6]) * float(row[4])
        ),
        "confirmed": str(row[8]) == "1",
    }
    if candle["openTime"] <= 0 or not all(
        math.isfinite(float(candle[field]))
        for field in ("open", "high", "low", "close", "volume", "quoteVolume")
    ):
        raise ValueError(f"OKX returned a non-finite candle for {instrument_id}")
    if min(candle["open"], candle["high"], candle["low"], candle["close"]) <= 0:
        raise ValueError(f"OKX returned a non-positive candle for {instrument_id}")
    return candle


def _fetch_candles(instrument_id: str) -> list[dict[str, Any]]:
    by_timestamp: dict[int, dict[str, Any]] = {}
    after: str | None = None
    previous_oldest: int | None = None
    for _page in range(MAX_CANDLE_PAGES):
        rows = _fetch_okx(
            "/api/v5/market/history-candles",
            {
                "instId": instrument_id,
                "bar": OKX_BAR,
                "limit": 100,
                "after": after,
            },
        )
        if not rows:
            break
        parsed = [_parse_candle(row, instrument_id) for row in rows]
        for candle in parsed:
            if candle["confirmed"]:
                by_timestamp[candle["openTime"]] = candle
        oldest = min(candle["openTime"] for candle in parsed)
        if previous_oldest is not None and oldest >= previous_oldest:
            break
        previous_oldest = oldest
        after = str(oldest)
        if len(by_timestamp) >= HISTORY_LIMIT:
            break
    candles = sorted(by_timestamp.values(), key=lambda item: item["openTime"])
    if len(candles) < MINIMUM_COMMON_DAYS:
        raise ValueError(
            f"{instrument_id} returned {len(candles)} closed daily candles; "
            f"at least {MINIMUM_COMMON_DAYS} are required"
        )
    return candles[-HISTORY_LIMIT:]


def _fetch_mark(instrument_id: str, fallback: float) -> tuple[float, int]:
    rows = _fetch_okx(
        "/api/v5/public/mark-price",
        {"instType": "SWAP", "instId": instrument_id},
    )
    if not rows or not isinstance(rows[0], dict):
        return fallback, int(time.time() * 1000)
    price = float(rows[0].get("markPx") or fallback)
    observed_at_ms = int(rows[0].get("ts") or time.time() * 1000)
    if not math.isfinite(price) or price <= 0:
        return fallback, observed_at_ms
    return price, observed_at_ms


def _funding_rate(record: dict[str, Any]) -> float:
    realized = record.get("realizedRate")
    raw = realized if realized not in (None, "") else record.get("fundingRate")
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("OKX returned a non-finite funding rate")
    return value


def _fetch_funding_history(
    instrument_id: str, earliest_timestamp_ms: int
) -> list[dict[str, Any]]:
    by_timestamp: dict[int, dict[str, Any]] = {}
    after: str | None = None
    previous_oldest: int | None = None
    for _page in range(MAX_FUNDING_PAGES):
        rows = _fetch_okx(
            "/api/v5/public/funding-rate-history",
            {"instId": instrument_id, "limit": 400, "after": after},
        )
        if not rows:
            break
        timestamps: list[int] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"OKX returned invalid funding data for {instrument_id}")
            timestamp = int(row.get("fundingTime") or 0)
            rate = _funding_rate(row)
            if timestamp <= 0:
                raise ValueError(f"OKX returned invalid funding time for {instrument_id}")
            timestamps.append(timestamp)
            by_timestamp[timestamp] = {
                "fundingTime": timestamp,
                "rate": rate,
                "formulaType": row.get("formulaType"),
                "method": row.get("method"),
            }
        oldest = min(timestamps)
        if oldest <= earliest_timestamp_ms:
            break
        if previous_oldest is not None and oldest >= previous_oldest:
            break
        previous_oldest = oldest
        after = str(oldest)
    return sorted(by_timestamp.values(), key=lambda item: item["fundingTime"])


def _load_asset_bundle(asset: str, reset_date: str) -> dict[str, Any]:
    instrument_id = INSTRUMENTS[asset]
    candles = _fetch_candles(instrument_id)
    warnings: list[str] = []
    try:
        mark_price, mark_time_ms = _fetch_mark(instrument_id, candles[-1]["close"])
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        mark_price = candles[-1]["close"]
        mark_time_ms = int(time.time() * 1000)
        warnings.append(f"mark fallback: {type(error).__name__}: {error}")
    try:
        funding = _fetch_funding_history(instrument_id, _timestamp_ms(reset_date))
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        funding = []
        warnings.append(f"funding fallback: {type(error).__name__}: {error}")
    bars = {
        datetime.fromtimestamp(candle["openTime"] / 1000, UTC).date().isoformat(): candle
        for candle in candles
    }
    return {
        "asset": asset,
        "instrumentId": instrument_id,
        "bars": bars,
        "liveMark": mark_price,
        "markTimeMs": mark_time_ms,
        "funding": funding,
        "warnings": warnings,
    }


def load_market_data(
    *, reset_date: str
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    histories: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(_load_asset_bundle, asset, reset_date): asset for asset in ASSETS
        }
        for future in as_completed(futures):
            asset = futures[future]
            try:
                histories.append(future.result())
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                failures.append(
                    {
                        "asset": asset,
                        "instrumentId": INSTRUMENTS[asset],
                        "reason": f"{type(error).__name__}: {error}",
                    }
                )
    histories.sort(key=lambda item: ASSETS.index(item["asset"]))
    failures.sort(key=lambda item: ASSETS.index(item["asset"]))
    return histories, failures
