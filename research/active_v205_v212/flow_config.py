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
ROLLING_WINDOW_MINUTES = 1_440
ROLLING_MIN_PERIODS = 720
PRICE_IMPACT_LOOKBACK_MINUTES = 3
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
    flow_threshold: float
    pressure_threshold: float
    volume_threshold: float
    max_impact_bps: float
    depth_threshold: float
    persistence: int
    hold_minutes: int

    @property
    def name(self) -> str:
        return (
            f"{self.family}_f{self.flow_threshold:g}_p{self.pressure_threshold:g}_"
            f"v{self.volume_threshold:g}_i{self.max_impact_bps:g}_"
            f"d{self.depth_threshold:g}_n{self.persistence}_h{self.hold_minutes}"
        )


@dataclass(frozen=True, slots=True)
class Audit:
    name: str
    round_trip_cost_bps: float
    execution_delay_minutes: int = 0
    forced_exit_extra_bps: float = 20.0


AGREEMENT = tuple(
    Policy(
        family="agreement_continuation",
        flow_threshold=flow,
        pressure_threshold=pressure,
        volume_threshold=0.0,
        max_impact_bps=0.0,
        depth_threshold=0.0,
        persistence=persistence,
        hold_minutes=hold,
    )
    for flow, pressure, persistence, hold in product(
        (1.5, 2.0),
        (1.0, 1.5),
        (1, 3),
        (1, 3, 12),
    )
)

FLOW_VACUUM = tuple(
    Policy(
        family="flow_vacuum_continuation",
        flow_threshold=flow,
        pressure_threshold=pressure,
        volume_threshold=0.0,
        max_impact_bps=0.0,
        depth_threshold=depth,
        persistence=1,
        hold_minutes=hold,
    )
    for flow, pressure, depth, hold in product(
        (1.5, 2.0),
        (1.0, 1.5),
        (-1.5, -2.0),
        (1, 3, 12),
    )
)

ABSORPTION = tuple(
    Policy(
        family="absorption_reversal",
        flow_threshold=flow,
        pressure_threshold=opposite_pressure,
        volume_threshold=0.0,
        max_impact_bps=impact,
        depth_threshold=0.0,
        persistence=1,
        hold_minutes=hold,
    )
    for flow, opposite_pressure, impact, hold in product(
        (1.5, 2.0),
        (0.5, 1.0),
        (3.0, 6.0),
        (1, 3, 12),
    )
)

EXHAUSTION = tuple(
    Policy(
        family="flow_exhaustion_reversal",
        flow_threshold=flow,
        pressure_threshold=0.0,
        volume_threshold=volume,
        max_impact_bps=impact,
        depth_threshold=0.0,
        persistence=1,
        hold_minutes=hold,
    )
    for flow, volume, impact, hold in product(
        (1.5, 2.0),
        (1.0, 2.0),
        (3.0, 6.0),
        (1, 3, 12),
    )
)

REVERSED_AGREEMENT_CONTROL = tuple(
    Policy(
        family="reversed_agreement_control",
        flow_threshold=flow,
        pressure_threshold=pressure,
        volume_threshold=0.0,
        max_impact_bps=0.0,
        depth_threshold=0.0,
        persistence=persistence,
        hold_minutes=hold,
    )
    for flow, pressure, persistence, hold in product(
        (1.5, 2.0),
        (1.0, 1.5),
        (1, 3),
        (1, 3, 12),
    )
)

POLICIES = AGREEMENT + FLOW_VACUUM + ABSORPTION + EXHAUSTION + REVERSED_AGREEMENT_CONTROL
assert len(POLICIES) == 120

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
    "unexplained_events_max": 0,
}
