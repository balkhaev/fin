from __future__ import annotations

PERIODS = {
    "selection": ("2021-01-01", "2024-01-01"),
    "holdout": ("2024-01-01", "2026-01-01"),
    "final_2026h1": ("2026-01-01", "2026-07-01"),
    "prefinal": ("2021-01-01", "2026-01-01"),
    "full": ("2021-01-01", "2026-07-01"),
}

LOOKBACKS = (63, 126)
REBALANCE_DAYS = (20, 40)
TRANSFER_COST_BPS = (10.0, 25.0)
TARGET_VOL = (0.18, 0.20)
CORR_THRESHOLDS = (0.30, 0.45)
LEVERAGE_CAPS = (1.00, 1.10, 1.15)
SHRINKAGE = (0.50, 0.75)
TAIL_THRESHOLDS = (0.35, 0.50)
CRISIS_DRAWDOWN = (-0.05, -0.10)
PORTFOLIO_GROSS_CAP = 1.20
FINANCING_RATE = 0.045

WEIGHT_GRID = tuple(
    (atlas, crisis, round(1.0 - atlas - crisis, 10))
    for atlas in (0.55, 0.65, 0.75, 0.85)
    for crisis in (0.05, 0.10, 0.15, 0.20, 0.25)
    if 0.05 <= 1.0 - atlas - crisis <= 0.30
)

SELECTION_GATES = {
    "cagr_floor_vs_atlas": -0.02,
    "max_drawdown_floor": -0.24,
    "sharpe_floor": 1.00,
    "max_gross": 1.20,
}

PROMOTION_GATES = {
    "holdout_positive": True,
    "final_positive": True,
    "cagr_loss_vs_atlas_max": 0.01,
    "dd_improvement_min": 0.015,
    "sharpe_improvement_min": 0.03,
    "delay5_positive": True,
    "severe_transfer_positive": True,
}
