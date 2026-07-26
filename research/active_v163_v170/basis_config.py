from __future__ import annotations

from dataclasses import dataclass
from itertools import product

ASSETS = ("BTC", "ETH", "SOL", "XRP", "DOGE")
START = "2021-01-01"
PREFINAL_END = "2025-12-31 23:59:59"
FINAL_START = "2026-01-01"
END = "2026-06-30 23:59:59"


@dataclass(frozen=True, slots=True)
class Policy:
    lookback_hours: int
    entry_abs_z: float
    entry_abs_spread_bps: float
    max_hold_hours: int
    stability_bars: int

    @property
    def name(self) -> str:
        return (
            f"basis_l{self.lookback_hours}_z{self.entry_abs_z:g}_"
            f"s{self.entry_abs_spread_bps:g}_h{self.max_hold_hours}_"
            f"c{self.stability_bars}"
        )


@dataclass(frozen=True, slots=True)
class Audit:
    name: str
    fee_bps: float
    slippage_bps: float
    funding_buffer_bps: float
    forced_exit_extra_bps: float
    execution_delay_hours: int = 0


POLICIES = tuple(
    Policy(*values)
    for values in product(
        (168, 336, 672),
        (3.0, 4.0, 5.0),
        (30.0, 45.0, 60.0),
        (1, 2, 4),
        (1, 2),
    )
)

AUDITS = (
    Audit(
        "base",
        fee_bps=5.0,
        slippage_bps=3.0,
        funding_buffer_bps=10.0,
        forced_exit_extra_bps=10.0,
    ),
    Audit(
        "severe",
        fee_bps=5.5,
        slippage_bps=6.0,
        funding_buffer_bps=20.0,
        forced_exit_extra_bps=20.0,
    ),
    Audit(
        "extreme",
        fee_bps=7.5,
        slippage_bps=10.0,
        funding_buffer_bps=30.0,
        forced_exit_extra_bps=35.0,
    ),
    Audit(
        "delay_1h",
        fee_bps=5.0,
        slippage_bps=3.0,
        funding_buffer_bps=10.0,
        forced_exit_extra_bps=10.0,
        execution_delay_hours=1,
    ),
)

GROSS = 0.50
LEG_GROSS = GROSS / 2.0
EXIT_ABS_Z = 0.50
MAX_ABS_SPREAD_BPS = 300.0
INITIAL_EQUITY = 10_000.0

PREFINAL_GATES = {
    "cagr_min": 0.05,
    "sharpe_min": 0.75,
    "max_drawdown_min": -0.12,
    "all_years_positive": True,
    "annual_turnover_max": 30.0,
}

POST_SELECTION_GATES = {
    "severe_full_cagr_positive": True,
    "worst_year_min": -0.08,
    "final_return_positive": True,
}
