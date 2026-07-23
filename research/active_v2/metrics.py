from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

ANNUAL_DAYS = 365.0


def equity_metrics(equity: pd.Series) -> dict[str, float]:
    equity = equity.dropna()
    if len(equity) < 2:
        return {
            "total_return": np.nan,
            "annualized_return": np.nan,
            "max_drawdown": np.nan,
            "sharpe": np.nan,
            "sortino": np.nan,
            "calmar": np.nan,
        }
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1)
    elapsed = max((equity.index[-1] - equity.index[0]).total_seconds() / 86400, 1)
    annualized = (1 + total_return) ** (ANNUAL_DAYS / elapsed) - 1 if total_return > -1 else -1.0
    drawdown = equity / equity.cummax() - 1
    max_drawdown = float(drawdown.min())
    daily = equity.resample("1D").last().dropna().pct_change().dropna()
    if len(daily) > 1 and daily.std(ddof=1) > 0:
        sharpe = float(math.sqrt(ANNUAL_DAYS) * daily.mean() / daily.std(ddof=1))
    else:
        sharpe = np.nan
    downside = daily[daily < 0]
    if len(downside) > 1 and downside.std(ddof=1) > 0:
        sortino = float(math.sqrt(ANNUAL_DAYS) * daily.mean() / downside.std(ddof=1))
    else:
        sortino = np.nan
    calmar = annualized / abs(max_drawdown) if max_drawdown < 0 else np.nan
    return {
        "total_return": total_return,
        "annualized_return": float(annualized),
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": float(calmar) if np.isfinite(calmar) else np.nan,
    }


def trade_metrics(trades: pd.DataFrame) -> dict[str, float]:
    if trades.empty:
        return {
            "trades": 0,
            "win_rate": np.nan,
            "profit_factor": np.nan,
            "average_trade": np.nan,
            "median_trade": np.nan,
            "net_pnl": 0.0,
            "fees": 0.0,
        }
    pnl = trades["net_pnl"].astype(float)
    gains, losses = pnl[pnl > 0].sum(), pnl[pnl < 0].sum()
    pf = gains / abs(losses) if losses < 0 else (np.inf if gains > 0 else np.nan)
    return {
        "trades": int(len(trades)),
        "win_rate": float((pnl > 0).mean()),
        "profit_factor": float(pf),
        "average_trade": float(trades["net_return"].mean()),
        "median_trade": float(trades["net_return"].median()),
        "net_pnl": float(pnl.sum()),
        "fees": float(trades.get("costs", pd.Series(0, index=trades.index)).sum()),
    }


def period_slice(frame: pd.DataFrame | pd.Series, start: str, end: str):
    begin = pd.Timestamp(start, tz="UTC")
    finish = pd.Timestamp(end, tz="UTC")
    return frame[(frame.index >= begin) & (frame.index < finish)]


def summarize_equity(
    equity: pd.DataFrame | pd.Series,
    periods: dict[str, tuple[str, str]],
    extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, (start, end) in periods.items():
        segment = period_slice(equity, start, end)
        if segment.empty:
            continue
        values = equity_metrics(segment["equity"])
        row: dict[str, Any] = {
            "period": name,
            "start": segment.index[0].isoformat(),
            "end": segment.index[-1].isoformat(),
            "bars": int(len(segment)),
            "average_exposure": float(segment.get("exposure", pd.Series(0, index=segment.index)).mean()),
            "turnover": float(segment.get("turnover", pd.Series(0, index=segment.index)).sum()),
            **values,
        }
        if extra:
            row.update(extra)
        rows.append(row)
    return rows


def robust_score(dev: dict[str, float], val: dict[str, float]) -> float:
    if not all(np.isfinite(x) for x in [dev["total_return"], val["total_return"]]):
        return -1e9
    if dev["total_return"] <= 0 or val["total_return"] <= 0:
        return -1e9
    if dev["max_drawdown"] < -0.40 or val["max_drawdown"] < -0.35:
        return -1e9
    dev_calmar = dev["calmar"] if np.isfinite(dev["calmar"]) else -10
    val_calmar = val["calmar"] if np.isfinite(val["calmar"]) else -10
    weak_calmar = min(dev_calmar, val_calmar)
    weak_return = min(dev["annualized_return"], val["annualized_return"])
    weak_sharpe = min(
        dev["sharpe"] if np.isfinite(dev["sharpe"]) else -10,
        val["sharpe"] if np.isfinite(val["sharpe"]) else -10,
    )
    return float(0.50 * weak_calmar + 0.35 * weak_return + 0.15 * weak_sharpe)
