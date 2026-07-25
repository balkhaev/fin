from __future__ import annotations

import math
import numpy as np
import pandas as pd


def cagr(returns: pd.Series) -> float:
    returns = returns.dropna()
    growth = float((1.0 + returns).prod())
    years = len(returns) / 252.0
    return growth ** (1.0 / years) - 1.0 if len(returns) and growth > 0.0 else -1.0


def metrics(account: pd.DataFrame) -> dict[str, float]:
    equity = account.equity
    returns = equity.pct_change().fillna(0.0)
    annual = cagr(returns)
    volatility = float(returns.std(ddof=0) * math.sqrt(252.0))
    drawdown = float((equity / equity.cummax() - 1.0).min())
    years = max(len(account) / 252.0, 1e-9)
    return {
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1.0),
        "annualized_return": annual,
        "sharpe": annual / volatility if volatility else 0.0,
        "max_drawdown": drawdown,
        "calmar": annual / abs(drawdown) if drawdown < 0.0 else 0.0,
        "annual_turnover": float(account.turnover.sum() / years),
        "average_gross": float(account.gross.mean()),
        "max_gross": float(account.gross.max()),
        "final_equity": float(equity.iloc[-1]),
    }


def yearly(account: pd.DataFrame) -> pd.DataFrame:
    returns = account.equity.pct_change().fillna(0.0)
    rows = []
    for year, group in returns.groupby(returns.index.year):
        equity = (1.0 + group).cumprod()
        rows.append({
            "year": int(year),
            "return": float(equity.iloc[-1] - 1.0),
            "max_drawdown": float((equity / equity.cummax() - 1.0).min()),
        })
    return pd.DataFrame(rows)


def diagnostics(account: pd.DataFrame) -> dict[str, float]:
    yearly_frame = yearly(account)
    positive = [math.log1p(float(value)) for value in yearly_frame["return"] if value > 0.0]
    share = max(positive) / sum(positive) if positive and sum(positive) > 0.0 else 0.0
    returns = account.equity.pct_change().fillna(0.0)
    rolling = (1.0 + returns).rolling(252).apply(np.prod, raw=True) - 1.0
    post = returns[returns.index >= pd.Timestamp("2021-01-01", tz="UTC")]
    return {
        "best_positive_year_log_share": float(share),
        "worst_rolling_252": float(rolling.min()),
        "post2020_cagr": float(cagr(post)),
        "positive_year_fraction": float((yearly_frame["return"] > 0.0).mean()),
    }
