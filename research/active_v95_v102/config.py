from __future__ import annotations

from dataclasses import dataclass

ETF_GROUPS = {
    "equity": ("SPY", "QQQ", "IWM", "EFA", "EEM", "VNQ"),
    "rates_credit": ("TLT", "IEF", "HYG"),
    "real_assets": ("GLD", "DBC"),
    "usd_fx_etf": ("UUP", "FXE", "FXY"),
}

PERIODS = {
    "development": ("2008-01-01", "2014-01-01"),
    "validation_a": ("2014-01-01", "2018-01-01"),
    "validation_b": ("2018-01-01", "2021-01-01"),
    "bridge": ("2021-01-01", "2024-01-01"),
    "holdout": ("2024-01-01", "2026-01-01"),
    "final_2026h1": ("2026-01-01", "2026-07-01"),
    "prefinal": ("2008-01-01", "2026-01-01"),
    "full": ("2008-01-01", "2026-07-01"),
}

SELECTION_PERIODS = ("development", "validation_a", "validation_b")
COST_SCENARIOS_BPS = {"stress": 10.0, "severe": 25.0, "extreme": 50.0}

@dataclass(frozen=True)
class CandidateSpec:
    family: str
    target_vol: float
    gross_cap: float
    rebalance_days: int
    no_trade_band: float

STANDALONE_GATES = {
    "selection_cagr_min": 0.04,
    "selection_sharpe_min": 0.45,
    "selection_max_drawdown_min": -0.22,
    "selection_turnover_max": 12.0,
    "worst_severe_period_min": -0.08,
}

PROMOTION_GATES = {
    "prefinal_cagr_min": 0.05,
    "prefinal_sharpe_min": 0.55,
    "prefinal_max_drawdown_min": -0.20,
    "post_2020_cagr_min": 0.035,
    "best_positive_year_log_share_max": 0.45,
    "worst_rolling_252_min": -0.15,
}

BLEND_WEIGHTS = (0.10, 0.20, 0.30, 0.40)
