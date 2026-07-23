from __future__ import annotations

import math

import numpy as np
import pandas as pd


def equity_metrics(equity: pd.Series) -> dict[str, float]:
    equity = equity.dropna()
    if len(equity) < 2:
        return {key: np.nan for key in (
            "total_return", "annualized_return", "max_drawdown", "sharpe", "sortino", "calmar"
        )}
    total = float(equity.iloc[-1] / equity.iloc[0] - 1)
    elapsed = max((equity.index[-1] - equity.index[0]).total_seconds() / 86400, 1)
    annualized = (1 + total) ** (365 / elapsed) - 1 if total > -1 else -1.0
    drawdown = equity / equity.cummax() - 1
    returns = equity.pct_change().dropna()
    std = returns.std(ddof=1)
    sharpe = float(math.sqrt(365) * returns.mean() / std) if std > 0 else np.nan
    downside = returns[returns < 0]
    downside_std = downside.std(ddof=1)
    sortino = float(math.sqrt(365) * returns.mean() / downside_std) if downside_std > 0 else np.nan
    max_dd = float(drawdown.min())
    calmar = float(annualized / abs(max_dd)) if max_dd < 0 else np.nan
    return {
        "total_return": total, "annualized_return": float(annualized),
        "max_drawdown": max_dd, "sharpe": sharpe, "sortino": sortino, "calmar": calmar,
    }


def slice_frame(frame: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    begin, finish = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    return frame[(frame.index >= begin) & (frame.index < finish)]
