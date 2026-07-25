from __future__ import annotations

PERIODS = {
    "bridge": ("2021-01-01", "2024-01-01"),
    "holdout": ("2024-01-01", "2026-01-01"),
    "final_2026h1": ("2026-01-01", "2026-07-01"),
    "prefinal": ("2021-01-01", "2026-01-01"),
    "full": ("2021-01-01", "2026-07-01"),
}
STATIC_ATLAS = (0.50, 0.60, 0.70, 0.80, 0.90)
STATIC_EXTERNAL = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30)
REBALANCE_DAYS = (20, 40, 60)
DYNAMIC_TARGET_VOL = (0.18, 0.20, 0.22)
DYNAMIC_CORR_THRESHOLD = (0.45, 0.55, 0.65)
DYNAMIC_LEVERAGE_CAP = (1.10, 1.15, 1.20)
PORTFOLIO_GROSS_CAP = 1.25
TRANSFER_COST_BPS = 10.0
FINANCING_RATE = 0.045
DRAWDOWN_SOFT = -0.10
DRAWDOWN_HARD = -0.15
RECOVERY = -0.05
PROMOTION = {
    "cagr_loss_max": 0.01,
    "dd_improvement_min": 0.02,
    "sharpe_improvement_min": 0.04,
    "max_gross": 1.25,
}
