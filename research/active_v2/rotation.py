from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

import numpy as np
import pandas as pd

from config import Costs, ResearchConfig, RotationParams
from data import aggregate
from metrics import equity_metrics, robust_score

BARS_4H_PER_DAY = 6
ANNUALIZATION_4H = BARS_4H_PER_DAY * 365


class RotationCache:
    def __init__(self, raw: dict[str, pd.DataFrame]):
        frames: dict[str, pd.DataFrame] = {}
        for symbol, frame in raw.items():
            four_hour = aggregate(frame, "4h").copy()
            four_hour.index = four_hour.index + pd.Timedelta(minutes=15)
            frames[symbol] = four_hour
        index: pd.DatetimeIndex | None = None
        for frame in frames.values():
            index = frame.index if index is None else index.intersection(frame.index)
        if index is None or len(index) < 2_000:
            raise ValueError("insufficient common 4h history")
        self.index = index.sort_values()
        self.symbols = tuple(sorted(frames))
        self.open = pd.DataFrame({s: frames[s]["open"].reindex(self.index) for s in self.symbols})
        self.close = pd.DataFrame({s: frames[s]["close"].reindex(self.index) for s in self.symbols})
        self.returns = self.close.pct_change().fillna(0.0)
        self._momentum: dict[int, pd.DataFrame] = {}
        self._ema: dict[int, pd.DataFrame] = {}
        self._vol: dict[int, pd.DataFrame] = {}

    def momentum(self, days: int) -> pd.DataFrame:
        if days not in self._momentum:
            self._momentum[days] = self.close.pct_change(days * BARS_4H_PER_DAY)
        return self._momentum[days]

    def ema(self, days: int) -> pd.DataFrame:
        if days not in self._ema:
            span = days * BARS_4H_PER_DAY
            self._ema[days] = self.close.ewm(span=span, adjust=False, min_periods=span).mean()
        return self._ema[days]

    def vol(self, days: int) -> pd.DataFrame:
        if days not in self._vol:
            window = days * BARS_4H_PER_DAY
            self._vol[days] = self.returns.rolling(window, min_periods=window).std().shift(1) * np.sqrt(
                ANNUALIZATION_4H
            )
        return self._vol[days]


def target_weights(cache: RotationCache, params: RotationParams) -> pd.DataFrame:
    fast = cache.momentum(params.fast_days)
    slow = cache.momentum(params.slow_days)
    ema = cache.ema(params.ema_days)
    vol = cache.vol(params.vol_days)
    score = 0.40 * fast.div(vol.replace(0, np.nan)) + 0.60 * slow.div(vol.replace(0, np.nan))
    eligible = (cache.close > ema) & (fast > 0) & (slow > 0) & np.isfinite(vol)
    weights = pd.DataFrame(0.0, index=cache.index, columns=cache.symbols)
    selected: str | None = None
    rebalance_every = max(1, params.rebalance_hours // 4)
    current = {symbol: 0.0 for symbol in cache.symbols}
    for i, timestamp in enumerate(cache.index):
        if i % rebalance_every != 0:
            for symbol in cache.symbols:
                weights.at[timestamp, symbol] = current[symbol]
            continue
        eligible_symbols = [symbol for symbol in cache.symbols if bool(eligible.at[timestamp, symbol])]
        if not eligible_symbols:
            selected = None
            current = {symbol: 0.0 for symbol in cache.symbols}
        else:
            leader = max(eligible_symbols, key=lambda symbol: float(score.at[timestamp, symbol]))
            if selected in eligible_symbols and leader != selected:
                current_score = float(score.at[timestamp, selected])
                leader_score = float(score.at[timestamp, leader])
                if leader_score < current_score + params.hysteresis:
                    leader = selected
            selected = leader
            annual_vol = float(vol.at[timestamp, selected])
            allocation = min(1.0, params.target_vol / annual_vol) if annual_vol > 0 else 0.0
            current = {symbol: (allocation if symbol == selected else 0.0) for symbol in cache.symbols}
        for symbol in cache.symbols:
            weights.at[timestamp, symbol] = current[symbol]
    return weights


def simulate_weights(
    cache: RotationCache,
    signal_weights: pd.DataFrame,
    costs: Costs,
    config: ResearchConfig,
    start: str,
    end: str,
) -> pd.DataFrame:
    begin, finish = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    mask = (cache.index >= begin) & (cache.index < finish)
    index = cache.index[mask]
    if len(index) < 100:
        raise ValueError(f"insufficient rotation period: {start}..{end}")
    equity = config.starting_equity
    high_water = equity
    actual = {symbol: 0.0 for symbol in cache.symbols}
    pending = {symbol: 0.0 for symbol in cache.symbols}
    hard_stop = False
    rows: list[dict[str, float | pd.Timestamp]] = []
    first_location = cache.index.get_loc(index[0])
    if first_location > 0:
        previous_time = cache.index[first_location - 1]
        pending = {symbol: float(signal_weights.at[previous_time, symbol]) for symbol in cache.symbols}
    for timestamp in index:
        if hard_stop:
            pending = {symbol: 0.0 for symbol in cache.symbols}
        turnover = sum(abs(pending[symbol] - actual[symbol]) for symbol in cache.symbols)
        cost_cash = equity * turnover * costs.rate
        equity_after_cost = max(0.0, equity - cost_cash)
        asset_values = {symbol: pending[symbol] * equity_after_cost for symbol in cache.symbols}
        cash_value = max(0.0, (1.0 - sum(pending.values())) * equity_after_cost)
        for symbol in cache.symbols:
            asset_values[symbol] *= 1.0 + float(cache.returns.at[timestamp, symbol])
        equity = cash_value + sum(asset_values.values())
        if equity <= 0:
            equity = 0.0
            actual = {symbol: 0.0 for symbol in cache.symbols}
        else:
            actual = {symbol: asset_values[symbol] / equity for symbol in cache.symbols}
        high_water = max(high_water, equity)
        drawdown = equity / high_water - 1.0 if high_water else -1.0
        if drawdown <= -config.hard_drawdown_stop:
            hard_stop = True
        rows.append(
            {
                "time": timestamp,
                "equity": equity,
                "drawdown": drawdown,
                "exposure": sum(actual.values()),
                "turnover": turnover,
                "costs": cost_cash,
                **{f"weight_{symbol}": actual[symbol] for symbol in cache.symbols},
            }
        )
        pending = {symbol: float(signal_weights.at[timestamp, symbol]) for symbol in cache.symbols}
    return pd.DataFrame(rows).set_index("time")


def evaluate_rotation_grid(
    cache: RotationCache,
    params_grid: Iterable[RotationParams],
    costs: Costs,
    config: ResearchConfig,
) -> pd.DataFrame:
    periods = {
        "development": (config.start, config.development_end),
        "validation": (config.development_end, config.validation_end),
    }
    rows: list[dict[str, object]] = []
    for number, params in enumerate(params_grid, start=1):
        weights = target_weights(cache, params)
        period_metrics: dict[str, dict[str, float]] = {}
        row: dict[str, object] = {"key": params.key, **asdict(params)}
        for period, (start, end) in periods.items():
            equity = simulate_weights(cache, weights, costs, config, start, end)
            values = equity_metrics(equity["equity"])
            period_metrics[period] = values
            for key, value in values.items():
                row[f"{period}_{key}"] = value
            row[f"{period}_turnover"] = float(equity["turnover"].sum())
        row["robust_score"] = robust_score(period_metrics["development"], period_metrics["validation"])
        rows.append(row)
        if number % 100 == 0:
            print(f"rotation candidates evaluated: {number}")
    return pd.DataFrame(rows).sort_values("robust_score", ascending=False).reset_index(drop=True)


def neighbor_count(results: pd.DataFrame) -> pd.Series:
    parameter_columns = [
        "fast_days", "slow_days", "ema_days", "vol_days", "target_vol", "rebalance_hours", "hysteresis"
    ]
    viable = results[results["robust_score"] > -1e8]
    counts: list[int] = []
    for _, row in results.iterrows():
        count = 0
        for _, other in viable.iterrows():
            differences = sum(row[column] != other[column] for column in parameter_columns)
            if differences <= 1:
                count += 1
        counts.append(count)
    return pd.Series(counts, index=results.index, dtype=int)


def ensemble_weights(weight_frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not weight_frames:
        raise ValueError("no rotation weights selected")
    total = weight_frames[0].copy() * 0.0
    for frame in weight_frames:
        total = total.add(frame, fill_value=0.0)
    return total / len(weight_frames)
