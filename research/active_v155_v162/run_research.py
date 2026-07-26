#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from config import INTEGRATION_WEIGHTS, PERIODS, POLICIES
from evaluation import (
    AUDIT_SET,
    BASE,
    SEVERE,
    V154_ENGINE,
    ensemble_target,
    evaluate_grid,
    post_checks,
)
from loader import HERE, REPO, load_atlas, load_market, synthetic_market
from strategy import self_test as strategy_self_test
from strategy import synthetic_overnight_audit


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_v75_metrics() -> dict:
    path = REPO / "docs" / "checkpoints" / "v138" / "CHECKPOINT_V138.json"
    value = json.loads(path.read_text())["original_v75"]
    return {
        "total_return": value["total_return"],
        "annualized_return": value["cagr"],
        "max_drawdown": value["max_drawdown"],
        "sharpe": value["sharpe"],
        "annual_turnover": value["annual_turnover"],
        "average_gross": value["average_gross"],
        "max_gross": value["max_gross"],
    }


def annual_returns(account: pd.DataFrame, label: str) -> pd.DataFrame:
    return V154_ENGINE.annual_returns(account, label)


def combine(atlas: pd.DataFrame, sleeve: pd.DataFrame, weight: float) -> pd.DataFrame:
    return V154_ENGINE.combine_separate_accounts(atlas, sleeve, weight, transfer_bps=5.0)


def choose_integration(
    atlas: pd.DataFrame,
    sleeve: pd.DataFrame,
    output: Path,
) -> tuple[float, pd.DataFrame]:
    rows = []
    for weight in INTEGRATION_WEIGHTS:
        account = combine(atlas, sleeve, weight)
        selection = V154_ENGINE.period(account, "2021-01-01", "2024-01-01")
        rows.append(
            {
                "sleeve_weight": weight,
                **selection,
                "score": selection["sharpe"]
                + 0.5 * selection["annualized_return"]
                + selection["max_drawdown"],
            }
        )
    frame = pd.DataFrame(rows).sort_values("score", ascending=False)
    frame.to_csv(output, index=False)
    return float(frame.iloc[0]["sleeve_weight"]), frame


def integration_checks(
    atlas: pd.DataFrame,
    account: pd.DataFrame,
) -> dict[str, bool]:
    aligned = atlas.reindex(account.index).dropna()
    control = account.reindex(aligned.index)
    atlas_metrics = V154_ENGINE.metrics(aligned)
    candidate_metrics = V154_ENGINE.metrics(control)
    holdout = V154_ENGINE.period(account, "2024-01-01", "2026-01-01")
    final = V154_ENGINE.period(account, "2026-01-01", "2026-07-01")
    return {
        "holdout_2024_2025_positive": holdout["total_return"] > 0,
        "final_2026h1_positive": final["total_return"] > 0,
        "drawdown_not_worse_by_more_than_1pp": candidate_metrics["max_drawdown"]
        >= atlas_metrics["max_drawdown"] - 0.01,
        "cagr_not_lower_by_more_than_2pp": candidate_metrics["annualized_return"]
        >= atlas_metrics["annualized_return"] - 0.02,
    }


def vxm_feasibility(target: pd.DataFrame, account: pd.DataFrame, market) -> dict[str, float]:
    dates = target.index[target.index >= pd.Timestamp("2020-08-10")]
    dates = dates[target.loc[dates].abs().sum(axis=1) > 1e-12]
    fractional, weights = [], []
    for day in dates:
        price = float(market.features.at[day, "front_settle"])
        equity = float(account.at[day, "equity"])
        gross = float(target.loc[day].abs().sum())
        notional = price * 100.0
        fractional.append(gross * equity / max(notional, 1e-12))
        weights.append(notional / max(equity, 1e-12))
    if not fractional:
        return {
            "active_days": 0,
            "fraction_days_one_contract_feasible": 0.0,
            "median_fractional_contracts": 0.0,
            "median_one_contract_weight": 0.0,
        }
    return {
        "active_days": len(fractional),
        "fraction_days_one_contract_feasible": float((np.asarray(fractional) >= 1).mean()),
        "median_fractional_contracts": float(np.median(fractional)),
        "median_one_contract_weight": float(np.median(weights)),
    }


def block_bootstrap(returns: pd.Series, output: Path, seed: int = 155162) -> pd.DataFrame:
    values = returns.dropna().to_numpy(float)
    rng = np.random.default_rng(seed)
    rows = []
    for block in (10, 21, 63):
        for horizon in (252, 504):
            totals, dds = [], []
            for _ in range(3000):
                path: list[float] = []
                while len(path) < horizon:
                    start = int(rng.integers(0, max(1, len(values) - block + 1)))
                    path.extend(values[start : start + block])
                sample = np.asarray(path[:horizon])
                equity = np.cumprod(1.0 + sample)
                totals.append(float(equity[-1] - 1.0))
                dds.append(float(np.min(equity / np.maximum.accumulate(equity) - 1.0)))
            rows.append(
                {
                    "block_days": block,
                    "horizon_days": horizon,
                    "median_return": float(np.median(totals)),
                    "p05_return": float(np.quantile(totals, 0.05)),
                    "probability_positive": float(np.mean(np.asarray(totals) > 0)),
                    "median_max_drawdown": float(np.median(dds)),
                    "p05_max_drawdown": float(np.quantile(dds, 0.05)),
                }
            )
    frame = pd.DataFrame(rows)
    frame.to_csv(output, index=False)
    return frame


def report(summary: dict, annual: pd.DataFrame) -> str:
    sleeve = summary["selected_sleeve"]
    lines = [
        "# Active V155–V162 — VIX carry / convexity switch",
        "",
        f"Status: `{summary['status']}`",
        "",
        "## Результат",
        "",
        "| Candidate | CAGR | Total return | Max DD | Sharpe | Turnover |",
        "|---|---:|---:|---:|---:|---:|",
        f"| V75 original | {summary['original_v75']['annualized_return']:.2%} | {summary['original_v75']['total_return']:.2%} | {summary['original_v75']['max_drawdown']:.2%} | {summary['original_v75']['sharpe']:.3f} | {summary['original_v75']['annual_turnover']:.2f}x |",
        f"| V160 carry/convex sleeve | {sleeve['full']['annualized_return']:.2%} | {sleeve['full']['total_return']:.2%} | {sleeve['full']['max_drawdown']:.2%} | {sleeve['full']['sharpe']:.3f} | {sleeve['full']['annual_turnover']:.2f}x |",
        "",
        "## Frozen decision",
        "",
        f"- policies: `{summary['selection']['policy_count']}`;",
        f"- eligible before 2021: `{summary['selection']['eligible_policy_count']}`;",
        f"- standalone passed: `{summary['standalone_selection_passed']}`;",
        f"- integration permitted: `{summary['integration_permitted']}`;",
        f"- promoted candidates: `{summary['promoted_candidates']}`;",
        "- `live_ready=false`;",
        "- `real_leverage_authorized=false`.",
        "",
        "## Годовая доходность",
        "",
        "| Год | V75 original | V160 carry/convex | Integrated |",
        "|---:|---:|---:|---:|",
    ]
    for _, row in annual.iterrows():
        def value(column: str) -> str:
            item = row.get(column)
            return "—" if pd.isna(item) else f"{item:+.2%}"
        lines.append(
            f"| {int(row['year'])} | {value('V75_original')} | "
            f"{value('V160_carry_convex')} | {value('V161_integrated')} |"
        )
    lines += [
        "",
        "## Evidence limits",
        "",
        "- Гипотеза создана после просмотра провала V154 после 2020 года; program-level holdout не pristine.",
        "- Official Cboe settlements не являются broker fill feed.",
        "- Calendar spread уменьшает outright vega, но не устраняет gap и margin risk.",
        "- Ни live trading, ни реальное плечо не разрешены.",
    ]
    return "\n".join(lines) + "\n"


def self_test() -> None:
    market = synthetic_market()
    strategy_self_test(synthetic_market)
    target = ensemble_target(list(POLICIES[:3]), market)
    account = V154_ENGINE.simulate(market, target, BASE)
    assert len(account) == len(market.index)
    assert account["equity"].gt(0).all()
    assert float(target.abs().sum(axis=1).max()) <= 0.12 + 1e-12
    print("V155-V162 self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=HERE / "results")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    output = args.output
    output.mkdir(parents=True, exist_ok=True)

    market = load_market()
    atlas = load_atlas()
    selected, ranking = evaluate_grid(market, output)
    target = ensemble_target(selected, market)
    target.to_csv(output / "selected_target_weights.csv")
    shock = synthetic_overnight_audit(target, market)

    accounts = {}
    audit_rows = []
    for audit in AUDIT_SET:
        account = V154_ENGINE.simulate(market, target, audit)
        accounts[audit.name] = account
        account.to_csv(output / f"equity_{audit.name}.csv")
        for name, bounds in PERIODS.items():
            audit_rows.append({"audit": audit.name, "period": name, **V154_ENGINE.period(account, *bounds)})
        audit_rows.append({"audit": audit.name, "period": "full", **V154_ENGINE.metrics(account)})
    pd.DataFrame(audit_rows).to_csv(output / "audit_metrics.csv", index=False)

    base, severe = accounts["base"], accounts["severe"]
    post = post_checks(base, severe)
    eligible_count = int(ranking["eligible_before_2021"].sum())
    standalone_pass = bool(eligible_count > 0 and all(post.values()))

    weight = None
    integrated = None
    int_checks = {}
    if standalone_pass:
        weight, _ = choose_integration(atlas, base, output / "integration_weight_selection_2021_2023.csv")
        integrated = combine(atlas, base, weight)
        integrated.to_csv(output / "integrated_equity.csv")
        int_checks = integration_checks(atlas, integrated)
    integration_pass = bool(int_checks and all(int_checks.values()))

    annual = annual_returns(atlas, "V75_original").merge(
        annual_returns(base, "V160_carry_convex"), on="year", how="outer"
    )
    if integrated is not None:
        annual = annual.merge(annual_returns(integrated, "V161_integrated"), on="year", how="outer")
    else:
        annual["V161_integrated"] = np.nan
    annual = annual.sort_values("year")
    annual.to_csv(output / "ANNUAL_RETURNS.csv", index=False)
    block_bootstrap(base["daily_return"], output / "block_bootstrap.csv")

    selected_metrics = {
        "full": V154_ENGINE.metrics(base),
        "prefinal_2006_2020": V154_ENGINE.period(base, "2006-01-01", "2021-01-01"),
        "holdout_2021_2023": V154_ENGINE.period(base, *PERIODS["holdout_2021_2023"]),
        "holdout_2024_2025": V154_ENGINE.period(base, *PERIODS["holdout_2024_2025"]),
        "final_2026h1": V154_ENGINE.period(base, *PERIODS["final_2026h1"]),
        "severe_full": V154_ENGINE.metrics(severe),
        "synthetic_overnight_audit": shock,
        "post_selection_checks": post,
        "vxm_integer_feasibility": vxm_feasibility(target, base, market),
    }
    status = (
        "historical_composite_candidate_needs_forward"
        if integration_pass
        else "standalone_historical_candidate_needs_forward"
        if standalone_pass
        else "rejected_or_needs_iteration"
    )
    summary = {
        "candidate": "ACTIVE_V155_V162_VIX_CARRY_CONVEXITY",
        "status": status,
        "live_ready": False,
        "real_leverage_authorized": False,
        "original_v75": canonical_v75_metrics(),
        "selection": {
            "selection_cutoff": "2020-12-31",
            "selection_uses_2021_or_later": False,
            "hypothesis_inspired_by_post_2020_v154_failure": True,
            "policy_count": len(ranking),
            "eligible_policy_count": eligible_count,
            "selected": [asdict(policy) for policy in selected],
            "selection_proof_sha256": sha256(output / "selection_proof_before_2021.json"),
        },
        "selected_sleeve": selected_metrics,
        "standalone_selection_passed": standalone_pass,
        "integration_permitted": standalone_pass,
        "integration_weight": weight,
        "integration_checks": int_checks,
        "integration_passed": integration_pass,
        "promoted_candidates": (
            ["V161_integrated"] if integration_pass else ["V160_carry_convex"] if standalone_pass else []
        ),
        "source_data": {
            "contracts_sha256": sha256(REPO / "research" / "active_v147_v154" / "inputs" / "processed" / "vx_monthly_contracts.csv"),
            "features_sha256": sha256(REPO / "research" / "active_v147_v154" / "inputs" / "processed" / "vx_term_structure_daily.csv"),
            "official_contract_rows": 44997,
            "official_expiries": 264,
        },
        "evidence_limits": {
            "program_level_holdout_pristine": False,
            "historical_bid_ask_available": False,
            "historical_broker_margin_available": False,
            "real_execution_authorized": False,
        },
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, default=float) + "\n")
    (output / "FROZEN_DECISION.json").write_text(json.dumps(summary, indent=2, default=float) + "\n")
    (output / "REPORT_RU.md").write_text(report(summary, annual))
    (HERE / "ANNUAL_RETURNS.csv").write_text((output / "ANNUAL_RETURNS.csv").read_text())
    print(json.dumps(summary, indent=2, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
