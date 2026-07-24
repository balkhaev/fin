#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

WEIGHTS = (0.20, 0.30, 0.40, 0.50)
PREFINAL_PERIODS = ("development", "validation_a", "validation_b", "bridge_2025")


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Frozen Active V10 capital-sleeve allocation")
    parser.add_argument("--v8-root", type=Path)
    parser.add_argument("--v6", type=Path)
    parser.add_argument("--v5", type=Path)
    parser.add_argument("--v4-signal", type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts/active_v10"))
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def read_signal(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    frame.index = pd.to_datetime(frame.index, utc=True)
    return frame


def combine_accounts(v8: pd.DataFrame, v4: pd.DataFrame) -> pd.DataFrame:
    index = v8.index.intersection(v4.index)
    v8, v4 = v8.loc[index], v4.loc[index]
    equity = v8["equity"] + v4["equity"]
    v8_weight = v8["equity"] / equity
    v4_weight = v4["equity"] / equity
    result = pd.DataFrame(index=index)
    result["equity"] = equity
    result["gross"] = v8["gross"] * v8_weight + v4["gross"] * v4_weight
    result["turnover"] = v8["turnover"] * v8_weight + v4["turnover"] * v4_weight
    result["costs"] = v8["costs"] + v4["costs"]
    result["funding_pnl"] = v8["funding_pnl"] + v4["funding_pnl"]
    result["risk_scale"] = v8["risk_scale"]
    result["spot_gross"] = v8["spot_gross"] * v8_weight + v4["spot_gross"] * v4_weight
    result["perp_gross"] = v8["perp_gross"] * v8_weight
    result["high_water"] = equity.cummax()
    return result


def self_test() -> None:
    index = pd.date_range("2025-01-01", periods=4, freq="1D", tz="UTC")
    left = pd.DataFrame({
        "equity": [8000.0, 8080.0, 8000.0, 8160.0],
        "gross": [0.4] * 4,
        "turnover": [0.2] * 4,
        "costs": [2.0] * 4,
        "funding_pnl": [1.0] * 4,
        "risk_scale": [1.5] * 4,
        "spot_gross": [0.3] * 4,
        "perp_gross": [0.1] * 4,
    }, index=index)
    right = pd.DataFrame({
        "equity": [2000.0, 2020.0, 2040.0, 2060.0],
        "gross": [0.1] * 4,
        "turnover": [0.05] * 4,
        "costs": [0.5] * 4,
        "funding_pnl": [0.0] * 4,
        "risk_scale": [1.0] * 4,
        "spot_gross": [0.1] * 4,
        "perp_gross": [0.0] * 4,
    }, index=index)
    combined = combine_accounts(left, right)
    assert np.allclose(combined["equity"], left["equity"] + right["equity"])
    assert float(combined["gross"].max()) < 0.4
    changed = right.copy()
    changed.iloc[-1, changed.columns.get_loc("equity")] *= 2.0
    second = combine_accounts(left, changed)
    pd.testing.assert_frame_equal(combined.iloc[:-1], second.iloc[:-1])
    print("self-test passed")


def main() -> int:
    parsed = arguments()
    if parsed.self_test:
        self_test()
        return 0
    required = (parsed.v8_root, parsed.v6, parsed.v5, parsed.v4_signal)
    if any(path is None for path in required):
        raise SystemExit("--v8-root, --v6, --v5 and --v4-signal are required")

    canonical = parsed.v8_root / "canonical"
    sys.path.insert(0, str(canonical))
    from config import FORCED_DELISTING_PENALTY_BPS, PERIODS, RATCHET, SCENARIOS, TARGET_GROSS_CAP
    from engine import SimulationSettings, metrics, rolling_diagnostics, simulate
    from inputs import load

    output = parsed.output
    output.mkdir(parents=True, exist_ok=True)
    data, _, perp_open, perp_close, funding = load(parsed.v6, parsed.v5)
    spot = read_signal(parsed.v8_root / "results" / "spot_signal.csv").reindex(index=data.index, columns=data.symbols).fillna(0.0)
    perp = read_signal(parsed.v8_root / "results" / "combined_perp_signal.csv").reindex(index=data.index, columns=perp_open.columns).fillna(0.0)
    raw_v4 = read_signal(parsed.v4_signal)
    v4 = pd.DataFrame(0.0, index=data.index, columns=data.symbols)
    for column in raw_v4.columns.intersection(v4.columns):
        v4[column] = raw_v4[column].reindex(data.index).fillna(0.0)
    zero_perp = perp * 0.0

    def v8_settings(starting_equity: float) -> SimulationSettings:
        return SimulationSettings(
            starting_equity=starting_equity,
            target_gross_cap=TARGET_GROSS_CAP,
            initial_scale=RATCHET["initial_scale"],
            first_high_water_multiple=RATCHET["first_high_water_multiple"],
            first_reduced_scale=RATCHET["first_reduced_scale"],
            second_high_water_multiple=RATCHET["second_high_water_multiple"],
            second_reduced_scale=RATCHET["second_reduced_scale"],
            ratchet=True,
        )

    def v4_settings(starting_equity: float) -> SimulationSettings:
        return SimulationSettings(starting_equity=starting_equity, target_gross_cap=0.85)

    def run(signal: pd.DataFrame, perp_signal: pd.DataFrame, start: str, end: str,
            scenario: str, settings: SimulationSettings) -> pd.DataFrame:
        costs = SCENARIOS[scenario]
        return simulate(
            data, signal, perp_open, perp_close, funding, perp_signal, start, end,
            spot_cost_rate=costs["spot_cost_bps"] / 10_000.0,
            perp_cost_rate=costs["perp_cost_bps"] / 10_000.0,
            forced_penalty_rate=FORCED_DELISTING_PENALTY_BPS / 10_000.0,
            settings=settings,
        )

    prefinal_rows: list[dict[str, object]] = []
    for weight in WEIGHTS:
        for scenario in ("stress", "severe"):
            for period in PREFINAL_PERIODS:
                start, end = PERIODS[period]
                account_v8 = run(spot, perp, start, end, scenario, v8_settings(10_000 * (1 - weight)))
                account_v4 = run(v4, zero_perp, start, end, scenario, v4_settings(10_000 * weight))
                account = combine_accounts(account_v8, account_v4)
                prefinal_rows.append({"v4_weight": weight, "scenario": scenario, "period": period, **metrics(account), **rolling_diagnostics(account)})
    prefinal = pd.DataFrame(prefinal_rows)
    prefinal.to_csv(output / "prefinal_metrics.csv", index=False)

    baseline_rows = []
    for scenario in ("stress", "severe"):
        for period in PREFINAL_PERIODS:
            start, end = PERIODS[period]
            baseline = run(spot, perp, start, end, scenario, v8_settings(10_000))
            baseline_rows.append({"scenario": scenario, "period": period, **metrics(baseline)})
    baseline = pd.DataFrame(baseline_rows)
    baseline_median_annualized = float(baseline[baseline.scenario == "stress"].annualized_return.median())

    ranking_rows = []
    for weight in WEIGHTS:
        subset = prefinal[prefinal.v4_weight == weight]
        stress = subset[subset.scenario == "stress"]
        severe = subset[subset.scenario == "severe"]
        eligible = bool(stress.total_return.gt(0).all() and severe.total_return.min() > -0.10 and severe.max_drawdown.min() > -0.30 and stress.annualized_return.median() >= 0.85 * baseline_median_annualized and subset.annual_turnover.max() < 31.0)
        score = float(severe.max_drawdown.min() + 0.25 * severe.max_drawdown.median() + 0.10 * stress.sharpe.min() + 0.05 * stress.annualized_return.median() - 0.001 * subset.annual_turnover.max()) if eligible else -1e9
        ranking_rows.append({"v4_weight": weight, "eligible": eligible, "score": score, "stress_median_annualized": float(stress.annualized_return.median()), "stress_min_return": float(stress.total_return.min()), "severe_min_return": float(severe.total_return.min()), "severe_worst_drawdown": float(severe.max_drawdown.min()), "max_turnover": float(subset.annual_turnover.max())})
    ranking = pd.DataFrame(ranking_rows).sort_values("score", ascending=False)
    ranking.to_csv(output / "prefinal_ranking.csv", index=False)
    eligible = ranking[ranking.eligible]
    if eligible.empty:
        raise SystemExit("no allocation passed prefinal criteria")
    selected = float(eligible.iloc[0].v4_weight)
    (output / "selection.json").write_text(json.dumps({"selected_v4_weight": selected, "selection_excludes_2026h1": True}, indent=2), encoding="utf-8")

    final_rows = []
    for weight in WEIGHTS:
        for scenario in ("nominal", "stress", "severe"):
            for period in ("full", "final_2026h1"):
                start, end = PERIODS[period]
                account_v8 = run(spot, perp, start, end, scenario, v8_settings(10_000 * (1 - weight)))
                account_v4 = run(v4, zero_perp, start, end, scenario, v4_settings(10_000 * weight))
                account = combine_accounts(account_v8, account_v4)
                final_rows.append({"v4_weight": weight, "scenario": scenario, "period": period, **metrics(account), **rolling_diagnostics(account)})
    pd.DataFrame(final_rows).to_csv(output / "final_metrics.csv", index=False)
    print(json.dumps({"selected_v4_weight": selected}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
