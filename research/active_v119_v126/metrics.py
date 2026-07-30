from __future__ import annotations

import math
import numpy as np
import pandas as pd


def cagr(returns: pd.Series) -> float:
    values = returns.dropna()
    if values.empty:
        return 0.0
    growth = float((1.0 + values).prod())
    years = len(values) / 252.0
    return growth ** (1.0 / years) - 1.0 if growth > 0.0 and years > 0.0 else -1.0


def metrics(account: pd.DataFrame) -> dict[str, float]:
    equity = account.equity.astype(float)
    returns = equity.pct_change().fillna(0.0)
    annual = cagr(returns)
    volatility = float(returns.std(ddof=0) * math.sqrt(252.0))
    drawdown = float((equity / equity.cummax() - 1.0).min())
    years = max(len(account) / 252.0, 1e-12)
    return {
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1.0),
        "annualized_return": float(annual),
        "annualized_volatility": volatility,
        "sharpe": float(annual / volatility) if volatility > 1e-12 else 0.0,
        "max_drawdown": drawdown,
        "calmar": float(annual / abs(drawdown)) if drawdown < -1e-12 else 0.0,
        "annual_turnover": float(account.turnover.sum() / years),
        "average_gross": float(account.gross.mean()),
        "max_gross": float(account.gross.max()),
        "average_scale": float(account.scale.mean()),
        "max_scale": float(account.scale.max()),
        "final_equity": float(equity.iloc[-1]),
    }


def yearly(account: pd.DataFrame) -> pd.DataFrame:
    returns = account.equity.pct_change().fillna(0.0)
    rows = []
    for year, group in returns.groupby(returns.index.year):
        path = (1.0 + group).cumprod()
        rows.append({
            "year": int(year),
            "return": float(path.iloc[-1] - 1.0),
            "max_drawdown": float((path / path.cummax() - 1.0).min()),
        })
    return pd.DataFrame(rows)


def diagnostics(account: pd.DataFrame) -> dict[str, float]:
    returns = account.equity.pct_change().fillna(0.0)
    year = yearly(account)
    positive_logs = [math.log1p(float(value)) for value in year["return"] if value > 0.0]
    best_share = max(positive_logs) / sum(positive_logs) if positive_logs and sum(positive_logs) > 0.0 else 0.0
    rolling_252 = (1.0 + returns).rolling(252).apply(np.prod, raw=True) - 1.0
    rolling_504 = (1.0 + returns).rolling(504).apply(np.prod, raw=True) - 1.0
    return {
        "best_positive_year_log_share": float(best_share),
        "worst_rolling_252": float(rolling_252.min()) if rolling_252.notna().any() else 0.0,
        "worst_rolling_504": float(rolling_504.min()) if rolling_504.notna().any() else 0.0,
        "positive_rolling_252_fraction": float((rolling_252.dropna() > 0.0).mean()),
        "positive_rolling_504_fraction": float((rolling_504.dropna() > 0.0).mean()),
    }
