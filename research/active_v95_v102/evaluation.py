from __future__ import annotations

import pandas as pd

from config import PERIODS, PROMOTION_GATES
from metrics import metrics, concentration


def utc(value):
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


def subset(account: pd.DataFrame, period: str) -> pd.DataFrame:
    start, end = PERIODS[period]
    return account.loc[(account.index >= utc(start)) & (account.index < utc(end))]


def safe_metrics(account: pd.DataFrame) -> dict[str, float]:
    if len(account) < 20:
        return {
            key: 0.0
            for key in (
                "total_return",
                "annualized_return",
                "annualized_volatility",
                "sharpe",
                "sortino",
                "max_drawdown",
                "calmar",
                "annual_turnover",
                "average_gross",
                "max_gross",
                "final_equity",
            )
        }
    return metrics(account)


def evaluate_account(
    account: pd.DataFrame,
    candidate: str,
    scenario: str,
    periods: tuple[str, ...],
) -> list[dict]:
    rows = []
    for period in periods:
        sliced = subset(account, period)
        if len(sliced) >= 20:
            rows.append(
                {
                    "candidate": candidate,
                    "scenario": scenario,
                    "period": period,
                    **safe_metrics(sliced),
                }
            )
    return rows


def promotion_checks(table: pd.DataFrame, stress_account: pd.DataFrame) -> tuple[dict, dict]:
    def get(scenario: str, period: str):
        return table[(table.scenario == scenario) & (table.period == period)].iloc[0]

    concentration_metrics = concentration(subset(stress_account, "prefinal"))
    checks = {
        "bridge_positive": float(get("stress", "bridge").total_return) > 0.0,
        "holdout_positive": float(get("stress", "holdout").total_return) > 0.0,
        "prefinal_cagr_min": float(get("stress", "prefinal").annualized_return)
        >= PROMOTION_GATES["prefinal_cagr_min"],
        "prefinal_sharpe_min": float(get("stress", "prefinal").sharpe)
        >= PROMOTION_GATES["prefinal_sharpe_min"],
        "prefinal_max_drawdown_min": float(get("stress", "prefinal").max_drawdown)
        >= PROMOTION_GATES["prefinal_max_drawdown_min"],
        "post_2020_cagr_min": concentration_metrics["post_2020_cagr"]
        >= PROMOTION_GATES["post_2020_cagr_min"],
        "best_positive_year_log_share_max": concentration_metrics["best_positive_year_log_share"]
        <= PROMOTION_GATES["best_positive_year_log_share_max"],
        "worst_rolling_252_min": concentration_metrics["worst_rolling_252"]
        >= PROMOTION_GATES["worst_rolling_252_min"],
        "severe_full_positive": float(get("severe", "full").annualized_return) > 0.0,
        "extreme_full_positive": float(get("extreme", "full").annualized_return) > 0.0,
    }
    return checks, concentration_metrics
