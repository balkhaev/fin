from __future__ import annotations

from dataclasses import dataclass
from itertools import product

ASSETS = ("BTC", "ETH")
USD_M_SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
COIN_M_SYMBOLS = {"BTC": "BTCUSD_PERP", "ETH": "ETHUSD_PERP"}

START = "2021-01-01 00:00:00"
DEVELOPMENT_END = "2023-12-31 23:59:59"
VALIDATION_START = "2024-01-01 00:00:00"
VALIDATION_END = "2024-12-31 23:59:59"
HOLDOUT_START = "2025-01-01 00:00:00"
HOLDOUT_END = "2025-12-31 23:59:59"
FINAL_START = "2026-01-01 00:00:00"
END = "2026-06-30 23:59:59"

INITIAL_EQUITY = 10_000.0
TARGET_GROSS = 0.25
BAR_HOURS = 1
ROLLING_MIN_FRACTION = 1.0
MIN_MONTH_ARCHIVE_SHARE = 0.95
MIN_PRICE_COVERAGE = 0.98

FAMILIES = (
    "dual_perp_basis_convergence",
    "funding_spread_carry",
    "funding_basis_joint",
    "reversed_dual_perp_control",
)
PROMOTABLE_FAMILIES = FAMILIES[:-1]


@dataclass(frozen=True, slots=True)
class Policy:
    family: str
    lookback_hours: int
    entry_abs_z: float
    minimum_expected_edge_bps: float
    hold_hours: int

    @property
    def name(self) -> str:
        return (
            f"{self.family}_l{self.lookback_hours}_z{self.entry_abs_z:g}_"
            f"e{self.minimum_expected_edge_bps:g}_h{self.hold_hours}"
        )


@dataclass(frozen=True, slots=True)
class Audit:
    name: str
    pair_round_trip_bps: float
    execution_delay_hours: int = 0
    forced_exit_extra_bps: float = 25.0

    @property
    def one_way_rate(self) -> float:
        return self.pair_round_trip_bps / 2.0 / 10_000.0


POLICIES = tuple(
    Policy(*values)
    for values in product(
        FAMILIES,
        (168, 336),
        (2.0, 3.0, 4.0),
        (10.0, 20.0),
        (8, 24, 72),
    )
)

AUDITS = (
    Audit("base", pair_round_trip_bps=24.0),
    Audit("severe", pair_round_trip_bps=40.0),
    Audit("extreme", pair_round_trip_bps=70.0),
    Audit("delay_1h", pair_round_trip_bps=24.0, execution_delay_hours=1),
)

DEVELOPMENT_GATES = {
    "cagr_min": 0.03,
    "sharpe_min": 0.80,
    "max_drawdown_min": -0.08,
    "closed_trades_min": 30,
    "annual_turnover_max": 50.0,
    "all_development_years_positive": True,
    "btc_pnl_positive": True,
    "eth_pnl_positive": True,
}

POST_SELECTION_GATES = {
    "validation_return_positive": True,
    "holdout_return_positive": True,
    "final_return_positive": True,
    "severe_full_cagr_positive": True,
    "latency_full_cagr_positive": True,
    "worst_calendar_year_min": -0.08,
    "zero_unplanned_forced_exits": True,
}
