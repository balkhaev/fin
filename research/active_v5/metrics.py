from __future__ import annotations

import math

import numpy as np
import pandas as pd


def periods_per_year(index: pd.DatetimeIndex) -> float:
    if len(index) < 3:
        return 365.0
    delta = index.to_series().diff().dropna().median()
    seconds = max(float(delta.total_seconds()), 1.0)
    return 365.0 * 24.0 * 60.0 * 60.0 / seconds


def max_drawdown_duration(equity: pd.Series) -> int:
    if equity.empty:
        return 0
    peak = equity.cummax()
    underwater = equity < peak
    best = current = 0
    for value in underwater.to_numpy(bool):
        current = current + 1 if value else 0
        best = max(best, current)
    return int(best)


def equity_metrics(equity: pd.Series) -> dict[str, float]:
    equity = equity.astype(float).dropna()
    if len(equity) < 2 or equity.iloc[0] <= 0:
        return {
            "total_return": np.nan,
            "annualized_return": np.nan,
            "max_drawdown": np.nan,
            "sharpe": np.nan,
            "sortino": np.nan,
            "calmar": np.nan,
            "volatility": np.nan,
            "ulcer_index": np.nan,
            "max_drawdown_duration_bars": np.nan,
        }
    ppy = periods_per_year(equity.index)
    returns = equity.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
    years = max((equity.index[-1] - equity.index[0]).total_seconds() / (365.0 * 86400.0), 1.0 / ppy)
    total = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    annualized = float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0) if equity.iloc[-1] > 0 else -1.0
    vol = float(returns.std(ddof=0) * math.sqrt(ppy))
    mean = float(returns.mean() * ppy)
    sharpe = mean / vol if vol > 1e-12 else np.nan
    downside = returns.clip(upper=0.0)
    downside_vol = float(downside.std(ddof=0) * math.sqrt(ppy))
    sortino = mean / downside_vol if downside_vol > 1e-12 else np.nan
    drawdown = equity / equity.cummax() - 1.0
    max_dd = float(drawdown.min())
    calmar = annualized / abs(max_dd) if max_dd < -1e-12 else np.nan
    ulcer = float(np.sqrt(np.mean(np.square(drawdown.to_numpy(float)))))
    return {
        "total_return": total,
        "annualized_return": annualized,
        "max_drawdown": max_dd,
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "calmar": float(calmar),
        "volatility": vol,
        "ulcer_index": ulcer,
        "max_drawdown_duration_bars": float(max_drawdown_duration(equity)),
    }


def rolling_return_diagnostics(equity: pd.Series, days: int = 365) -> dict[str, float]:
    if equity.empty:
        return {
            "rolling_windows": 0,
            "rolling_positive_share": np.nan,
            "rolling_worst": np.nan,
            "rolling_median": np.nan,
        }
    daily = equity.resample("1D").last().ffill()
    rolling = (daily / daily.shift(days) - 1.0).dropna()
    return {
        "rolling_windows": float(len(rolling)),
        "rolling_positive_share": float((rolling > 0).mean()) if len(rolling) else np.nan,
        "rolling_worst": float(rolling.min()) if len(rolling) else np.nan,
        "rolling_median": float(rolling.median()) if len(rolling) else np.nan,
    }


def yearly_metrics(equity: pd.Series) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    for year, segment in equity.groupby(equity.index.year):
        values = equity_metrics(segment)
        rows.append({"year": int(year), **values})
    return rows
