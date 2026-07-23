from __future__ import annotations

import numpy as np
import pandas as pd


def utc(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


class MarketData:
    def __init__(self, daily: dict[str, pd.DataFrame]):
        index: pd.DatetimeIndex | None = None
        for frame in daily.values():
            index = frame.index if index is None else index.intersection(frame.index)
        if index is None or len(index) < 2_000:
            raise ValueError("insufficient common daily history")
        self.index = index.sort_values()
        self.symbols = tuple(sorted(daily))
        if len(self.symbols) != 2:
            raise ValueError("V4 currently expects exactly two risk assets")
        self.open = pd.DataFrame({s: daily[s].open.reindex(self.index) for s in self.symbols})
        self.high = pd.DataFrame({s: daily[s].high.reindex(self.index) for s in self.symbols})
        self.low = pd.DataFrame({s: daily[s].low.reindex(self.index) for s in self.symbols})
        self.close = pd.DataFrame({s: daily[s].close.reindex(self.index) for s in self.symbols})
        self.close_returns = self.close.pct_change().fillna(0.0)
        self.overnight_returns = self.open.div(self.close.shift(1)).sub(1.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        self.intraday_returns = self.close.div(self.open).sub(1.0).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        self._momentum: dict[int, pd.DataFrame] = {}
        self._ema: dict[int, pd.DataFrame] = {}
        self._vol: dict[int, pd.DataFrame] = {}
        self._cov: dict[int, tuple[pd.Series, pd.Series, pd.Series]] = {}
        self._donchian: dict[tuple[int, int], pd.DataFrame] = {}

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
            self._vol[days] = self.close_returns.rolling(days, min_periods=days).std() * np.sqrt(365.0)
        return self._vol[days]

    def covariance(self, days: int) -> tuple[pd.Series, pd.Series, pd.Series]:
        if days not in self._cov:
            first, second = self.symbols
            r1, r2 = self.close_returns[first], self.close_returns[second]
            self._cov[days] = (
                r1.rolling(days, min_periods=days).var() * 365.0,
                r2.rolling(days, min_periods=days).var() * 365.0,
                r1.rolling(days, min_periods=days).cov(r2) * 365.0,
            )
        return self._cov[days]

    def donchian_state(self, entry_days: int, exit_days: int) -> pd.DataFrame:
        key = (entry_days, exit_days)
        if key in self._donchian:
            return self._donchian[key]
        result = pd.DataFrame(0.0, index=self.index, columns=self.symbols)
        for symbol in self.symbols:
            entry = self.close[symbol] > self.high[symbol].rolling(entry_days, min_periods=entry_days).max().shift(1)
            exit_ = self.close[symbol] < self.low[symbol].rolling(exit_days, min_periods=exit_days).min().shift(1)
            active = False
            values = np.zeros(len(self.index), dtype=float)
            for index in range(len(values)):
                if bool(exit_.iloc[index]):
                    active = False
                elif bool(entry.iloc[index]):
                    active = True
                values[index] = float(active)
            result[symbol] = values
        self._donchian[key] = result
        return result


def portfolio_scale(data: MarketData, weights: pd.DataFrame, vol_days: int, target_vol: float) -> pd.DataFrame:
    first, second = data.symbols
    var1, var2, cov12 = data.covariance(vol_days)
    w1, w2 = weights[first], weights[second]
    variance = w1.pow(2) * var1 + w2.pow(2) * var2 + 2.0 * w1 * w2 * cov12
    portfolio_vol = np.sqrt(variance.clip(lower=0.0))
    scale = (target_vol / portfolio_vol.replace(0.0, np.nan)).clip(upper=1.0).fillna(0.0)
    return weights.mul(scale, axis=0).fillna(0.0)


def desired_weights(
    data: MarketData,
    score: pd.DataFrame,
    eligible: pd.DataFrame,
    target_vol: float,
    mode: str,
    vol_days: int = 60,
) -> pd.DataFrame:
    clean = score.where(eligible, 0.0).replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(lower=0.0)
    if mode == "equal":
        raw = eligible.astype(float)
    elif mode == "invvol":
        raw = clean.div(data.vol(vol_days).replace(0.0, np.nan)).fillna(0.0)
    elif mode == "top1":
        raw = pd.DataFrame(0.0, index=data.index, columns=data.symbols)
        matrix = clean.to_numpy(float)
        maxima = matrix.max(axis=1)
        leaders = matrix.argmax(axis=1)
        valid = maxima > 0.0
        raw_values = raw.to_numpy()
        raw_values[np.arange(len(raw_values))[valid], leaders[valid]] = 1.0
        raw = pd.DataFrame(raw_values, index=data.index, columns=data.symbols)
    else:
        raise ValueError(f"unknown mode: {mode}")
    normalized = raw.div(raw.sum(axis=1).replace(0.0, np.nan), axis=0).fillna(0.0)
    return portfolio_scale(data, normalized, vol_days, target_vol)


def scheduled(weights: pd.DataFrame, rebalance_days: int, band: float) -> pd.DataFrame:
    result = np.zeros_like(weights.to_numpy(float))
    current = np.zeros(weights.shape[1], dtype=float)
    for index, desired in enumerate(weights.to_numpy(float)):
        force = (current.sum() == 0.0) != (desired.sum() == 0.0)
        if index % rebalance_days == 0 and (force or np.abs(desired - current).sum() >= band):
            current = desired.copy()
        result[index] = current
    return pd.DataFrame(result, index=weights.index, columns=weights.columns)


def regime_overlay(data: MarketData, kind: str) -> pd.Series:
    if kind == "none":
        return pd.Series(1.0, index=data.index)
    conditions = (
        data.close > data.ema(50),
        data.close > data.ema(200),
        data.momentum(21) > 0,
        data.momentum(126) > 0,
    )
    confidence = sum(condition.astype(float).sum(axis=1) for condition in conditions) / (len(conditions) * len(data.symbols))
    breadth = pd.Series(
        np.select([confidence < 0.25, confidence < 0.50, confidence < 0.75], [0.0, 0.35, 0.70], default=1.0),
        index=data.index,
    )
    if kind == "breadth":
        return breadth
    if kind != "breadth_vol":
        raise ValueError(f"unknown overlay: {kind}")
    market_return = data.close_returns.mean(axis=1)
    short_vol = market_return.rolling(20, min_periods=20).std() * np.sqrt(365.0)
    long_vol = market_return.rolling(252, min_periods=126).std() * np.sqrt(365.0)
    vol_cap = pd.Series(np.where(short_vol > 1.35 * long_vol, 0.50, 1.0), index=data.index)
    drawdown_90 = data.close.div(data.close.rolling(90, min_periods=30).max()).sub(1.0).mean(axis=1)
    dd_cap = pd.Series(np.where(drawdown_90 < -0.20, 0.50, 1.0), index=data.index)
    return (breadth * vol_cap * dd_cap).clip(0.0, 1.0)


def apply_overlay(weights: pd.DataFrame, overlay: pd.Series) -> pd.DataFrame:
    return weights.mul(overlay.reindex(weights.index).fillna(0.0), axis=0)


def mean_frames(frames: Iterable[pd.DataFrame], template: pd.DataFrame) -> pd.DataFrame:
    total = template.copy() * 0.0
    count = 0
    for frame in frames:
        total = total.add(frame, fill_value=0.0)
        count += 1
    if count == 0:
        return total
    return total / count
