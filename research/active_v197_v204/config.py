from __future__ import annotations

from dataclasses import dataclass
from itertools import product

SYMBOLS = ("BTCUSD_PERP", "ETHUSD_PERP")
START = "2023-01-01 00:00:00"
DEVELOPMENT_END = "2024-06-30 23:59:59"
VALIDATION_START = "2024-07-01 00:00:00"
VALIDATION_END = "2024-12-31 23:59:59"
HOLDOUT_START = "2025-01-01 00:00:00"
HOLDOUT_END = "2025-12-31 23:59:59"
FINAL_START = "2026-01-01 00:00:00"
END = "2026-06-30 23:59:59"

INITIAL_EQUITY = 10_000.0
TARGET_GROSS = 0.20
NORMALIZED_FREQUENCY = "1min"
ROLLING_WINDOW_MINUTES = 1_440
ROLLING_MIN_PERIODS = 720
REPLENISHMENT_LOOKBACK_MINUTES = 3
PRICE_MOVE_LOOKBACK_MINUTES = 3
REQUIRED_MONTH_COVERAGE = 0.95
MIN_FULL_MONTHS = {
    "development": 15,
    "validation": 5,
    "holdout": 11,
    "final": 5,
}


@dataclass(frozen=True, slots=True)
class Policy:
    family: str
    hold_minutes: int
    threshold: float
    persistence: int = 1
    secondary: float = 0.0
    confirmation: float = 0.0

    @property
    def name(self) -> str:
        return (
            f"{self.family}_t{self.threshold:g}_p{self.persistence}_"
            f"s{self.secondary:g}_c{self.confirmation:g}_h{self.hold_minutes}"
        )


@dataclass(frozen=True, slots=True)
class Audit:
    name: str
    round_trip_cost_bps: float
    execution_delay_minutes: int = 0
    forced_exit_extra_bps: float = 20.0


CONTINUATION = tuple(
    Policy(
        family="imbalance_continuation",
        threshold=threshold,
        persistence=persistence,
        hold_minutes=hold,
    )
    for threshold, persistence, hold in product(
        (1.5, 2.0, 2.5),
        (1, 3),
        (1, 3, 12),
    )
)

FALSE_PRESSURE_CONTROL = tuple(
    Policy(
        family="false_pressure_control",
        threshold=threshold,
        persistence=persistence,
        hold_minutes=hold,
    )
    for threshold, persistence, hold in product(
        (1.5, 2.0, 2.5),
        (1, 3),
        (1, 3, 12),
    )
)

VACUUM = tuple(
    Policy(
        family="liquidity_vacuum_continuation",
        threshold=pressure_threshold,
        secondary=depth_threshold,
        persistence=persistence,
        hold_minutes=hold,
    )
    for pressure_threshold, depth_threshold, persistence, hold in product(
        (1.5, 2.0),
        (-1.5, -2.0),
        (1, 3),
        (1, 3, 12),
    )
)

REPLENISHMENT = tuple(
    Policy(
        family="replenishment_reversal",
        threshold=move_bps,
        secondary=replenishment,
        confirmation=confirmation,
        hold_minutes=hold,
    )
    for move_bps, replenishment, confirmation, hold in product(
        (5.0, 10.0),
        (0.10, 0.25),
        (0.0, 1.0),
        (1, 3, 12),
    )
)

POLICIES = CONTINUATION + VACUUM + REPLENISHMENT + FALSE_PRESSURE_CONTROL
assert len(POLICIES) == 84

AUDITS = (
    Audit("base", round_trip_cost_bps=10.0),
    Audit("severe", round_trip_cost_bps=20.0),
    Audit("extreme", round_trip_cost_bps=35.0),
    Audit("delay_1m", round_trip_cost_bps=10.0, execution_delay_minutes=1),
)

DEVELOPMENT_GATES = {
    "cagr_min": 0.05,
    "sharpe_min": 1.0,
    "max_drawdown_min": -0.10,
    "closed_trades_min": 250,
    "annual_turnover_max": 250.0,
    "btc_return_positive": True,
    "eth_return_positive": True,
}

POST_SELECTION_GATES = {
    "validation_return_positive": True,
    "holdout_return_positive": True,
    "final_return_positive": True,
    "severe_full_cagr_positive": True,
    "latency_full_cagr_positive": True,
    "worst_calendar_quarter_min": -0.08,
    "top_month_positive_pnl_share_max": 0.30,
    "unexplained_book_events_max": 0,
}
