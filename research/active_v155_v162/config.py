from __future__ import annotations

from dataclasses import dataclass

SELECTION_END = "2021-01-01"
PERIODS = {
    "development_2006_2010": ("2006-01-01", "2011-01-01"),
    "validation_2011_2014": ("2011-01-01", "2015-01-01"),
    "validation_2015_2018": ("2015-01-01", "2019-01-01"),
    "bridge_2019_2020": ("2019-01-01", "2021-01-01"),
    "holdout_2021_2023": ("2021-01-01", "2024-01-01"),
    "holdout_2024_2025": ("2024-01-01", "2026-01-01"),
    "final_2026h1": ("2026-01-01", "2026-07-01"),
}


@dataclass(frozen=True)
class Policy:
    name: str
    family: str
    carry_budget: float
    convex_budget: float
    contango_threshold: float
    crisis_threshold: float
    spike_threshold: float
    confirm_days: int
    hold_days: int


def build_policies() -> tuple[Policy, ...]:
    result: list[Policy] = []
    # Calendar carry only: short front / long second, zero outright notional.
    for budget in (0.04, 0.08, 0.12):
        for contango in (0.02, 0.04, 0.06):
            for confirm in (1, 3):
                for hold in (3, 7):
                    result.append(
                        Policy(
                            f"carry_b{int(budget*100):02d}_c{int(contango*100):02d}_q{confirm}_h{hold}",
                            "carry_only",
                            budget,
                            0.0,
                            contango,
                            0.0,
                            0.15,
                            confirm,
                            hold,
                        )
                    )
    # Regime switch: carry in calm contango, long front in crisis.
    for carry in (0.04, 0.08):
        for convex in (0.04, 0.08):
            for contango in (0.02, 0.04, 0.06):
                for spike in (0.10, 0.20):
                    for hold in (3, 7):
                        result.append(
                            Policy(
                                f"switch_ca{int(carry*100):02d}_cv{int(convex*100):02d}_c{int(contango*100):02d}_s{int(spike*100):02d}_h{hold}",
                                "carry_convex_switch",
                                carry,
                                convex,
                                contango,
                                0.0,
                                spike,
                                2,
                                hold,
                            )
                        )
    # Defensive switch: only small carry; larger convex response in backwardation.
    for carry in (0.02, 0.04):
        for convex in (0.06, 0.10):
            for contango in (0.03, 0.05):
                for backwardation in (0.00, 0.03):
                    for hold in (3, 7):
                        result.append(
                            Policy(
                                f"defensive_ca{int(carry*100):02d}_cv{int(convex*100):02d}_c{int(contango*100):02d}_b{int(backwardation*100):02d}_h{hold}",
                                "defensive_switch",
                                carry,
                                convex,
                                contango,
                                backwardation,
                                0.15,
                                2,
                                hold,
                            )
                        )
    return tuple(result)


POLICIES = build_policies()

# Standalone gates are fixed before reading 2021+ outcomes.
STANDALONE_GATES = {
    "prefinal_cagr_min": 0.025,
    "prefinal_sharpe_min": 0.60,
    "prefinal_max_drawdown_min": -0.15,
    "annual_turnover_max": 15.0,
    "validation_2011_2014_min": 0.0,
    "validation_2015_2018_min": 0.0,
    "bridge_2019_2020_min": 0.0,
    "stress_prefinal_cagr_min": 0.0,
    "stress_prefinal_max_drawdown_min": -0.20,
    "synthetic_overnight_loss_min": -0.075,
}

POST_SELECTION_GATES = {
    "holdout_2021_2023_min": 0.0,
    "holdout_2024_2025_min": 0.0,
    "final_2026h1_min": 0.0,
    "severe_full_cagr_min": 0.0,
    "worst_year_min": -0.10,
    "best_year_positive_log_share_max": 0.60,
}

INTEGRATION_WEIGHTS = (0.05, 0.10, 0.15)
