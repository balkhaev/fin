from __future__ import annotations

PERIODS = {
    "development": ("2021-01-01", "2023-01-01"),
    "validation_a": ("2023-01-01", "2024-01-01"),
    "validation_b": ("2024-01-01", "2025-01-01"),
    "bridge_2025": ("2025-01-01", "2026-01-01"),
    "final_2026h1": ("2026-01-01", "2026-07-01"),
    "full": ("2021-01-01", "2026-07-01"),
}

SCENARIOS = {
    "nominal": {"spot_cost_bps": 40.0, "perp_cost_bps": 20.0},
    "stress": {"spot_cost_bps": 40.0, "perp_cost_bps": 40.0},
    "severe": {"spot_cost_bps": 80.0, "perp_cost_bps": 80.0},
}

FORCED_DELISTING_PENALTY_BPS = 100.0
TARGET_GROSS_CAP = 0.85
V8_OVERLAY_SCALE = 0.40

RATCHET = {
    "initial_scale": 1.50,
    "first_high_water_multiple": 1.50,
    "first_reduced_scale": 1.00,
    "second_high_water_multiple": 2.00,
    "second_reduced_scale": 0.75,
}

V7_HEDGE_COMPONENTS = (
    {"kind": "carry_bear", "max_size": 0.30, "ema_days": 100, "mom_days": 252,
     "vol_days": 90, "target_vol": 0.10, "every": 14},
    {"kind": "dual", "max_size": 0.40, "ema_days": 100, "mom_days": 252,
     "vol_days": 90, "target_vol": 0.10, "every": 14},
    {"kind": "carry_bear", "max_size": 0.40, "ema_days": 100, "mom_days": 252,
     "vol_days": 60, "target_vol": 0.10, "every": 14},
)

V8_COMPONENTS = (
    {"lookback_days": 126, "threshold": 0.05, "vol_days": 60,
     "target_vol": 0.20, "max_gross": 0.75, "rebalance_days": 7},
    {"lookback_days": 90, "threshold": 0.15, "vol_days": 30,
     "target_vol": 0.10, "max_gross": 1.00, "rebalance_days": 28},
    {"lookback_days": 90, "threshold": 0.15, "vol_days": 60,
     "target_vol": 0.10, "max_gross": 1.00, "rebalance_days": 28},
)
