from __future__ import annotations

import math

import numpy as np
import pandas as pd


def equity_metrics(equity: pd.Series) -> dict[str, float]:
    equity = equity.dropna()
    keys = ("total_return", "annualized_return", "max_drawdown", "sharpe", "sortino", "calmar", "ulcer_index", "worst_day")
    if len(equity) < 2:
        return {key: np.nan for key in keys}
    total = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    elapsed_days = max((equity.index[-1] - equity.index[0]).total_seconds() / 86_400, 1.0)
    annualized = (1.0 + total) ** (365.0 / elapsed_days) - 1.0 if total > -1.0 else -1.0
    drawdown = equity / equity.cummax() - 1.0
    returns = equity.pct_change().dropna()
    std = float(returns.std(ddof=1))
    sharpe = float(math.sqrt(365.0) * returns.mean() / std) if std > 0 else np.nan
    downside = returns[returns < 0]
    downside_std = float(downside.std(ddof=1))
    sortino = float(math.sqrt(365.0) * returns.mean() / downside_std) if downside_std > 0 else np.nan
    max_drawdown = float(drawdown.min())
    calmar = float(annualized / abs(max_drawdown)) if max_drawdown < 0 else np.nan
    ulcer_index = float(np.sqrt(np.mean(np.square(drawdown))))
    return {
        "total_return": total,
        "annualized_return": float(annualized),
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "ulcer_index": ulcer_index,
        "worst_day": float(returns.min()) if len(returns) else np.nan,
    }


def rolling_diagnostics(equity: pd.Series, window: int = 365) -> dict[str, float]:
    values = (equity / equity.shift(window) - 1.0).dropna()
    return {
        f"rolling_{window}_windows": int(len(values)),
        f"rolling_{window}_positive_share": float((values > 0).mean()) if len(values) else np.nan,
        f"rolling_{window}_worst": float(values.min()) if len(values) else np.nan,
        f"rolling_{window}_median": float(values.median()) if len(values) else np.nan,
    }


def annual_rows(equity: pd.Series, label: str, scenario: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for year, segment in equity.groupby(equity.index.year):
        rows.append({"label": label, "scenario": scenario, "year": int(year), **equity_metrics(segment)})
    return rows
