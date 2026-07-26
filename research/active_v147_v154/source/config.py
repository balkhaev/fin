from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

START_YEAR = 2004
END_YEAR = 2026
RESEARCH_END = "2026-07-01"
MONTH_CODES = "FGHJKMNQUVXZ"

PERIODS = {
    "development_2006_2010": ("2006-01-01", "2011-01-01"),
    "validation_2011_2014": ("2011-01-01", "2015-01-01"),
    "validation_2015_2018": ("2015-01-01", "2019-01-01"),
    "bridge_2019_2020": ("2019-01-01", "2021-01-01"),
    "holdout_2021_2023": ("2021-01-01", "2024-01-01"),
    "holdout_2024_2025": ("2024-01-01", "2026-01-01"),
    "final_2026h1": ("2026-01-01", RESEARCH_END),
}
SELECTION_END = "2021-01-01"

ARCHIVE_TEMPLATE = (
    "https://cdn.cboe.com/resources/futures/archive/volume-and-price/"
    "CFE_{month_code}{year2}_VX.csv"
)
MODERN_TEMPLATE = (
    "https://cdn.cboe.com/data/us/futures/market_statistics/"
    "historical_data/VX/VX_{expiry}.csv"
)
VIX_SPOT_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"

# Contract-level economics used for feasibility diagnostics. Historical
# strategies are simulated in notional weights; VXM integer sizing is audited
# separately because VXM was not available for the whole sample.
VX_MULTIPLIER = 1000.0
VXM_MULTIPLIER = 100.0
VXM_AVAILABLE_FROM = "2020-08-10"
ROLL_DAYS_BEFORE_EXPIRY = 5
FORCED_EXIT_PENALTY = 0.01


@dataclass(frozen=True)
class Audit:
    name: str
    cost_bps_per_side: float
    initial_margin_ratio: float
    maintenance_margin_ratio: float
    operational_reserve: float
    execution_delay_days: int = 0
    spread_widen_multiplier: float = 1.0


AUDITS = (
    Audit("base", 20.0, 0.50, 0.30, 0.10),
    Audit("stress", 40.0, 0.60, 0.35, 0.12),
    Audit("severe", 80.0, 0.70, 0.40, 0.15, 1, 1.5),
    Audit("extreme", 150.0, 0.80, 0.50, 0.20, 2, 2.0),
)


@dataclass(frozen=True)
class Policy:
    name: str
    family: str
    threshold: float
    budget: float
    hold_days: int
    band: float


def policies() -> tuple[Policy, ...]:
    result: list[Policy] = []
    for budget in (0.05, 0.10, 0.15):
        for threshold in (0.00, 0.02, 0.05):
            for hold in (3, 7, 14):
                result.append(
                    Policy(
                        f"backwardation_t{int(threshold*100):02d}_b{int(budget*100):02d}_h{hold}",
                        "backwardation_long",
                        threshold,
                        budget,
                        hold,
                        0.0,
                    )
                )
                result.append(
                    Policy(
                        f"curve_spread_t{int(threshold*100):02d}_b{int(budget*100):02d}_h{hold}",
                        "curve_spread",
                        threshold,
                        budget,
                        hold,
                        0.0,
                    )
                )
    for budget in (0.05, 0.10, 0.15):
        for spike in (0.10, 0.20, 0.30):
            for hold in (3, 7, 14):
                result.append(
                    Policy(
                        f"spot_spike_t{int(spike*100):02d}_b{int(budget*100):02d}_h{hold}",
                        "spot_spike_long",
                        spike,
                        budget,
                        hold,
                        0.0,
                    )
                )
    for budget in (0.05, 0.10, 0.15):
        for percentile in (0.80, 0.90, 0.95):
            for hold in (3, 7, 14):
                result.append(
                    Policy(
                        f"tail_t{int(percentile*100):02d}_b{int(budget*100):02d}_h{hold}",
                        "tail_long",
                        percentile,
                        budget,
                        hold,
                        0.0,
                    )
                )
    return tuple(result)


POLICIES = policies()

# Frozen standalone gates. They are intentionally modest because the sleeve is
# expected to be sparse and defensive, but it must have independent value.
STANDALONE_GATES = {
    "prefinal_cagr_min": 0.02,
    "prefinal_sharpe_min": 0.50,
    "prefinal_max_drawdown_min": -0.15,
    "annual_turnover_max": 12.0,
    "validation_2011_2014_min": 0.0,
    "validation_2015_2018_min": 0.0,
    "bridge_2019_2020_min": -0.02,
    "stress_prefinal_cagr_min": 0.0,
    "stress_prefinal_max_drawdown_min": -0.20,
}

POST_SELECTION_GATES = {
    "holdout_2021_2023_min": 0.0,
    "holdout_2024_2025_min": 0.0,
    "final_2026h1_min": 0.0,
    "best_year_positive_log_share_max": 0.60,
}

INTEGRATION_WEIGHTS = (0.05, 0.10, 0.15)


def output_paths(root: Path) -> dict[str, Path]:
    return {
        "processed": root / "inputs" / "processed",
        "results": root / "results",
        "provenance": root / "inputs" / "provenance",
    }
