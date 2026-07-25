from __future__ import annotations

import math
import numpy as np
import pandas as pd


def annualized_return(returns: pd.Series, periods_per_year: float = 252.0) -> float:
    r = returns.dropna()
    if r.empty:
        return 0.0
    growth = float((1.0 + r).prod())
    years = len(r) / periods_per_year
    return growth ** (1.0 / years) - 1.0 if growth > 0.0 and years > 0.0 else -1.0


def max_drawdown(equity: pd.Series) -> float:
    return float((equity / equity.cummax() - 1.0).min()) if len(equity) else 0.0


def metrics(account: pd.DataFrame, periods_per_year: float = 252.0) -> dict[str, float]:
    equity = account["equity"].astype(float)
    returns = equity.pct_change().fillna(0.0)
    ann = annualized_return(returns, periods_per_year)
    vol = float(returns.std(ddof=0) * math.sqrt(periods_per_year))
    downside = float(returns.clip(upper=0.0).std(ddof=0) * math.sqrt(periods_per_year))
    dd = max_drawdown(equity)
    years = max(len(account) / periods_per_year, 1e-12)
    turnover = float(account.get("turnover", pd.Series(0.0, index=account.index)).sum()) / years
    gross = account.get("gross", pd.Series(0.0, index=account.index))
    return {
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1.0) if len(equity) > 1 else 0.0,
        "annualized_return": float(ann),
        "annualized_volatility": vol,
        "sharpe": float(ann / vol) if vol > 1e-12 else 0.0,
        "sortino": float(ann / downside) if downside > 1e-12 else 0.0,
        "max_drawdown": dd,
        "calmar": float(ann / abs(dd)) if dd < -1e-12 else 0.0,
        "annual_turnover": turnover,
        "average_gross": float(gross.mean()),
        "max_gross": float(gross.max()),
        "final_equity": float(equity.iloc[-1]),
    }


def yearly_returns(account: pd.DataFrame) -> pd.DataFrame:
    r = account.equity.pct_change().fillna(0.0)
    rows = []
    for year, group in r.groupby(r.index.year):
        eq = (1.0 + group).cumprod()
        rows.append({
            "year": int(year),
            "return": float(eq.iloc[-1] - 1.0),
            "max_drawdown": float((eq / eq.cummax() - 1.0).min()),
        })
    return pd.DataFrame(rows)


def concentration(account: pd.DataFrame) -> dict[str, float]:
    yearly = yearly_returns(account)
    positive_logs = [math.log1p(float(x)) for x in yearly["return"] if x > 0]
    share = max(positive_logs) / sum(positive_logs) if positive_logs and sum(positive_logs) > 0 else 0.0
    r = account.equity.pct_change().fillna(0.0)
    rolling = (1.0 + r).rolling(252).apply(np.prod, raw=True) - 1.0
    post = r[r.index >= pd.Timestamp("2021-01-01", tz="UTC")]
    return {
        "best_positive_year_log_share": float(share),
        "worst_rolling_252": float(rolling.min()) if rolling.notna().any() else 0.0,
        "post_2020_cagr": float(annualized_return(post)),
        "positive_year_fraction": float((yearly["return"] > 0).mean()) if len(yearly) else 0.0,
    }
