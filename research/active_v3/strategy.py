from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

import numpy as np
import pandas as pd

from config import Costs, ResearchConfig, TrendParams
from metrics import equity_metrics


class DailyCache:
    def __init__(self, daily: dict[str, pd.DataFrame]):
        index: pd.DatetimeIndex | None = None
        for frame in daily.values():
            index = frame.index if index is None else index.intersection(frame.index)
        if index is None or len(index) < 1_000:
            raise ValueError("insufficient common daily history")
        self.index = index.sort_values()
        self.symbols = tuple(sorted(daily))
        self.open = pd.DataFrame({s: daily[s].open.reindex(self.index) for s in self.symbols})
        self.close = pd.DataFrame({s: daily[s].close.reindex(self.index) for s in self.symbols})
        self.open_to_close = self.close.div(self.open).sub(1).fillna(0.0)
        self.close_returns = self.close.pct_change().fillna(0.0)
        self._momentum: dict[int, pd.DataFrame] = {}
        self._ema: dict[int, pd.DataFrame] = {}
        self._vol: dict[int, pd.DataFrame] = {}

    def momentum(self, days: int) -> pd.DataFrame:
        if days not in self._momentum:
            self._momentum[days] = self.close.pct_change(days)
        return self._momentum[days]

    def ema(self, days: int) -> pd.DataFrame:
        if days not in self._ema:
            self._ema[days] = self.close.ewm(span=days, adjust=False, min_periods=days).mean()
        return self._ema[days]

    def vol(self, days: int) -> pd.DataFrame:
        if days not in self._vol:
            self._vol[days] = self.close_returns.rolling(days, min_periods=days).std().shift(1) * np.sqrt(365)
        return self._vol[days]


def target_weights(cache: DailyCache, params: TrendParams) -> pd.DataFrame:
    fast = cache.momentum(params.fast_days)
    slow = cache.momentum(params.slow_days)
    ema = cache.ema(params.ema_days)
    vol = cache.vol(params.vol_days)
    score = 0.35 * fast.div(vol.replace(0, np.nan)) + 0.65 * slow.div(vol.replace(0, np.nan))
    eligible = (
        (cache.close > ema)
        & (fast > 0)
        & (slow > params.min_slow_momentum)
        & np.isfinite(vol)
    )
    weights = np.zeros((len(cache.index), len(cache.symbols)), dtype=float)
    symbol_to_col = {symbol: i for i, symbol in enumerate(cache.symbols)}
    selected: str | None = None
    target_allocation = 0.0
    held_days = 0
    for i, timestamp in enumerate(cache.index):
        if i % params.rebalance_days != 0:
            if selected is not None:
                weights[i, symbol_to_col[selected]] = target_allocation
                held_days += 1
            continue
        candidates = [symbol for symbol in cache.symbols if bool(eligible.at[timestamp, symbol])]
        if selected is not None and selected not in candidates:
            selected = None
            target_allocation = 0.0
            held_days = 0
        if not candidates:
            selected = None
            target_allocation = 0.0
            held_days = 0
        else:
            leader = max(candidates, key=lambda symbol: float(score.at[timestamp, symbol]))
            if selected in candidates and leader != selected:
                incumbent_score = float(score.at[timestamp, selected])
                leader_score = float(score.at[timestamp, leader])
                if held_days < params.min_hold_days or leader_score < incumbent_score + params.switch_threshold:
                    leader = selected
            if selected != leader:
                selected = leader
                held_days = 0
            annual_vol = float(vol.at[timestamp, selected])
            desired = min(1.0, params.target_vol / annual_vol) if annual_vol > 0 else 0.0
            if abs(desired - target_allocation) >= params.weight_band or target_allocation == 0.0:
                target_allocation = desired
        if selected is not None:
            weights[i, symbol_to_col[selected]] = target_allocation
            held_days += 1
    return pd.DataFrame(weights, index=cache.index, columns=cache.symbols)


def simulate(
    cache: DailyCache,
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
        raise ValueError("insufficient period")
    start_location = cache.index.get_loc(index[0])
    pending = np.zeros(len(cache.symbols), dtype=float)
    if start_location > 0:
        pending = signal_weights.iloc[start_location - 1].to_numpy(float)
    actual = np.zeros(len(cache.symbols), dtype=float)
    equity = config.starting_equity
    high_water = equity
    hard_stop = False
    rows: list[dict[str, object]] = []
    for timestamp in index:
        if hard_stop:
            pending = np.zeros(len(cache.symbols), dtype=float)
        turnover = float(np.abs(pending - actual).sum())
        cost_cash = equity * turnover * costs.rate
        after_cost = max(0.0, equity - cost_cash)
        asset_values = pending * after_cost
        cash_value = max(0.0, (1.0 - pending.sum()) * after_cost)
        returns = cache.open_to_close.loc[timestamp].to_numpy(float)
        asset_values *= 1.0 + returns
        equity = float(cash_value + asset_values.sum())
        actual = asset_values / equity if equity > 0 else np.zeros(len(cache.symbols), dtype=float)
        high_water = max(high_water, equity)
        drawdown = equity / high_water - 1 if high_water > 0 else -1.0
        if drawdown <= -config.hard_drawdown_stop:
            hard_stop = True
        rows.append({
            "time": timestamp, "equity": equity, "drawdown": drawdown,
            "exposure": float(actual.sum()), "turnover": turnover, "costs": cost_cash,
            **{f"weight_{symbol}": float(actual[j]) for j, symbol in enumerate(cache.symbols)},
        })
        pending = signal_weights.loc[timestamp].to_numpy(float)
    return pd.DataFrame(rows).set_index("time")


def robust_score(metrics: list[dict[str, float]], annual_turnovers: list[float]) -> float:
    if any(item["total_return"] <= 0 for item in metrics):
        return -1e9
    if any(item["max_drawdown"] < -0.30 for item in metrics):
        return -1e9
    calmars = [item["calmar"] for item in metrics]
    sharpes = [item["sharpe"] for item in metrics]
    annualized = [item["annualized_return"] for item in metrics]
    if not all(np.isfinite(calmars + sharpes + annualized)):
        return -1e9
    return float(
        0.45 * min(calmars)
        + 0.25 * min(sharpes)
        + 0.25 * min(annualized)
        - 0.01 * max(annual_turnovers)
    )


def evaluate_grid(
    cache: DailyCache,
    params_grid: Iterable[TrendParams],
    base_cost: Costs,
    stress_cost: Costs,
    config: ResearchConfig,
) -> pd.DataFrame:
    periods = {
        "development": (config.start, config.development_end),
        "validation": (config.development_end, config.validation_end),
    }
    rows: list[dict[str, object]] = []
    for number, params in enumerate(params_grid, start=1):
        weights = target_weights(cache, params)
        row: dict[str, object] = {"key": params.key, **asdict(params)}
        score_metrics: list[dict[str, float]] = []
        annual_turnovers: list[float] = []
        for period, (start, end) in periods.items():
            elapsed_years = max((pd.Timestamp(end) - pd.Timestamp(start)).days / 365, 1 / 365)
            for cost in (base_cost, stress_cost):
                equity = simulate(cache, weights, cost, config, start, end)
                values = equity_metrics(equity.equity)
                score_metrics.append(values)
                annual_turnover = float(equity.turnover.sum() / elapsed_years)
                annual_turnovers.append(annual_turnover)
                for key, value in values.items():
                    row[f"{period}_{cost.name}_{key}"] = value
                row[f"{period}_{cost.name}_annual_turnover"] = annual_turnover
        row["robust_score"] = robust_score(score_metrics, annual_turnovers)
        rows.append(row)
        if number % 100 == 0:
            print(f"daily trend candidates evaluated: {number}")
    return pd.DataFrame(rows).sort_values("robust_score", ascending=False).reset_index(drop=True)


def neighbor_count(results: pd.DataFrame) -> pd.Series:
    columns = [
        "fast_days", "slow_days", "ema_days", "vol_days", "target_vol",
        "rebalance_days", "min_hold_days", "switch_threshold", "weight_band",
        "min_slow_momentum",
    ]
    viable = results[results.robust_score > -1e8]
    counts: list[int] = []
    for _, row in results.iterrows():
        count = 0
        for _, other in viable.iterrows():
            if sum(row[column] != other[column] for column in columns) <= 1:
                count += 1
        counts.append(count)
    return pd.Series(counts, index=results.index, dtype=int)


def ensemble_weights(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        raise ValueError("empty ensemble")
    total = frames[0].copy() * 0.0
    for frame in frames:
        total = total.add(frame, fill_value=0.0)
    return total / len(frames)
