from __future__ import annotations

from dataclasses import dataclass
from itertools import product

ASSETS = ("BTC", "ETH", "SOL", "XRP", "DOGE")
START = "2023-05-01 00:00:00"
DEVELOPMENT_END = "2024-12-31 23:59:59"
HOLDOUT_START = "2025-01-01 00:00:00"
HOLDOUT_END = "2025-12-31 23:59:59"
FINAL_START = "2026-01-01 00:00:00"
END = "2026-06-30 23:59:59"
INITIAL_EQUITY = 10_000.0
LEG_GROSS = 0.50
TARGET_GROSS = 1.0
BLOCK_HOURS = 8


@dataclass(frozen=True, slots=True)
class Policy:
    lookback_blocks: int
    min_predicted_edge_bps: float
    hold_blocks: int
    max_abs_basis_bps: float

    @property
    def name(self) -> str:
        return (
            f"fund_l{self.lookback_blocks}_e{self.min_predicted_edge_bps:g}_"
            f"h{self.hold_blocks}_b{self.max_abs_basis_bps:g}"
        )


@dataclass(frozen=True, slots=True)
class Audit:
    name: str
    fee_bps_per_side: float
    slippage_bps_per_side: float
    execution_delay_blocks: int = 0
    missing_funding_penalty_bps: float = 8.0
    forced_exit_extra_bps: float = 15.0

    @property
    def trade_rate(self) -> float:
        return (self.fee_bps_per_side + self.slippage_bps_per_side) / 10_000.0


POLICIES = tuple(
    Policy(*values)
    for values in product(
        (3, 6, 12),
        (16.0, 24.0, 32.0),
        (1, 2, 3),
        (20.0, 40.0),
    )
)

AUDITS = (
    Audit("base", fee_bps_per_side=5.0, slippage_bps_per_side=2.5),
    Audit("severe", fee_bps_per_side=5.5, slippage_bps_per_side=6.0),
    Audit("extreme", fee_bps_per_side=7.5, slippage_bps_per_side=10.0),
    Audit(
        "delay_8h",
        fee_bps_per_side=5.0,
        slippage_bps_per_side=2.5,
        execution_delay_blocks=1,
    ),
)

DEVELOPMENT_GATES = {
    "cagr_min": 0.05,
    "sharpe_min": 0.75,
    "max_drawdown_min": -0.10,
    "all_calendar_years_positive": True,
    "trade_count_min": 20,
    "annual_turnover_max": 40.0,
}

POST_SELECTION_GATES = {
    "holdout_return_positive": True,
    "final_return_positive": True,
    "severe_full_cagr_positive": True,
    "delay_full_cagr_positive": True,
    "worst_year_min": -0.08,
    "trade_count_min": 30,
    "zero_forced_exits": True,
}
