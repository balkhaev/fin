"""Frozen Stage-1 sleeves and portfolio safety transforms."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Iterable

from ._ds40180_common import (
    ASSET_VOLATILITY_LOOKBACK,
    EPSILON,
    PAPER_ASSET_CAP,
    PAPER_GROSS_CAP,
    SLEEVE_ADVERSE_SHORT_CARRY_ANNUAL,
    SLEEVE_ANNUAL_DAYS,
    SLEEVE_ASSET_CAP,
    SLEEVE_EXECUTION_COST,
    SLEEVE_TARGET_VOLATILITY,
    _annualized_volatility,
    _clamp,
    _ema,
    _gross,
    _sign,
    _zero_row,
)


def _donchian_module(
    prices: list[float], entry: int, exit_window: int
) -> list[float]:
    state = 0.0
    output: list[float] = []
    for index, price in enumerate(prices):
        if index >= entry and price > max(prices[index - entry : index]):
            state = 1.0
        elif index >= entry and price < min(prices[index - entry : index]):
            state = -1.0
        elif (
            state > 0
            and index >= exit_window
            and price < min(prices[index - exit_window : index])
        ):
            state = 0.0
        elif (
            state < 0
            and index >= exit_window
            and price > max(prices[index - exit_window : index])
        ):
            state = 0.0
        output.append(state)
    return output


def _average_rows(rows: list[list[float | None]]) -> list[float | None]:
    if not rows:
        return []
    output: list[float | None] = []
    for values in zip(*rows, strict=True):
        if any(value is None for value in values):
            output.append(None)
        else:
            output.append(sum(float(value) for value in values) / len(values))
    return output


def _donchian_score(
    prices: list[float], modules: Iterable[tuple[int, int]]
) -> list[float | None]:
    return _average_rows(
        [_donchian_module(prices, entry, exit_window) for entry, exit_window in modules]
    )


def _momentum_score(prices: list[float], horizons: Iterable[int]) -> list[float | None]:
    horizon_list = list(horizons)
    output: list[float | None] = []
    for index, price in enumerate(prices):
        if any(index < horizon for horizon in horizon_list):
            output.append(None)
            continue
        output.append(
            sum(_sign(price / prices[index - horizon] - 1.0) for horizon in horizon_list)
            / len(horizon_list)
        )
    return output


def _ema_score(prices: list[float], spans: Iterable[int]) -> list[float | None]:
    span_list = list(spans)
    components = [_ema(prices, span) for span in span_list]
    output: list[float | None] = []
    for index, price in enumerate(prices):
        values = [component[index] for component in components]
        if any(value is None for value in values):
            output.append(None)
            continue
        output.append(
            sum(_sign(price / float(value) - 1.0) for value in values) / len(values)
        )
    return output


def _normalize_inverse_volatility(
    signals: list[bool], inverse_volatility: list[float]
) -> list[float]:
    raw = [
        inverse_volatility[index] if signals[index] and inverse_volatility[index] > 0 else 0.0
        for index in range(len(signals))
    ]
    total = sum(raw)
    return [value / total if total > 0 else 0.0 for value in raw]


def _budget_value(
    series: Sequence[float] | None,
    index: int,
    *,
    bear: Sequence[bool] | None,
    bear_budget: float,
    bull_budget: float,
) -> float:
    if series is not None:
        return float(series[index])
    return bear_budget if bear is not None and bear[index] else bull_budget


def _run_sleeve(
    *,
    dates: list[str],
    returns: list[list[float]],
    eligible: list[list[bool]],
    inverse_volatility: list[list[float]],
    long_entries: list[list[bool]],
    short_entries: list[list[bool]],
    bear: list[bool] | None = None,
    bear_long_budget: float = 1.0,
    bear_short_budget: float = 0.0,
    long_budget_by_day: Sequence[float] | None = None,
    short_budget_by_day: Sequence[float] | None = None,
) -> dict[str, Any]:
    asset_count = len(returns[0])
    unscaled_weights: list[list[float]] = []
    for date_index in range(len(dates)):
        prior = date_index - 1
        if prior < 0:
            unscaled_weights.append(_zero_row(asset_count))
            continue
        long_signal = [
            long_entries[prior][asset_index] and eligible[date_index][asset_index]
            for asset_index in range(asset_count)
        ]
        short_signal = [
            short_entries[prior][asset_index] and eligible[date_index][asset_index]
            for asset_index in range(asset_count)
        ]
        long_budget = _budget_value(
            long_budget_by_day,
            prior,
            bear=bear,
            bear_budget=bear_long_budget,
            bull_budget=1.0,
        )
        short_budget = _budget_value(
            short_budget_by_day,
            prior,
            bear=bear,
            bear_budget=bear_short_budget,
            bull_budget=0.0,
        )
        long_weights = _normalize_inverse_volatility(
            long_signal, inverse_volatility[date_index]
        )
        short_weights = _normalize_inverse_volatility(
            short_signal, inverse_volatility[date_index]
        )
        unscaled_weights.append(
            [
                _clamp(long_weights[index] * long_budget, 0.0, SLEEVE_ASSET_CAP)
                - _clamp(short_weights[index] * short_budget, 0.0, SLEEVE_ASSET_CAP)
                for index in range(asset_count)
            ]
        )

    preliminary_returns = [
        sum(weight * returns[index][asset] for asset, weight in enumerate(row))
        for index, row in enumerate(unscaled_weights)
    ]
    volatility_scale: list[float] = []
    weights: list[list[float]] = []
    for index, row in enumerate(unscaled_weights):
        realized = _annualized_volatility(
            preliminary_returns,
            index,
            ASSET_VOLATILITY_LOOKBACK,
            40,
            SLEEVE_ANNUAL_DAYS,
        )
        scale = (
            min(1.0, SLEEVE_TARGET_VOLATILITY / realized)
            if realized is not None and realized > 0
            else 1.0
        )
        volatility_scale.append(scale)
        weights.append([value * scale for value in row])

    strategy_returns: list[float] = []
    turnover: list[float] = []
    long_exposure: list[float] = []
    short_exposure: list[float] = []
    previous = _zero_row(asset_count)
    for index, row in enumerate(weights):
        row_turnover = sum(
            abs(value - previous[asset]) for asset, value in enumerate(row)
        )
        row_short = sum(max(-value, 0.0) for value in row)
        strategy_returns.append(
            sum(value * returns[index][asset] for asset, value in enumerate(row))
            - SLEEVE_EXECUTION_COST * row_turnover
            - SLEEVE_ADVERSE_SHORT_CARRY_ANNUAL / SLEEVE_ANNUAL_DAYS * row_short
        )
        turnover.append(row_turnover)
        long_exposure.append(sum(max(value, 0.0) for value in row))
        short_exposure.append(row_short)
        previous = row
    return {
        "weights": weights,
        "returns": strategy_returns,
        "turnover": turnover,
        "longExposure": long_exposure,
        "shortExposure": short_exposure,
        "volatilityScale": volatility_scale,
    }


def _apply_target_safety(
    row: list[float],
    *,
    gross_cap: float = PAPER_GROSS_CAP,
    asset_cap: float = PAPER_ASSET_CAP,
) -> tuple[list[float], bool]:
    clipped = [_clamp(value, -asset_cap, asset_cap) for value in row]
    gross = _gross(clipped)
    if gross <= gross_cap + EPSILON:
        return clipped, clipped != row
    return [value * gross_cap / gross for value in clipped], True
