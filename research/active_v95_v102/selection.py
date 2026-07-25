from __future__ import annotations

import itertools
import pandas as pd

from config import CandidateSpec, STANDALONE_GATES
from engine import normalize_targets, scheduled_targets, volatility_scale


def candidate_specs(processes: dict[str, pd.DataFrame]):
    for family in processes:
        for target_vol, gross_cap, rebalance, band in itertools.product(
            (0.08, 0.10, 0.12),
            (0.75, 1.00, 1.25),
            (5, 10, 20),
            (0.05, 0.10),
        ):
            yield CandidateSpec(family, target_vol, gross_cap, rebalance, band)


def candidate_id(spec: CandidateSpec) -> str:
    return (
        f"{spec.family}__vol{int(spec.target_vol * 100):02d}"
        f"__cap{int(spec.gross_cap * 100):03d}"
        f"__r{spec.rebalance_days}__b{int(spec.no_trade_band * 100):02d}"
    )


def build_target(
    raw: pd.DataFrame,
    groups: dict[str, str],
    spec: CandidateSpec,
    returns: pd.DataFrame,
) -> pd.DataFrame:
    target = normalize_targets(raw, groups, spec.gross_cap)
    target = scheduled_targets(target, spec.rebalance_days, spec.no_trade_band)
    return volatility_scale(target, returns, spec.target_vol, spec.gross_cap)


def selection_decision(
    table: pd.DataFrame,
    candidate: str,
    selection_periods: tuple[str, ...],
) -> tuple[bool, dict]:
    stress = table[(table.candidate == candidate) & (table.scenario == "stress")]
    severe = table[(table.candidate == candidate) & (table.scenario == "severe")]
    periods = {p: stress[stress.period == p].iloc[0] for p in selection_periods}
    selection = stress[stress.period == "selection_2008_2020"].iloc[0]
    severe_periods = severe[severe.period.isin(selection_periods)]
    checks = {
        "development_positive": float(periods["development"].total_return) > 0.0,
        "validation_a_positive": float(periods["validation_a"].total_return) > 0.0,
        "validation_b_positive": float(periods["validation_b"].total_return) > 0.0,
        "selection_cagr_min": float(selection.annualized_return)
        >= STANDALONE_GATES["selection_cagr_min"],
        "selection_sharpe_min": float(selection.sharpe)
        >= STANDALONE_GATES["selection_sharpe_min"],
        "selection_max_drawdown_min": float(selection.max_drawdown)
        >= STANDALONE_GATES["selection_max_drawdown_min"],
        "selection_turnover_max": float(selection.annual_turnover)
        <= STANDALONE_GATES["selection_turnover_max"],
        "worst_severe_period_min": float(severe_periods.total_return.min())
        >= STANDALONE_GATES["worst_severe_period_min"],
    }
    score = float(
        selection.annualized_return
        + 0.08 * selection.sharpe
        + 0.10 * min(float(row.total_return) for row in periods.values())
        - 0.12 * abs(selection.max_drawdown)
        - 0.001 * selection.annual_turnover
    )
    return all(checks.values()), {"checks": checks, "score": score}
