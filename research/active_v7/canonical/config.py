from __future__ import annotations

PERIODS = {
    "development": ("2021-01-01", "2023-01-01"),
    "validation_a": ("2023-01-01", "2024-01-01"),
    "validation_b": ("2024-01-01", "2025-01-01"),
    "bridge_2025": ("2025-01-01", "2026-01-01"),
    "final_2026h1": ("2026-01-01", "2026-07-01"),
    "full": ("2021-01-01", "2026-07-01"),
}

SPOT_COST_BPS = 40.0
HEDGE_COST_BPS = 20.0
FORCED_DELISTING_PENALTY_BPS = 100.0
MAX_GROSS = 1.0

HEDGE_COMPONENTS = (
    dict(kind="carry_bear", size=0.30, ema_days=100, mom_days=252, vol_days=90, target_vol=0.10, every=14),
    dict(kind="dual", size=0.40, ema_days=100, mom_days=252, vol_days=90, target_vol=0.10, every=14),
    dict(kind="carry_bear", size=0.40, ema_days=100, mom_days=252, vol_days=60, target_vol=0.10, every=14),
)
