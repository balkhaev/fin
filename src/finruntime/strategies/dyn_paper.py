"""Exact paper port of fin2 DYN-IV113 for the consolidated FIN runtime."""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HISTORY_LIMIT = 600
MINIMUM_ASSETS = 6
TARGET_VOLATILITY = 0.7
MAXIMUM_GROSS = 2.5
ASSET_CAP = 1.0
EXECUTION_COST = 0.003
FINANCING_ANNUAL = 0.25
RETURN_CAP = 0.3
EPSILON = 1e-8
MATERIAL_DELTA = 0.005
SNAPSHOT_DATE = "2026-07-26"
STRATEGY_ID = "DYN-IV113"
API_BASE = "https://data-api.binance.vision/api/v3/klines"
MARKET_SYMBOLS = (
    "ADAUSDT",
    "ATOMUSDT",
    "BNBUSDT",
    "BTCUSDT",
    "BTTUSDT",
    "DASHUSDT",
    "EOSUSDT",
    "ETCUSDT",
    "ETHUSDT",
    "LINKUSDT",
    "LTCUSDT",
    "NEOUSDT",
    "TRXUSDT",
    "XLMUSDT",
    "XMRUSDT",
    "XRPUSDT",
    "ZECUSDT",
)
ASSETS = tuple(symbol.removesuffix("USDT") for symbol in MARKET_SYMBOLS)


@dataclass(frozen=True, slots=True)
class DynProfile:
    name: str
    strategy_id: str
    label: str
    target_volatility: float
    maximum_gross: float
    asset_cap: float
    target_deadband: float = 0.0
    mode: str = "shadow"


DYN_PROFILES = {
    "baseline": DynProfile(
        name="baseline",
        strategy_id=STRATEGY_ID,
        label="DYN-IV113",
        target_volatility=TARGET_VOLATILITY,
        maximum_gross=MAXIMUM_GROSS,
        asset_cap=ASSET_CAP,
        mode="paper",
    ),
    "risk50": DynProfile(
        name="risk50",
        strategy_id="DYN-IV113-RISK50",
        label="DYN-IV113 · target vol 50%",
        target_volatility=0.50,
        maximum_gross=MAXIMUM_GROSS,
        asset_cap=ASSET_CAP,
    ),
    "band2": DynProfile(
        name="band2",
        strategy_id="DYN-IV113-BAND2",
        label="DYN-IV113 · target deadband 2%",
        target_volatility=TARGET_VOLATILITY,
        maximum_gross=MAXIMUM_GROSS,
        asset_cap=ASSET_CAP,
        target_deadband=0.02,
    ),
}


def get_profile(profile: str | DynProfile = "baseline") -> DynProfile:
    if isinstance(profile, DynProfile):
        return profile
    try:
        return DYN_PROFILES[profile]
    except KeyError as error:
        raise ValueError(f"unknown DYN profile: {profile}") from error


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _finite_window(
    values: list[float | None], end_index: int, window: int
) -> list[float]:
    start = max(0, end_index - window + 1)
    return [
        value
        for value in values[start : end_index + 1]
        if value is not None and math.isfinite(value)
    ]


def _rolling_mean(
    values: list[float | None], window: int, minimum_periods: int
) -> list[float | None]:
    output: list[float | None] = []
    for index in range(len(values)):
        sample = _finite_window(values, index, window)
        output.append(
            statistics.fmean(sample) if len(sample) >= minimum_periods else None
        )
    return output


def _rolling_median(
    values: list[float | None], window: int, minimum_periods: int
) -> list[float | None]:
    output: list[float | None] = []
    for index in range(len(values)):
        sample = _finite_window(values, index, window)
        output.append(
            statistics.median(sample) if len(sample) >= minimum_periods else None
        )
    return output


def _sample_standard_deviation(values: list[float]) -> float | None:
    return statistics.stdev(values) if len(values) >= 2 else None


def _rolling_standard_deviation(
    values: list[float | None], window: int, minimum_periods: int
) -> list[float | None]:
    output: list[float | None] = []
    for index in range(len(values)):
        sample = _finite_window(values, index, window)
        output.append(
            _sample_standard_deviation(sample)
            if len(sample) >= minimum_periods
            else None
        )
    return output


def _exponential_moving_average(
    values: list[float | None], span: int
) -> list[float | None]:
    alpha = 2 / (span + 1)
    previous: float | None = None
    output: list[float | None] = []
    for value in values:
        if value is not None:
            previous = (
                value if previous is None else alpha * value + (1 - alpha) * previous
            )
        output.append(previous)
    return output


def _percentile_ranks(values: list[float | None]) -> list[float | None]:
    finite = sorted(
        ((index, value) for index, value in enumerate(values) if value is not None),
        key=lambda item: (item[1], item[0]),
    )
    output: list[float | None] = [None] * len(values)
    cursor = 0
    while cursor < len(finite):
        end = cursor + 1
        while end < len(finite) and finite[end][1] == finite[cursor][1]:
            end += 1
        average_rank = (cursor + 1 + end) / 2
        for ranked_index in range(cursor, end):
            output[finite[ranked_index][0]] = average_rank / len(finite)
        cursor = end
    return output


def _zero_row(length: int) -> list[float]:
    return [0.0] * length


def _row_gross(row: list[float]) -> float:
    return sum(abs(value) for value in row)


def _rank_weights(
    scores: list[float | None],
    eligible: list[bool],
    inverse_volatility: list[float | None],
    top_k: int,
) -> list[float]:
    selected = sorted(
        (
            (index, score)
            for index, score in enumerate(scores)
            if eligible[index] and score is not None
        ),
        key=lambda item: (-item[1], item[0]),
    )[:top_k]
    output = _zero_row(len(scores))
    raw = [
        value if value is not None and math.isfinite(value) and value > 0 else 0.0
        for index, _score in selected
        for value in [inverse_volatility[index]]
    ]
    total = sum(raw)
    if total <= 0:
        return output
    for (index, _score), value in zip(selected, raw, strict=True):
        output[index] = value / total
    return output


def _weekly_hold(
    weights: list[list[float]], dates: list[str], target_weekday: int
) -> list[list[float]]:
    held = _zero_row(len(weights[0]) if weights else 0)
    current_week: tuple[int, int] | None = None
    updated_this_week = False
    output: list[list[float]] = []
    for date_text, row in zip(dates, weights, strict=True):
        parsed = date.fromisoformat(date_text)
        week = parsed.isocalendar()[:2]
        if week != current_week:
            current_week = week
            updated_this_week = False
        if not updated_this_week and parsed.weekday() >= target_weekday:
            held = list(row)
            updated_this_week = True
        output.append(list(held))
    return output


def _multiply_signal(
    signal: list[list[float]], regime: list[int], eligible_counts: list[int]
) -> list[list[float]]:
    return [
        [value * regime[index] for value in row]
        if eligible_counts[index] >= MINIMUM_ASSETS
        else _zero_row(len(row))
        for index, row in enumerate(signal)
    ]


def _family_returns(
    signal: list[list[float]], returns: list[list[float]]
) -> list[float]:
    asset_count = len(signal[0]) if signal else 0
    previous_target = _zero_row(asset_count)
    previous_previous_target = _zero_row(asset_count)
    output: list[float] = []
    for index, row in enumerate(signal):
        target = list(signal[index - 1]) if index > 0 else _zero_row(asset_count)
        daily_returns = returns[index]
        gross = sum(
            weight * _clamp(daily_returns[asset_index], -RETURN_CAP, RETURN_CAP)
            for asset_index, weight in enumerate(previous_previous_target)
        )
        turnover = sum(
            abs(weight - previous_target[asset_index])
            for asset_index, weight in enumerate(target)
        )
        output.append(gross - EXECUTION_COST * turnover)
        previous_previous_target = previous_target
        previous_target = target
    return output


def _family_weight(
    flow_returns: list[float], absolute_returns: list[float], dates: list[str]
) -> list[float]:
    held_weight = 0.5
    output: list[float] = []
    for index, date_text in enumerate(dates):
        flow_sample = flow_returns[max(0, index - 90) : index]
        absolute_sample = absolute_returns[max(0, index - 90) : index]
        flow_vol = (
            _sample_standard_deviation(flow_sample) if len(flow_sample) >= 45 else None
        )
        absolute_vol = (
            _sample_standard_deviation(absolute_sample)
            if len(absolute_sample) >= 45
            else None
        )
        raw_weight = 0.5
        if flow_vol and absolute_vol and flow_vol > 0 and absolute_vol > 0:
            flow_inverse = 1 / flow_vol
            absolute_inverse = 1 / absolute_vol
            raw_weight = _clamp(
                flow_inverse / (flow_inverse + absolute_inverse), 0.2, 0.8
            )
        if date.fromisoformat(date_text).weekday() == 0:
            held_weight = raw_weight
        output.append(held_weight)
    return output


def _fetch_json(url: str, *, timeout_seconds: float = 8.0) -> Any:
    request = Request(
        url, headers={"Accept": "application/json", "User-Agent": "FIN-DYN-IV113/1.0"}
    )
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return json.load(response)
        except (OSError, ValueError) as error:
            last_error = error
            if attempt == 0:
                time.sleep(0.2)
    raise RuntimeError(f"Binance request failed: {last_error}")


def _fetch_asset_history(symbol: str) -> dict[str, Any]:
    query = urlencode({"symbol": symbol, "interval": "1d", "limit": HISTORY_LIMIT})
    rows = _fetch_json(f"{API_BASE}?{query}")
    if not isinstance(rows, list):
        raise TypeError(f"Binance returned an invalid kline payload for {symbol}")
    now_ms = int(time.time() * 1000)
    candles: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 9:
            raise ValueError(f"Binance returned an invalid kline row for {symbol}")
        candle = {
            "openTime": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "closeTime": int(row[6]),
            "quoteVolume": float(row[7]),
            "closed": int(row[6]) < now_ms,
        }
        if not all(
            math.isfinite(candle[field])
            for field in ("open", "high", "low", "close", "quoteVolume")
        ):
            raise ValueError(f"Binance returned non-finite candles for {symbol}")
        candles.append(candle)
    if not candles:
        raise ValueError(f"No daily candles were returned for {symbol}")
    bars = {
        datetime.fromtimestamp(candle["openTime"] / 1000, UTC)
        .date()
        .isoformat(): candle
        for candle in candles
        if candle["closed"]
    }
    if not bars:
        raise ValueError(f"No closed daily candles were returned for {symbol}")
    return {
        "asset": symbol.removesuffix("USDT"),
        "symbol": symbol,
        "bars": bars,
        "liveCandle": candles[-1],
    }


def load_asset_histories() -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    histories: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(_fetch_asset_history, symbol): symbol
            for symbol in MARKET_SYMBOLS
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                histories.append(future.result())
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                failures.append({"symbol": symbol, "reason": str(error)})
    histories.sort(key=lambda item: ASSETS.index(item["asset"]))
    failures.sort(key=lambda item: MARKET_SYMBOLS.index(item["symbol"]))
    return histories, failures


def build_engine(
    histories: list[dict[str, Any]],
    failed_symbols: list[dict[str, str]],
    *,
    target_volatility: float = TARGET_VOLATILITY,
    maximum_gross: float = MAXIMUM_GROSS,
    asset_cap: float = ASSET_CAP,
) -> dict[str, Any]:
    history_by_asset = {history["asset"]: history for history in histories}
    assets = [asset for asset in ASSETS if asset in history_by_asset]
    dates = sorted(
        {date_text for history in histories for date_text in history["bars"]}
    )
    asset_count = len(assets)
    date_count = len(dates)
    closes: list[list[float | None]] = [[None] * asset_count for _ in dates]
    highs: list[list[float | None]] = [[None] * asset_count for _ in dates]
    lows: list[list[float | None]] = [[None] * asset_count for _ in dates]
    volumes: list[list[float | None]] = [[None] * asset_count for _ in dates]
    active = [[False] * asset_count for _ in dates]
    for asset_index, asset in enumerate(assets):
        bars = history_by_asset[asset]["bars"]
        for date_index, date_text in enumerate(dates):
            bar = bars.get(date_text)
            if bar is None:
                continue
            closes[date_index][asset_index] = bar["close"]
            highs[date_index][asset_index] = bar["high"]
            lows[date_index][asset_index] = bar["low"]
            volumes[date_index][asset_index] = bar["quoteVolume"]
            active[date_index][asset_index] = True

    returns = [_zero_row(asset_count) for _ in dates]
    ages = [_zero_row(asset_count) for _ in dates]
    for asset_index in range(asset_count):
        previous_close: float | None = None
        age = 0
        for date_index in range(date_count):
            close = closes[date_index][asset_index]
            if close is not None:
                age += 1
                returns[date_index][asset_index] = (
                    0.0 if previous_close is None else close / previous_close - 1
                )
                previous_close = close
            ages[date_index][asset_index] = float(age)

    liquidity_by_asset: list[list[float | None]] = []
    volatility_by_asset: list[list[float | None]] = []
    inverse_volatility: list[list[float | None]] = [[None] * asset_count for _ in dates]
    for asset_index in range(asset_count):
        volume_series = [row[asset_index] for row in volumes]
        liquidity_by_asset.append(_rolling_median(volume_series, 30, 15))
        active_returns = [
            returns[index][asset_index] if active[index][asset_index] else None
            for index in range(date_count)
        ]
        volatility = [
            None if value is None else value * math.sqrt(365)
            for value in _rolling_standard_deviation(active_returns, 30, 15)
        ]
        volatility_by_asset.append(volatility)
        for date_index in range(1, date_count):
            previous_volatility = volatility[date_index - 1]
            if previous_volatility is not None:
                inverse_volatility[date_index][asset_index] = 1 / _clamp(
                    previous_volatility, 0.15, 3
                )

    try:
        btc_index = assets.index("BTC")
    except ValueError as error:
        raise ValueError("BTC daily history is required for DYN-IV113") from error
    btc = [row[btc_index] for row in closes]
    ema20 = _exponential_moving_average(btc, 20)
    ema50 = _exponential_moving_average(btc, 50)
    ema100 = _exponential_moving_average(btc, 100)
    ema200 = _exponential_moving_average(btc, 200)
    sma150 = _rolling_mean(btc, 150, 100)
    btc_filters: list[dict[str, bool]] = []
    for index, close in enumerate(btc):
        btc_filters.append(
            {
                "f1": bool(
                    close is not None
                    and ema100[index] is not None
                    and ema20[index] is not None
                    and close > ema100[index]
                    and ema20[index] > ema100[index]
                ),
                "f2": bool(
                    close is not None
                    and sma150[index] is not None
                    and close > sma150[index]
                ),
                "f3": bool(
                    ema50[index] is not None
                    and ema200[index] is not None
                    and ema50[index] > ema200[index]
                ),
            }
        )
    consensus = [sum(map(int, filters.values())) for filters in btc_filters]
    consensus_regime = [int(value >= 1) for value in consensus]
    sma_regime = [int(filters["f2"]) for filters in btc_filters]

    eligibility: list[list[bool]] = []
    eligible_counts: list[int] = []
    eligible_assets: list[list[str]] = []
    for date_index in range(date_count):
        candidates: list[tuple[int, float]] = []
        for asset_index in range(asset_count):
            liquidity = liquidity_by_asset[asset_index][date_index]
            if (
                active[date_index][asset_index]
                and ages[date_index][asset_index] >= 180
                and liquidity is not None
                and liquidity >= 1_000_000
            ):
                candidates.append((asset_index, liquidity))
        candidates.sort(key=lambda item: (-item[1], item[0]))
        selected = {asset_index for asset_index, _liquidity in candidates[:8]}
        eligibility.append([index in selected for index in range(asset_count)])
        eligible_counts.append(len(selected))
        eligible_assets.append([assets[index] for index, _value in candidates[:8]])

    flow_values_by_asset: list[list[float | None]] = []
    momentum84_by_asset: list[list[float | None]] = []
    for asset_index in range(asset_count):
        flow_daily: list[float | None] = []
        momentum: list[float | None] = []
        for date_index in range(date_count):
            close = closes[date_index][asset_index]
            high = highs[date_index][asset_index]
            low = lows[date_index][asset_index]
            volume = volumes[date_index][asset_index]
            if None in (close, high, low, volume) or high == low:
                flow_daily.append(None)
            else:
                close_location = _clamp(((close - low) / (high - low) - 0.5) * 2, -1, 1)
                flow_daily.append(close_location * math.log1p(volume))
            if date_index < 84:
                momentum.append(None)
                continue
            previous = closes[date_index - 84][asset_index]
            previous_volatility = volatility_by_asset[asset_index][date_index - 1]
            if close is None or previous is None or previous_volatility is None:
                momentum.append(None)
            else:
                momentum.append(
                    (close / previous - 1) / _clamp(previous_volatility, 0.15, 3)
                )
        flow_values_by_asset.append(_rolling_mean(flow_daily, 42, 34))
        momentum84_by_asset.append(momentum)

    flow_raw: list[list[float]] = []
    for date_index in range(date_count):
        flow_ranks = _percentile_ranks(
            [flow_values_by_asset[index][date_index] for index in range(asset_count)]
        )
        momentum_ranks = _percentile_ranks(
            [momentum84_by_asset[index][date_index] for index in range(asset_count)]
        )
        scores = [
            None if flow is None or momentum is None else flow + momentum
            for flow, momentum in zip(flow_ranks, momentum_ranks, strict=True)
        ]
        flow_raw.append(
            _rank_weights(
                scores,
                eligibility[date_index],
                inverse_volatility[date_index],
                2,
            )
        )
    flow_signal = _multiply_signal(
        _weekly_hold(flow_raw, dates, 0), consensus_regime, eligible_counts
    )

    absolute_specifications = (
        (168, sma_regime, 3),
        (126, sma_regime, 0),
        (126, consensus_regime, 0),
    )
    absolute_pieces: list[list[list[float]]] = []
    for lookback, regime, target_weekday in absolute_specifications:
        raw: list[list[float]] = []
        for date_index in range(date_count):
            scores: list[float | None] = []
            for asset_index in range(asset_count):
                if date_index < lookback:
                    scores.append(None)
                    continue
                current = closes[date_index][asset_index]
                previous = closes[date_index - lookback][asset_index]
                score = (
                    None
                    if current is None or previous is None
                    else current / previous - 1
                )
                scores.append(score if score is not None and score > 0 else None)
            raw.append(
                _rank_weights(
                    scores,
                    eligibility[date_index],
                    inverse_volatility[date_index],
                    3,
                )
            )
        absolute_pieces.append(
            _multiply_signal(
                _weekly_hold(raw, dates, target_weekday), regime, eligible_counts
            )
        )
    absolute_signal = [
        [
            sum(piece[date_index][asset_index] for piece in absolute_pieces)
            / len(absolute_pieces)
            for asset_index in range(asset_count)
        ]
        for date_index in range(date_count)
    ]

    flow_returns = _family_returns(flow_signal, returns)
    absolute_returns = _family_returns(absolute_signal, returns)
    flow_weight = _family_weight(flow_returns, absolute_returns, dates)
    absolute_weight = [1 - weight for weight in flow_weight]
    composite_signal = [
        [
            flow_signal[date_index][asset_index] * flow_weight[date_index]
            + absolute_signal[date_index][asset_index] * absolute_weight[date_index]
            for asset_index in range(asset_count)
        ]
        for date_index in range(date_count)
    ]

    base_targets: list[list[float]] = []
    base_net_for_vol: list[float] = []
    previous_base_target = _zero_row(asset_count)
    previous_previous_base_target = _zero_row(asset_count)
    for date_index in range(date_count):
        base_target = (
            list(composite_signal[date_index - 1])
            if date_index > 0
            else _zero_row(asset_count)
        )
        daily_returns = returns[date_index]
        gross = sum(
            weight * _clamp(daily_returns[asset_index], -RETURN_CAP, RETURN_CAP)
            for asset_index, weight in enumerate(previous_previous_base_target)
        )
        turnover = sum(
            abs(weight - previous_base_target[asset_index])
            for asset_index, weight in enumerate(base_target)
        )
        base_targets.append(base_target)
        base_net_for_vol.append(gross - 0.0015 * turnover)
        previous_previous_base_target = previous_base_target
        previous_base_target = base_target
    realized_volatility = [
        None if value is None else value * math.sqrt(365)
        for value in _rolling_standard_deviation(base_net_for_vol, 90, 45)
    ]
    leverage: list[float] = []
    for date_index, base_target in enumerate(base_targets):
        previous_volatility = (
            realized_volatility[date_index - 1] if date_index > 0 else None
        )
        leverage.append(
            _clamp(target_volatility / previous_volatility, 0, maximum_gross)
            if previous_volatility
            and previous_volatility > 0
            and _row_gross(base_target) > 0
            else 0.0
        )
    target = [
        [_clamp(weight * leverage[index], -asset_cap, asset_cap) for weight in row]
        for index, row in enumerate(base_targets)
    ]
    return {
        "absWeight": absolute_weight,
        "assets": assets,
        "btcFilters": btc_filters,
        "closes": closes,
        "consensus": consensus,
        "dates": dates,
        "eligibleAssets": eligible_assets,
        "failedSymbols": failed_symbols,
        "flowWeight": flow_weight,
        "leverage": leverage,
        "returns": returns,
        "target": target,
    }


def _apply_target_deadband(
    targets: list[list[float]], threshold: float
) -> list[list[float]]:
    if threshold <= 0 or not targets:
        return [list(row) for row in targets]
    held = _zero_row(len(targets[0]))
    output: list[list[float]] = []
    for row in targets:
        next_row: list[float] = []
        for old, new in zip(held, row, strict=True):
            sign_flip = old * new < -EPSILON
            exit_required = abs(old) > EPSILON and abs(new) <= EPSILON
            if sign_flip or exit_required or abs(new - old) >= threshold:
                next_row.append(float(new))
            else:
                next_row.append(float(old))
        held = next_row
        output.append(list(held))
    return output


def build_profile_engine(
    histories: list[dict[str, Any]],
    failed_symbols: list[dict[str, str]],
    profile: str | DynProfile = "baseline",
) -> dict[str, Any]:
    config = get_profile(profile)
    engine = build_engine(
        histories,
        failed_symbols,
        target_volatility=config.target_volatility,
        maximum_gross=config.maximum_gross,
        asset_cap=config.asset_cap,
    )
    engine["target"] = _apply_target_deadband(
        engine["target"], config.target_deadband
    )
    engine["profile"] = {
        "name": config.name,
        "strategyId": config.strategy_id,
        "label": config.label,
        "mode": config.mode,
        "targetVolatility": config.target_volatility,
        "maximumGross": config.maximum_gross,
        "assetCap": config.asset_cap,
        "targetDeadband": config.target_deadband,
    }
    return engine


def _elapsed_days(previous: str, current: str) -> int:
    return max(1, (date.fromisoformat(current) - date.fromisoformat(previous)).days)


def _rebalance_tracker(
    *,
    desired_quantity: float,
    execution_price: float,
    opened_on: str,
    previous: dict[str, Any] | None,
    trading_costs_usd: float,
) -> dict[str, Any] | None:
    if abs(desired_quantity) <= 1e-10:
        return None
    if previous is None or abs(previous["quantity"]) <= 1e-10:
        return {
            "averageEntryPrice": execution_price,
            "financingCostsUsd": 0.0,
            "openedOn": opened_on,
            "quantity": desired_quantity,
            "realizedPnlUsd": 0.0,
            "tradingCostsUsd": trading_costs_usd,
        }
    previous_direction = math.copysign(1, previous["quantity"])
    desired_direction = math.copysign(1, desired_quantity)
    if previous_direction != desired_direction:
        opening_share = abs(desired_quantity) / (
            abs(previous["quantity"]) + abs(desired_quantity)
        )
        return {
            "averageEntryPrice": execution_price,
            "financingCostsUsd": 0.0,
            "openedOn": opened_on,
            "quantity": desired_quantity,
            "realizedPnlUsd": 0.0,
            "tradingCostsUsd": trading_costs_usd * opening_share,
        }
    previous_size = abs(previous["quantity"])
    desired_size = abs(desired_quantity)
    average_entry_price = previous["averageEntryPrice"]
    realized_pnl_usd = previous["realizedPnlUsd"]
    if desired_size > previous_size:
        added_size = desired_size - previous_size
        average_entry_price = (
            previous_size * previous["averageEntryPrice"] + added_size * execution_price
        ) / desired_size
    else:
        closed_size = previous_size - desired_size
        realized_pnl_usd += (
            closed_size
            * (execution_price - previous["averageEntryPrice"])
            * previous_direction
        )
    return {
        **previous,
        "averageEntryPrice": average_entry_price,
        "quantity": desired_quantity,
        "realizedPnlUsd": realized_pnl_usd,
        "tradingCostsUsd": previous["tradingCostsUsd"] + trading_costs_usd,
    }


def paper_continuation(
    engine: dict[str, Any], *, reset_date: str, initial_nav_usd: float
) -> dict[str, Any]:
    snapshot_indexes = [
        index
        for index, date_text in enumerate(engine["dates"])
        if date_text <= reset_date
    ]
    if not snapshot_indexes:
        raise ValueError("The market window does not include the DYN paper reset date")
    snapshot_index = snapshot_indexes[-1]
    asset_count = len(engine["assets"])
    nav = initial_nav_usd
    previous_target = _zero_row(asset_count)
    previous_date = reset_date
    daily: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []
    trackers: dict[str, dict[str, Any]] = {}
    for date_index in range(snapshot_index + 1, len(engine["dates"])):
        current_date = engine["dates"][date_index]
        target = engine["target"][date_index]
        daily_returns = engine["returns"][date_index]
        nav_before_return = nav
        gross_return = sum(
            weight * _clamp(daily_returns[index], -RETURN_CAP, RETURN_CAP)
            for index, weight in enumerate(previous_target)
        )
        turnover = sum(
            abs(weight - previous_target[index]) for index, weight in enumerate(target)
        )
        gross_exposure = _row_gross(previous_target)
        trade_cost = turnover * EXECUTION_COST
        financing_cost = (
            max(gross_exposure - 1, 0)
            * FINANCING_ANNUAL
            * _elapsed_days(previous_date, current_date)
            / 365
        )
        net_return = gross_return - trade_cost - financing_cost
        nav *= 1 + net_return
        if not math.isfinite(nav) or nav <= 0:
            raise ValueError("DYN forward paper account exhausted its capital")
        daily.append(
            {
                "date": current_date,
                "financingCost": financing_cost,
                "grossExposure": gross_exposure,
                "navUsd": nav,
                "return": net_return,
                "tradeCost": trade_cost,
            }
        )
        signal_date = engine["dates"][date_index - 1]
        for asset_index, new_weight in enumerate(target):
            old_weight = previous_target[asset_index]
            delta_weight = new_weight - old_weight
            asset = engine["assets"][asset_index]
            previous_tracker = trackers.get(asset)
            if previous_tracker and gross_exposure > EPSILON:
                previous_tracker = {
                    **previous_tracker,
                    "financingCostsUsd": previous_tracker["financingCostsUsd"]
                    + nav_before_return
                    * financing_cost
                    * (abs(old_weight) / gross_exposure),
                }
            if abs(delta_weight) <= EPSILON:
                if previous_tracker:
                    trackers[asset] = previous_tracker
                continue
            price = engine["closes"][date_index][asset_index]
            if price is None or price <= 0:
                continue
            next_tracker = _rebalance_tracker(
                desired_quantity=nav * new_weight / price,
                execution_price=price,
                opened_on=current_date,
                previous=previous_tracker,
                trading_costs_usd=nav_before_return
                * abs(delta_weight)
                * EXECUTION_COST,
            )
            if next_tracker:
                trackers[asset] = next_tracker
            else:
                trackers.pop(asset, None)
            executions.append(
                {
                    "asset": asset,
                    "costToNav": abs(delta_weight) * EXECUTION_COST,
                    "deltaWeight": delta_weight,
                    "id": f"forward-{current_date.replace('-', '')}-{asset}-{len(executions)}",
                    "newWeight": new_weight,
                    "oldWeight": old_weight,
                    "orderDate": current_date,
                    "price": price,
                    "side": "BUY" if delta_weight >= 0 else "SELL",
                    "signalDate": signal_date,
                }
            )
        previous_target = list(target)
        previous_date = current_date
    return {
        "daily": daily,
        "executions": executions,
        "nav": nav,
        "positionTrackers": trackers,
        "target": previous_target,
    }


def _mark_portfolio_to_live(
    engine: dict[str, Any],
    histories: list[dict[str, Any]],
    continuation: dict[str, Any],
) -> dict[str, Any]:
    latest_index = len(engine["dates"]) - 1
    history_by_asset = {history["asset"]: history for history in histories}
    closed_notional_usd = 0.0
    for asset, tracker in continuation["positionTrackers"].items():
        asset_index = engine["assets"].index(asset)
        closed_price = engine["closes"][latest_index][asset_index]
        if closed_price:
            closed_notional_usd += tracker["quantity"] * closed_price
    cash_usd = continuation["nav"] - closed_notional_usd
    nav_usd = cash_usd
    marked: list[dict[str, Any]] = []
    for asset, tracker in continuation["positionTrackers"].items():
        asset_index = engine["assets"].index(asset)
        closed_price = engine["closes"][latest_index][asset_index]
        live_price = history_by_asset[asset]["liveCandle"]["close"] or closed_price
        if not closed_price or not live_price:
            continue
        notional_usd = tracker["quantity"] * live_price
        nav_usd += notional_usd
        direction = math.copysign(1, tracker["quantity"])
        unrealized_pnl_usd = tracker["quantity"] * (
            live_price - tracker["averageEntryPrice"]
        )
        net_pnl_usd = (
            tracker["realizedPnlUsd"]
            + unrealized_pnl_usd
            - tracker["tradingCostsUsd"]
            - tracker["financingCostsUsd"]
        )
        marked.append(
            {
                "asset": asset,
                **tracker,
                "netPnlUsd": net_pnl_usd,
                "notionalUsd": notional_usd,
                "price": live_price,
                "unrealizedPnlPercent": (live_price / tracker["averageEntryPrice"] - 1)
                * direction,
                "unrealizedPnlUsd": unrealized_pnl_usd,
            }
        )
    if not math.isfinite(nav_usd) or nav_usd <= 0:
        raise ValueError("DYN forward paper account exhausted its capital")
    positions = [
        {**position, "weight": position["notionalUsd"] / nav_usd}
        for position in marked
        if abs(position["notionalUsd"]) > EPSILON
    ]
    positions.sort(key=lambda position: -abs(position["weight"]))
    return {
        "borrowedWeight": max(0.0, -cash_usd / nav_usd),
        "cashWeight": max(0.0, cash_usd / nav_usd),
        "navUsd": nav_usd,
        "netExposure": sum(position["weight"] for position in positions),
        "positions": positions,
    }


def compute_forward_state(
    histories: list[dict[str, Any]],
    failed_symbols: list[dict[str, str]],
    *,
    reset_date: str = SNAPSHOT_DATE,
    initial_nav_usd: float = 10_000.0,
    profile: str | DynProfile = "baseline",
) -> dict[str, Any]:
    generated_at = _utc_now()
    profile_config = get_profile(profile)
    if len(histories) < MINIMUM_ASSETS:
        raise ValueError(
            f"Only {len(histories)} DYN assets returned usable daily history; "
            f"at least {MINIMUM_ASSETS} are required"
        )
    engine = build_profile_engine(histories, failed_symbols, profile_config)
    latest_index = len(engine["dates"]) - 1
    latest_date = engine["dates"][latest_index]
    continuation = paper_continuation(
        engine, reset_date=reset_date, initial_nav_usd=initial_nav_usd
    )
    live_portfolio = _mark_portfolio_to_live(engine, histories, continuation)
    target_gross = _row_gross(continuation["target"])
    peak_nav = max(
        initial_nav_usd,
        live_portfolio["navUsd"],
        *(point["navUsd"] for point in continuation["daily"]),
    )
    latest_filters = engine["btcFilters"][latest_index]
    material_executions = [
        execution
        for execution in continuation["executions"]
        if abs(execution["deltaWeight"]) >= MATERIAL_DELTA
        or execution["oldWeight"] <= EPSILON
        or execution["newWeight"] <= EPSILON
    ]
    candles = []
    for history in histories:
        items = [
            {
                "timestamp_ms": candle["openTime"],
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
            }
            for _date_text, candle in sorted(history["bars"].items())[-119:]
        ]
        live_candle = history["liveCandle"]
        if not live_candle["closed"]:
            items.append(
                {
                    "timestamp_ms": live_candle["openTime"],
                    "open": live_candle["open"],
                    "high": live_candle["high"],
                    "low": live_candle["low"],
                    "close": live_candle["close"],
                }
            )
        candles.append(
            {
                "asset": history["asset"],
                "exchange_id": "binance",
                "timeframe": "1d",
                "items": items,
            }
        )
    return {
        "schema_version": 1,
        "mode": profile_config.mode,
        "profile": engine["profile"],
        "absFamilyWeight": engine["absWeight"][latest_index],
        "asOf": latest_date,
        "borrowedWeight": live_portfolio["borrowedWeight"],
        "btcConsensusScore": engine["consensus"][latest_index],
        "btcFilters": [
            {
                "active": latest_filters["f1"],
                "id": "F1",
                "label": "BTC > EMA100 and EMA20 > EMA100",
            },
            {"active": latest_filters["f2"], "id": "F2", "label": "BTC > SMA150"},
            {"active": latest_filters["f3"], "id": "F3", "label": "EMA50 > EMA200"},
        ],
        "cashWeight": live_portfolio["cashWeight"],
        "candles": candles,
        "dataAssetCount": len(engine["assets"]),
        "eligibleAssets": engine["eligibleAssets"][latest_index],
        "failedSymbols": failed_symbols,
        "flowFamilyWeight": engine["flowWeight"][latest_index],
        "generatedAt": generated_at,
        "leverageSignal": engine["leverage"][latest_index],
        "marketDataAt": generated_at,
        "netExposure": live_portfolio["netExposure"],
        "paper": {
            "account": {"initialNavUsd": initial_nav_usd, "resetDate": reset_date},
            "currentDrawdown": live_portfolio["navUsd"] / peak_nav - 1,
            "daily": continuation["daily"],
            "executions": material_executions,
            "lastOrderDate": material_executions[-1]["orderDate"]
            if material_executions
            else None,
            "navUsd": live_portfolio["navUsd"],
            "pnlSinceSnapshotUsd": live_portfolio["navUsd"] - initial_nav_usd,
            "returnSinceSnapshot": live_portfolio["navUsd"] / initial_nav_usd - 1,
            "totalExecutions": len(continuation["executions"]),
        },
        "positions": live_portfolio["positions"],
        "snapshotDate": SNAPSHOT_DATE,
        "status": "ready",
        "strategyId": profile_config.strategy_id,
        "targetGross": target_gross,
        "warnings": [
            f"{failure['symbol']}: {failure['reason']}" for failure in failed_symbols
        ],
        "exchange_submission_available": False,
    }


def _write_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                value,
                handle,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def run_once(
    path: Path,
    *,
    reset_date: str,
    initial_nav_usd: float,
    profile: str | DynProfile = "baseline",
) -> dict[str, Any]:
    histories, failed_symbols = load_asset_histories()
    snapshot = compute_forward_state(
        histories,
        failed_symbols,
        reset_date=reset_date,
        initial_nav_usd=initial_nav_usd,
        profile=profile,
    )
    _write_atomic(path, snapshot)
    return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run DYN-IV113 against real Binance candles in paper mode"
    )
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--reset-date", default=SNAPSHOT_DATE)
    parser.add_argument("--starting-cash", type=float, default=10_000.0)
    parser.add_argument(
        "--profile", choices=tuple(DYN_PROFILES), default="baseline"
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if args.poll_seconds < 10:
        parser.error("poll-seconds must be at least 10")
    while True:
        started = time.monotonic()
        try:
            snapshot = run_once(
                args.snapshot,
                reset_date=args.reset_date,
                initial_nav_usd=args.starting_cash,
                profile=args.profile,
            )
            print(
                json.dumps(
                    {
                        "event": "dyn_paper_snapshot",
                        "profile": args.profile,
                        "status": snapshot["status"],
                        "as_of": snapshot["asOf"],
                        "assets": snapshot["dataAssetCount"],
                        "nav_usd": round(snapshot["paper"]["navUsd"], 4),
                        "target_gross": round(snapshot["targetGross"], 6),
                    },
                    separators=(",", ":"),
                ),
                flush=True,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            print(
                json.dumps(
                    {
                        "event": "dyn_paper_error",
                        "error": f"{type(error).__name__}: {error}",
                    },
                    separators=(",", ":"),
                ),
                flush=True,
            )
        if args.once:
            return 0 if args.snapshot.is_file() else 1
        time.sleep(max(0.0, args.poll_seconds - (time.monotonic() - started)))


if __name__ == "__main__":
    raise SystemExit(main())
