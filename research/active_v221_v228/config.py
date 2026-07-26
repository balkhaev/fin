from __future__ import annotations

from dataclasses import dataclass
from itertools import product

ASSETS = ("BTC", "ETH")
SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
BAR_MINUTES = 5
START = "2021-01-01 00:00:00"
DEVELOPMENT_END = "2023-12-31 23:59:59"
VALIDATION_START = "2024-01-01 00:00:00"
VALIDATION_END = "2024-12-31 23:59:59"
HOLDOUT_START = "2025-01-01 00:00:00"
HOLDOUT_END = "2025-12-31 23:59:59"
FINAL_START = "2026-01-01 00:00:00"
END = "2026-06-30 23:59:59"
INITIAL_EQUITY = 10_000.0
TARGET_GROSS = 0.20
SHOCK_WINDOW_BARS = 3
ROLLING_WINDOW_BARS = 2016
ROLLING_MIN_PERIODS = 576

FAMILIES = (
    "btc_leads_eth_continuation",
    "btc_leads_beta_hedged_catchup",
    "eth_overshoot_beta_hedged_reversal",
    "eth_leads_btc_negative_control",
)
PROMOTABLE_FAMILIES = FAMILIES[:-1]


@dataclass(frozen=True, slots=True)
class Policy:
    family: str
    shock_abs_z: float
    gap_abs_z: float
    hold_bars: int
    persistence_bars: int

    @property
    def name(self) -> str:
        return (
            f"{self.family}_s{self.shock_abs_z:g}_g{self.gap_abs_z:g}_"
            f"h{self.hold_bars}_p{self.persistence_bars}"
        )


@dataclass(frozen=True, slots=True)
class Audit:
    name: str
    single_round_trip_bps: float
    pair_round_trip_bps: float
    execution_delay_bars: int = 0
    forced_exit_extra_bps: float = 20.0


POLICIES = tuple(
    Policy(*values)
    for values in product(
        FAMILIES,
        (2.0, 3.0, 4.0),
        (0.75, 1.25),
        (3, 12, 36),
        (1, 2),
    )
)

AUDITS = (
    Audit("base", single_round_trip_bps=12.0, pair_round_trip_bps=24.0),
    Audit("severe", single_round_trip_bps=20.0, pair_round_trip_bps=40.0),
    Audit("extreme", single_round_trip_bps=35.0, pair_round_trip_bps=65.0),
    Audit(
        "delay_1bar",
        single_round_trip_bps=12.0,
        pair_round_trip_bps=24.0,
        execution_delay_bars=1,
    ),
)

DEVELOPMENT_GATES = {
    "cagr_min": 0.05,
    "sharpe_min": 1.0,
    "max_drawdown_min": -0.12,
    "closed_trades_min": 100,
    "annual_turnover_max": 200.0,
    "all_years_positive": True,
    "long_short_side_pnl_positive": True,
}

POST_SELECTION_GATES = {
    "validation_return_positive": True,
    "holdout_return_positive": True,
    "final_return_positive": True,
    "severe_full_cagr_positive": True,
    "delay_full_cagr_positive": True,
    "worst_quarter_min": -0.08,
    "top_month_positive_pnl_share_max": 0.30,
    "zero_forced_exits": True,
}
