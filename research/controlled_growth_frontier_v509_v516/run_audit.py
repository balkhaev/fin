#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
V501_SOURCE = REPO_ROOT / "research" / "controlled_growth_v501_v508" / "run_research.py"
_SPEC = importlib.util.spec_from_file_location("v501_frontier_source", V501_SOURCE)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(V501_SOURCE)
v = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = v
_SPEC.loader.exec_module(v)

PROGRAM = "V509_V516_CONTROLLED_GROWTH_FRONTIER_AUDIT"
POLICY_NAMES = (
    "diversified_growth_k3_convex",
    "diversified_growth_k4_convex",
    "diversified_growth_k4_balanced",
    "diversified_growth_k4_growth",
)
TARGET_GATES = {
    "full_cagr_min": 0.50,
    "full_sharpe_min": 1.30,
    "full_max_drawdown_min": -0.25,
    "severe_full_cagr_min": 0.35,
    "extreme_full_cagr_min": 0.15,
    "worst_calendar_year_min": -0.10,
    "liquidations_max": 0,
    "minimum_margin_buffer": 0.10,
}


def policy_by_name(name: str) -> Any:
    return next(policy for policy in v.POLICIES if policy.name == name)


def self_test() -> None:
    assert len(POLICY_NAMES) == 4
    assert len(set(POLICY_NAMES)) == 4
    for name in POLICY_NAMES:
        policy = policy_by_name(name)
        assert policy.promotable
        assert policy.factor_mix == "diversified_growth"
    assert policy_by_name("diversified_growth_k3_convex").top_k == 3
    assert policy_by_name("diversified_growth_k4_growth").profile == "growth"
    print("V509-V516 exact frontier audit self-test passed")


def target_gate_results(
    base_full: dict[str, Any],
    severe_full: dict[str, Any],
    extreme_full: dict[str, Any],
    validation: dict[str, Any],
    holdout: dict[str, Any],
    final: dict[str, Any],
    annual: pd.DataFrame,
) -> dict[str, bool]:
    worst_year = float(annual["return"].min()) if not annual.empty else -1.0
    return {
        "validation_positive": float(validation["total_return"]) > 0.0,
        "holdout_positive": float(holdout["total_return"]) > 0.0,
        "final_positive": float(final["total_return"]) > 0.0,
        "full_cagr": float(base_full["cagr"]) >= TARGET_GATES["full_cagr_min"],
        "full_sharpe": float(base_full["sharpe"]) >= TARGET_GATES["full_sharpe_min"],
        "full_max_drawdown": float(base_full["max_drawdown"])
        >= TARGET_GATES["full_max_drawdown_min"],
        "severe_full_cagr": float(severe_full["cagr"])
        >= TARGET_GATES["severe_full_cagr_min"],
        "extreme_full_cagr": float(extreme_full["cagr"])
        >= TARGET_GATES["extreme_full_cagr_min"],
        "worst_calendar_year": worst_year >= TARGET_GATES["worst_calendar_year_min"],
        "liquidations": int(base_full["liquidations"]) <= TARGET_GATES["liquidations_max"],
        "margin_buffer": float(base_full["minimum_margin_buffer"])
        >= TARGET_GATES["minimum_margin_buffer"],
    }


def run(root: Path, cache: Path, state_path: Path) -> int:
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
    config = v.base.V9Config(
        symbols=v.SYMBOLS,
        start="2020-01-01",
        end_exclusive=v.END_EXCLUSIVE,
        interval="1d",
        starting_equity=v.INITIAL_EQUITY,
        max_gross=1.70,
        forced_exit_penalty_bps=v.FORCED_EXIT_PENALTY_BPS,
    )
    klines, funding, records, quality = v.base.load_v9(config, cache, False)
    pd.DataFrame(records).to_csv(results / "data_manifest.csv", index=False)
    pd.DataFrame(quality).to_csv(results / "data_quality.csv", index=False)
    coverage = v.base.data_gate(klines, records)
    coverage["candidate"] = "V509_FIXED_UNIVERSE_DATA_COVERAGE"
    v.write_json(results / "coverage_gate.json", coverage)
    if not coverage["passed"]:
        summary = {
            "program": PROGRAM,
            "status": "data_access_insufficient",
            "selection_performed": False,
            "integration_permitted": False,
            "capital_change_authorized": False,
            "live_ready": False,
            "real_leverage_authorized": False,
            "profitability_proven": False,
        }
        v.write_json(results / "summary.json", summary)
        (results / "REPORT_RU.md").write_text("# V509–V516\n\nData gate failed.\n")
        return 0

    market = v.base.Market(klines, funding)
    state = v.load_state(state_path, market.index)
    factors = v.factor_book(market)
    score = v.composite_score(factors, "diversified_growth")
    rows: list[dict[str, Any]] = []
    policy_summaries: dict[str, Any] = {}
    annual_frames: list[pd.DataFrame] = []

    for policy_name in POLICY_NAMES:
        policy = policy_by_name(policy_name)
        profile = v.PROFILES[policy.profile]
        raw = v.build_raw_long_weights(market, score, policy.factor_mix, policy.top_k)
        unit = v.schedule_weights(raw, market.available, profile.signal_rebalance_days)
        budget = v.build_risk_budget(market, state, unit, profile)
        audit_accounts: dict[str, pd.DataFrame] = {}
        audit_diagnostics: dict[str, Any] = {}
        for audit in v.AUDITS:
            account, diagnostics = v.simulate(
                market, unit, budget, profile, v.START, v.END_EXCLUSIVE, audit
            )
            audit_accounts[audit.name] = account
            audit_diagnostics[audit.name] = diagnostics
            for period in v.PERIODS:
                rows.append(
                    {
                        "policy": policy_name,
                        "audit": audit.name,
                        "period": period,
                        **v.period_metrics(account, period),
                    }
                )
        audit_accounts["base"].to_csv(results / f"equity_{policy_name}.csv")
        unit.to_csv(results / f"unit_weights_{policy_name}.csv")
        budget.to_csv(results / f"risk_budget_{policy_name}.csv")
        annual = v.yearly_returns(audit_accounts["base"]).rename(
            columns={"return": policy_name}
        )
        annual_frames.append(annual)

        table = pd.DataFrame([row for row in rows if row["policy"] == policy_name])
        def metric(audit: str, period: str) -> dict[str, Any]:
            item = table[(table.audit == audit) & (table.period == period)].iloc[0]
            return v.clean(item.drop(labels=["policy", "audit", "period"]).to_dict())

        base_full = metric("base", "full")
        severe_full = metric("severe", "full")
        extreme_full = metric("extreme", "full")
        validation = metric("base", "validation_2024")
        holdout = metric("base", "holdout_2025")
        final = metric("base", "final_2026h1")
        gate_results = target_gate_results(
            base_full,
            severe_full,
            extreme_full,
            validation,
            holdout,
            final,
            annual.rename(columns={policy_name: "return"}),
        )
        policy_summaries[policy_name] = {
            "policy": v.clean(v.asdict(policy)),
            "profile": v.clean(v.asdict(profile)),
            "metrics": {
                "development": metric("base", "development"),
                "validation_2024": validation,
                "holdout_2025": holdout,
                "final_2026h1": final,
                "full_base": base_full,
                "full_severe": severe_full,
                "full_extreme": extreme_full,
                "full_delay_1d": metric("delay_1d", "full"),
            },
            "annual_returns": v.clean(annual.to_dict(orient="records")),
            "diagnostics": audit_diagnostics,
            "target_gate_results": gate_results,
            "target_passed": bool(all(gate_results.values())),
        }
        print(
            policy_name,
            f"full CAGR={float(base_full['cagr']):.4f}",
            f"Sharpe={float(base_full['sharpe']):.3f}",
            f"DD={float(base_full['max_drawdown']):.4f}",
            f"passed={all(gate_results.values())}",
            flush=True,
        )

    metrics_table = pd.DataFrame(rows)
    metrics_table.to_csv(results / "audit_metrics.csv", index=False)
    annual_output: pd.DataFrame | None = None
    for frame in annual_frames:
        annual_output = frame if annual_output is None else annual_output.merge(frame, on="year", how="outer")
    if annual_output is None:
        annual_output = pd.DataFrame(columns=["year"])
    annual_output.sort_values("year").to_csv(results / "ANNUAL_RETURNS.csv", index=False)

    passed = [name for name, value in policy_summaries.items() if value["target_passed"]]
    full_rows = metrics_table[(metrics_table.audit == "base") & (metrics_table.period == "full")]
    leaders = {
        "full_cagr": {
            "policy": str(full_rows.sort_values("cagr", ascending=False).iloc[0].policy),
            "value": float(full_rows.cagr.max()),
        },
        "full_sharpe": {
            "policy": str(full_rows.sort_values("sharpe", ascending=False).iloc[0].policy),
            "value": float(full_rows.sharpe.max()),
        },
        "max_drawdown": {
            "policy": str(full_rows.sort_values("max_drawdown", ascending=False).iloc[0].policy),
            "value": float(full_rows.max_drawdown.max()),
        },
    }
    summary = {
        "program": PROGRAM,
        "status": "frontier_target_pass_non_pristine" if passed else "frontier_audit_complete_no_target_pass",
        "selection_performed": False,
        "audited_policies": list(POLICY_NAMES),
        "target_gates": TARGET_GATES,
        "leaders": leaders,
        "target_passes": passed,
        "policy_summaries": policy_summaries,
        "program_level_holdout_pristine": False,
        "integration_permitted": False,
        "capital_change_authorized": False,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    v.write_json(results / "summary.json", summary)
    v.write_json(
        results / "FROZEN_DECISION.json",
        {
            "program": PROGRAM,
            "status": summary["status"],
            "target_passes": passed,
            "selection_performed": False,
            "integration_permitted": False,
            "capital_change_authorized": False,
            "live_ready": False,
            "real_leverage_authorized": False,
            "profitability_proven": False,
        },
    )
    report = [
        "# V509–V516 — controlled-growth frontier OOS audit",
        "",
        f"Status: `{summary['status']}`.",
        "",
        "No new selection or parameter tuning was performed.",
        "",
        "| Policy | Full CAGR | Sharpe | Max DD | 2024 | 2025 | 2026 H1 | Severe CAGR | Extreme CAGR | Pass |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in POLICY_NAMES:
        item = policy_summaries[name]
        metrics = item["metrics"]
        report.append(
            "| " + name
            + f" | {float(metrics['full_base']['cagr']):+.2%}"
            + f" | {float(metrics['full_base']['sharpe']):.3f}"
            + f" | {float(metrics['full_base']['max_drawdown']):+.2%}"
            + f" | {float(metrics['validation_2024']['total_return']):+.2%}"
            + f" | {float(metrics['holdout_2025']['total_return']):+.2%}"
            + f" | {float(metrics['final_2026h1']['total_return']):+.2%}"
            + f" | {float(metrics['full_severe']['cagr']):+.2%}"
            + f" | {float(metrics['full_extreme']['cagr']):+.2%}"
            + f" | {item['target_passed']} |"
        )
    report += [
        "",
        "No capital, live trading or real leverage is authorized.",
    ]
    (results / "REPORT_RU.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    v.write_manifest(root)
    print(json.dumps(v.clean(summary), indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.root is None or args.cache is None or args.state is None:
        raise SystemExit("--root, --cache and --state are required")
    return run(args.root, args.cache, args.state)


if __name__ == "__main__":
    raise SystemExit(main())
