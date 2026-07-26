from __future__ import annotations

from dataclasses import dataclass
from itertools import product

ASSETS = ("BTC", "ETH")
SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}

START = "2021-01-01 00:00:00"
DEVELOPMENT_END = "2023-12-31 23:59:59"
VALIDATION_START = "2024-01-01 00:00:00"
VALIDATION_END = "2024-12-31 23:59:59"
HOLDOUT_START = "2025-01-01 00:00:00"
HOLDOUT_END = "2025-12-31 23:59:59"
FINAL_START = "2026-01-01 00:00:00"
END = "2026-06-30 23:59:59"

BAR_MINUTES = 5
INITIAL_EQUITY = 10_000.0
TARGET_GROSS = 0.20
RETURN_WINDOW = 288          # 24h of 5m bars
FLOW_WINDOW = 288            # 24h
BASIS_WINDOW = 2016          # 7d
MIN_FEATURE_PERIODS = 144
MAX_ABS_BASIS_BPS = 250.0

FAMILIES = (
    "spot_lead_continuation",
    "perp_unconfirmed_reversal",
    "basis_flow_convergence",
    "reversed_spot_lead_control",
)


@dataclass(frozen=True, slots=True)
class Policy:
    family: str
    shock_z: float
    gap_z: float
    hold_bars: int
    persistence: int

    @property
    def name(self) -> str:
        return (
            f"{self.family}_s{self.shock_z:g}_g{self.gap_z:g}_"
            f"h{self.hold_bars}_p{self.persistence}"
        )


@dataclass(frozen=True, slots=True)
class Audit:
    name: str
    single_round_trip_bps: float
    pair_round_trip_bps: float
    delay_bars: int = 0
    forced_exit_extra_bps: float = 15.0


POLICIES = tuple(
    Policy(*values)
    for values in product(
        FAMILIES,
        (1.5, 2.0, 2.5),
        (0.5, 1.0),
        (1, 3, 12),
        (1, 2),
    )
)

AUDITS = (
    Audit("base", single_round_trip_bps=12.0, pair_round_trip_bps=34.0),
    Audit("severe", single_round_trip_bps=20.0, pair_round_trip_bps=50.0),
    Audit("extreme", single_round_trip_bps=35.0, pair_round_trip_bps=75.0),
    Audit(
        "delay_5m",
        single_round_trip_bps=12.0,
        pair_round_trip_bps=34.0,
        delay_bars=1,
    ),
)

DEVELOPMENT_GATES = {
    "cagr_min": 0.05,
    "sharpe_min": 1.0,
    "max_drawdown_min": -0.12,
    "closed_trades_min": 150,
    "annual_turnover_max": 250.0,
    "all_calendar_years_positive": True,
    "btc_pnl_positive": True,
    "eth_pnl_positive": True,
}

POST_SELECTION_GATES = {
    "validation_return_positive": True,
    "holdout_return_positive": True,
    "final_return_positive": True,
    "severe_full_cagr_positive": True,
    "latency_full_cagr_positive": True,
    "worst_calendar_quarter_min": -0.08,
    "top_month_positive_pnl_share_max": 0.30,
    "unexplained_events_max": 0,
}
