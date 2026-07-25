from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from config import (
    BLEND_WEIGHTS,
    COST_SCENARIOS_BPS,
    ETF_GROUPS,
    PERIODS,
    SELECTION_PERIODS,
    CandidateSpec,
)
from data import load, load_atlas
from engine import combine_separate_accounts, simulate
from evaluation import evaluate_account, promotion_checks, safe_metrics, subset
from metrics import yearly_returns
from selection import build_target, candidate_id, candidate_specs, selection_decision
from signals import family_book, process_book


def self_test() -> None:
    index = pd.date_range("2010-01-01", periods=800, freq="B", tz="UTC")
    rng = np.random.default_rng(95)
    prices = pd.DataFrame(
        {
            "SPY": 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, len(index)))),
            "TLT": 100 * np.exp(np.cumsum(rng.normal(0.0001, 0.006, len(index)))),
            "GLD": 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.009, len(index)))),
            "FX_EUR": 1.2 * np.exp(np.cumsum(rng.normal(0.0, 0.004, len(index)))),
        },
        index=index,
    )
    groups = {"SPY": "equity", "TLT": "rates_credit", "GLD": "real_assets", "FX_EUR": "fx_spot"}
    processes = process_book(family_book(prices, groups))
    spec = CandidateSpec("synthetic", 0.10, 1.0, 10, 0.05)
    first = next(iter(processes.values()))
    target = build_target(first, groups, spec, prices.pct_change(fill_method=None))
    assert float(target.abs().sum(axis=1).max()) <= 1.0000001
    changed = prices.copy()
    changed.iloc[-1] *= 10.0
    second_process = next(iter(process_book(family_book(changed, groups)).values()))
    second = build_target(second_process, groups, spec, changed.pct_change(fill_method=None))
    pd.testing.assert_frame_equal(target.iloc[:-1], second.iloc[:-1], check_exact=False, rtol=1e-12, atol=1e-12)
    print("Active V95-V102 self-test passed")


def select_frozen_ensemble(table: pd.DataFrame, specs, targets):
    rows = []
    for spec in specs:
        cid = candidate_id(spec)
        eligible, info = selection_decision(table, cid, SELECTION_PERIODS)
        selection = table[
            (table.candidate == cid)
            & (table.scenario == "stress")
            & (table.period == "selection_2008_2020")
        ].iloc[0]
        rows.append(
            {
                "candidate": cid,
                "eligible_selection": eligible,
                "score": info["score"],
                **{f"check_{key}": value for key, value in info["checks"].items()},
                "selection_cagr": float(selection.annualized_return),
                "selection_sharpe": float(selection.sharpe),
                "selection_dd": float(selection.max_drawdown),
                "selection_turnover": float(selection.annual_turnover),
            }
        )
    ranking = pd.DataFrame(rows).sort_values(["eligible_selection", "score"], ascending=False)
    pool = ranking[ranking.eligible_selection]
    if pool.empty:
        pool = ranking.head(10)
    selected, used = [], set()
    for candidate in pool.candidate:
        family = str(candidate).split("__")[0]
        if family not in used or not selected:
            selected.append(str(candidate))
            used.add(family)
        if len(selected) == 3:
            break
    if not selected:
        selected = [str(ranking.iloc[0].candidate)]
    ensemble = sum(targets[candidate] for candidate in selected) / len(selected)
    return ranking, selected, ensemble


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--etf", type=Path)
    parser.add_argument("--fx", type=Path)
    parser.add_argument("--atlas", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if None in (args.etf, args.fx, args.atlas, args.output):
        raise SystemExit("all data and output paths are required")

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    market = load(args.etf, args.fx, ETF_GROUPS)
    atlas = load_atlas(args.atlas)
    processes = process_book(family_book(market.prices, market.groups))
    specs = list(candidate_specs(processes))
    rows, targets = [], {}

    for number, spec in enumerate(specs, 1):
        cid = candidate_id(spec)
        target = build_target(processes[spec.family], market.groups, spec, market.returns)
        targets[cid] = target
        for scenario in ("stress", "severe"):
            account = simulate(
                market.prices,
                market.returns,
                target,
                "2008-01-01",
                "2021-01-01",
                COST_SCENARIOS_BPS[scenario],
            )
            rows.extend(evaluate_account(account, cid, scenario, SELECTION_PERIODS))
            rows.append(
                {
                    "candidate": cid,
                    "scenario": scenario,
                    "period": "selection_2008_2020",
                    **safe_metrics(account),
                }
            )
        if number % 100 == 0:
            print("candidate", number, "/", len(specs), flush=True)

    selection_table = pd.DataFrame(rows)
    ranking, selected, ensemble = select_frozen_ensemble(selection_table, specs, targets)
    ranking.to_csv(output / "selection_ranking.csv", index=False)
    ensemble.to_csv(output / "v95_target_weights.csv")

    proof = {
        "candidate": "ACTIVE_V95_GLOBAL_CRISIS_ALPHA",
        "selection_end": "2020-12-31",
        "selection_uses_post_2020": False,
        "candidate_count": len(specs),
        "process_count": len(processes),
        "selected": selected,
        "ranking_top": ranking.head(50).to_dict(orient="records"),
    }
    proof_bytes = json.dumps(proof, indent=2, sort_keys=True).encode()
    proof_sha = hashlib.sha256(proof_bytes).hexdigest()
    (output / "selection_proof_before_post2020.json").write_bytes(proof_bytes)

    evaluation_rows, scenario_accounts = [], {}
    periods = tuple(PERIODS)
    for scenario, cost_bps in COST_SCENARIOS_BPS.items():
        account = simulate(market.prices, market.returns, ensemble, "2008-01-01", "2026-07-01", cost_bps)
        scenario_accounts[scenario] = account
        account.to_csv(output / f"v95_{scenario}_equity.csv")
        evaluation_rows.extend(evaluate_account(account, "V95", scenario, periods))
    evaluation = pd.DataFrame(evaluation_rows)
    evaluation.to_csv(output / "v95_metrics.csv", index=False)

    stress = scenario_accounts["stress"]
    checks, concentration = promotion_checks(evaluation, stress)
    standalone_pass = all(checks.values())
    blend_selected = None

    if standalone_pass:
        bridge_scores = []
        blend_rows = []
        blend_accounts = {}
        for weight in BLEND_WEIGHTS:
            combined = combine_separate_accounts(atlas, stress, weight)
            blend_accounts[weight] = combined
            bridge = safe_metrics(subset(combined, "bridge"))
            bridge_scores.append((weight, bridge["sharpe"] + 0.5 * bridge["annualized_return"] - 0.2 * abs(bridge["max_drawdown"])))
            for period in ("bridge", "holdout", "final_2026h1", "prefinal", "full"):
                sliced = subset(combined, period)
                if len(sliced) >= 20:
                    blend_rows.append({"weight": weight, "period": period, **safe_metrics(sliced)})
        blend_selected = max(bridge_scores, key=lambda item: item[1])[0]
        pd.DataFrame(blend_rows).to_csv(output / "v101_blend_metrics.csv", index=False)
        blend_accounts[blend_selected].to_csv(output / "v101_selected_equity.csv")

    yearly_returns(stress).to_csv(output / "v95_yearly.csv", index=False)
    aligned = pd.concat({"atlas": atlas.equity.pct_change(), "v95": stress.equity.pct_change()}, axis=1).dropna()
    correlation = float(aligned.corr().iloc[0, 1]) if len(aligned) > 20 else 0.0

    def get(scenario: str, period: str):
        return evaluation[(evaluation.scenario == scenario) & (evaluation.period == period)].iloc[0]

    summary = {
        "research": "ACTIVE_V95_V102_GLOBAL_CRISIS_ALPHA",
        "selection_proof_sha256": proof_sha,
        "selected_processes": selected,
        "standalone_status": "frozen_candidate" if standalone_pass else "rejected_or_needs_iteration",
        "standalone_checks": checks,
        "standalone_concentration": concentration,
        "stress_prefinal": {key: float(get("stress", "prefinal")[key]) for key in ("annualized_return", "total_return", "max_drawdown", "sharpe", "annual_turnover", "average_gross", "max_gross")},
        "stress_final_2026h1": {key: float(get("stress", "final_2026h1")[key]) for key in ("annualized_return", "total_return", "max_drawdown", "sharpe", "annual_turnover", "average_gross", "max_gross")},
        "strict_costs": {scenario: {"full_cagr": float(get(scenario, "full").annualized_return), "full_return": float(get(scenario, "full").total_return), "full_dd": float(get(scenario, "full").max_drawdown)} for scenario in COST_SCENARIOS_BPS},
        "atlas_daily_correlation": correlation,
        "blend_selected_weight": blend_selected,
        "live_ready": False,
        "real_leverage_authorized": False,
        "data_manifest": market.source_manifest,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    print(json.dumps(summary, indent=2, default=float))


if __name__ == "__main__":
    main()
