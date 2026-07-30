#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config import (
    ASSETS,
    AUDITS,
    DEVELOPMENT_END,
    DEVELOPMENT_GATES,
    END,
    FINAL_START,
    HOLDOUT_END,
    HOLDOUT_START,
    POLICIES,
    POST_SELECTION_GATES,
    PROMOTABLE_FAMILIES,
    START,
    VALIDATION_END,
    VALIDATION_START,
    Audit,
    Policy,
)
from data import load_all
from engine import (
    ensemble,
    metrics,
    monthly_pnl_share,
    policy_dict,
    prepare,
    quarterly_returns,
    side_pnl,
    simulate,
    slice_account,
    subset,
    yearly_returns,
)

CANDIDATE = "ACTIVE_V221_CROSS_ASSET_SHOCK_SPILLOVER"
REQUIRED_MONTH_COVERAGE = 0.95
REQUIRED_FULL_MONTH_SHARE = 0.90


def canonical_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=float,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def period_months(start: str, end: str) -> pd.PeriodIndex:
    return pd.period_range(
        pd.Timestamp(start).to_period("M"), pd.Timestamp(end).to_period("M"), freq="M"
    )


def coverage_gate(panel: pd.DataFrame, provenance: dict[str, Any]) -> dict[str, Any]:
    periods = {
        "development": (START, DEVELOPMENT_END),
        "validation": (VALIDATION_START, VALIDATION_END),
        "holdout": (HOLDOUT_START, HOLDOUT_END),
        "final": (FINAL_START, END),
    }
    frame = panel.copy()
    frame["timestamp"] = pd.to_datetime(frame.timestamp, utc=True)
    frame["month"] = frame.timestamp.dt.to_period("M")
    monthly = frame.groupby("month").complete.mean()
    period_rows: dict[str, Any] = {}
    passed = True
    for name, (start, end) in periods.items():
        expected = period_months(start, end)
        values = monthly.reindex(expected).fillna(0.0)
        full = int(values.ge(REQUIRED_MONTH_COVERAGE).sum())
        required = int(np.ceil(len(expected) * REQUIRED_FULL_MONTH_SHARE))
        row = {
            "months": len(expected),
            "full_months": full,
            "required_full_months": required,
            "minimum_coverage": float(values.min()) if len(values) else 0.0,
            "median_coverage": float(values.median()) if len(values) else 0.0,
            "passed": full >= required,
        }
        period_rows[name] = row
        passed = passed and row["passed"]
    archive_rows = {}
    for asset in ASSETS:
        value = provenance["assets"][asset]
        archive_ok = int(value["valid_months"]) >= 60
        archive_rows[asset] = {
            "valid_months": int(value["valid_months"]),
            "attempted_months": int(value["attempted_months"]),
            "passed": archive_ok,
        }
        passed = passed and archive_ok
    return {
        "candidate": "V221_CROSS_ASSET_PANEL_COVERAGE",
        "required_month_coverage": REQUIRED_MONTH_COVERAGE,
        "required_full_month_share": REQUIRED_FULL_MONTH_SHARE,
        "periods": period_rows,
        "archives": archive_rows,
        "passed": bool(passed),
    }


def policy_by_name(name: str) -> Policy:
    return next(policy for policy in POLICIES if policy.name == name)


def choose_components(ranking: pd.DataFrame) -> list[str]:
    eligible = ranking[ranking.eligible_development]
    selected: list[str] = []
    families: set[str] = set()
    holds: set[int] = set()
    for _, row in eligible.iterrows():
        family = str(row.family)
        hold = int(row.hold_bars)
        if not selected or family not in families or hold not in holds:
            selected.append(str(row.policy))
            families.add(family)
            holds.add(hold)
        if len(selected) == 3:
            break
    for name in eligible.policy.astype(str):
        if len(selected) == 3:
            break
        if name not in selected:
            selected.append(name)
    return selected


def self_test() -> None:
    from config import BAR_MINUTES

    index = pd.date_range("2022-01-01", periods=9000, freq=f"{BAR_MINUTES}min", tz="UTC")
    rng = np.random.default_rng(221)
    btc_ret = rng.normal(0.0, 0.0005, len(index))
    eth_ret = 1.1 * btc_ret + rng.normal(0.0, 0.00035, len(index))
    flow_btc = rng.normal(0.0, 0.05, len(index))
    flow_eth = rng.normal(0.0, 0.05, len(index))
    for j in range(2300, len(index) - 50, 450):
        sign = 1.0 if (j // 450) % 2 == 0 else -1.0
        btc_ret[j] += sign * 0.018
        flow_btc[j] = sign
        eth_ret[j] += sign * 0.001
        eth_ret[j + 2] += sign * 0.012
    btc_close = 100.0 * np.exp(np.cumsum(btc_ret))
    eth_close = 120.0 * np.exp(np.cumsum(eth_ret))
    panel = pd.DataFrame(
        {
            "timestamp": index,
            "open_btc": np.r_[btc_close[0], btc_close[:-1]],
            "close_btc": btc_close,
            "flow_btc": flow_btc,
            "open_eth": np.r_[eth_close[0], eth_close[:-1]],
            "close_eth": eth_close,
            "flow_eth": flow_eth,
            "complete": True,
        }
    )
    prepared = prepare(panel)
    policy = next(
        policy
        for policy in POLICIES
        if policy.family == "btc_leads_eth_continuation"
        and policy.shock_abs_z == 2.0
        and policy.gap_abs_z == 0.75
        and policy.hold_bars == 3
        and policy.persistence_bars == 1
    )
    account, trades = simulate(
        prepared,
        policy,
        Audit("test", single_round_trip_bps=0.0, pair_round_trip_bps=0.0),
    )
    assert len(account) == len(index)
    assert account.equity.notna().all()
    assert account.gross.max() <= 0.25
    assert len(trades) >= 5
    changed = panel.copy()
    changed.loc[7000:, "close_eth"] *= 2.0
    before = prepared.frame["beta"].iloc[:7000]
    after = prepare(changed).frame["beta"].iloc[:7000]
    np.testing.assert_allclose(before, after, equal_nan=True)
    print(f"V221-V228 self-test passed; trades={len(trades)}")


def write_manifest(root: Path) -> None:
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        rel = str(path.relative_to(root))
        if rel in {"MANIFEST.json", "run.log"} or rel.startswith("inputs/"):
            continue
        files[rel] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    (root / "MANIFEST.json").write_text(
        json.dumps({"candidate": CANDIDATE, "files": files}, indent=2) + "\n"
    )


def write_failure(
    root: Path,
    results: Path,
    gate: dict[str, Any],
    provenance: dict[str, Any],
) -> None:
    proof = {
        "candidate": CANDIDATE,
        "selection_not_run": True,
        "reason": "data coverage gate failed",
        "selection_uses_2024": False,
        "selection_uses_2025": False,
        "selection_uses_2026": False,
        "coverage_gate": gate,
    }
    summary = {
        "candidate": CANDIDATE,
        "status": "data_access_insufficient",
        "coverage_gate": gate,
        "selection": proof,
        "selection_run": False,
        "full_backtest_run": False,
        "integration_permitted": False,
        "promoted_candidates": [],
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
        "provenance_sha256": canonical_hash(provenance),
    }
    (results / "selection_proof_before_validation.json").write_text(
        json.dumps(proof, indent=2) + "\n"
    )
    (results / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (results / "FROZEN_DECISION.json").write_text(json.dumps(summary, indent=2) + "\n")
    pd.DataFrame().to_csv(results / "selection_ranking_before_validation.csv", index=False)
    (results / "REPORT_RU.md").write_text(
        "# Active V221–V228 — cross-asset shock spillover\n\n"
        "Status: `data_access_insufficient`. P&L and selection were not opened.\n"
    )
    write_manifest(root)


def run(args: argparse.Namespace) -> int:
    root = args.root
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
    panel, provenance = load_all(args.cache)
    gate = coverage_gate(panel, provenance)
    (results / "coverage_gate.json").write_text(json.dumps(gate, indent=2) + "\n")
    (results / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    if not gate["passed"]:
        write_failure(root, results, gate, provenance)
        print(json.dumps({"status": "data_access_insufficient", "coverage": gate}, indent=2))
        return 0

    development_panel = subset(panel, START, DEVELOPMENT_END)
    prepared_dev = prepare(development_panel)
    base_audit = next(item for item in AUDITS if item.name == "base")
    ranking_rows: list[dict[str, Any]] = []
    for number, policy in enumerate(POLICIES, start=1):
        account, trades = simulate(prepared_dev, policy, base_audit)
        values = metrics(account)
        yearly = yearly_returns(account, "return")
        all_positive = bool(not yearly.empty and (yearly["return"] > 0.0).all())
        sides = side_pnl(trades)
        side_positive = sides["long"] > 0.0 and sides["short"] > 0.0
        promotable = policy.family in PROMOTABLE_FAMILIES
        eligible = bool(
            promotable
            and values["cagr"] >= DEVELOPMENT_GATES["cagr_min"]
            and values["sharpe"] >= DEVELOPMENT_GATES["sharpe_min"]
            and values["max_drawdown"] >= DEVELOPMENT_GATES["max_drawdown_min"]
            and values["trade_count"] >= DEVELOPMENT_GATES["closed_trades_min"]
            and values["annual_turnover"] <= DEVELOPMENT_GATES["annual_turnover_max"]
            and all_positive
            and side_positive
        )
        score = (
            float(values["cagr"])
            + 0.06 * float(values["sharpe"])
            + 0.10 * float(values["max_drawdown"])
            - 0.0002 * float(values["annual_turnover"])
        )
        ranking_rows.append(
            {
                "policy": policy.name,
                **asdict(policy),
                "promotable_family": promotable,
                "eligible_development": eligible,
                "all_development_years_positive": all_positive,
                "long_side_pnl": sides["long"],
                "short_side_pnl": sides["short"],
                "side_pnl_positive": side_positive,
                "score": score,
                **{f"development_{key}": value for key, value in values.items()},
            }
        )
        if number % 24 == 0:
            print(f"processed {number}/{len(POLICIES)} policies", flush=True)

    ranking = pd.DataFrame(ranking_rows).sort_values(
        ["eligible_development", "promotable_family", "score"],
        ascending=[False, False, False],
    )
    ranking.to_csv(results / "selection_ranking_before_validation.csv", index=False)
    selected_names = choose_components(ranking)
    proof = {
        "candidate": CANDIDATE,
        "selection_cutoff": "2023-12-31T23:59:59Z",
        "selection_uses_2024": False,
        "selection_uses_2025": False,
        "selection_uses_2026": False,
        "program_level_holdout_pristine": False,
        "policy_count": len(POLICIES),
        "promotable_policy_count": int(ranking.promotable_family.sum()),
        "eligible_policy_count": int(ranking.eligible_development.sum()),
        "selected": [policy_dict(policy_by_name(name)) for name in selected_names],
        "development_gates": DEVELOPMENT_GATES,
        "post_selection_gates": POST_SELECTION_GATES,
        "ranking_sha256": hashlib.sha256(
            ranking.to_csv(index=False).encode("utf-8")
        ).hexdigest(),
        "design_sha256": sha256_file(root / "V221_V228_DESIGN.json"),
        "coverage_gate": gate,
    }
    proof["selection_proof_sha256"] = canonical_hash(proof)
    (results / "selection_proof_before_validation.json").write_text(
        json.dumps(proof, indent=2, default=float) + "\n"
    )

    if not selected_names:
        diagnostics = ranking.head(12).replace({np.nan: None}).to_dict(orient="records")
        summary = {
            "candidate": CANDIDATE,
            "status": "rejected_before_validation",
            "eligible_policy_count": 0,
            "standalone_selection_passed": False,
            "integration_permitted": False,
            "promoted_candidates": [],
            "live_ready": False,
            "real_leverage_authorized": False,
            "profitability_proven": False,
            "selection": proof,
            "coverage_gate": gate,
            "development_diagnostics": diagnostics,
            "provenance_sha256": canonical_hash(provenance),
        }
        (results / "summary.json").write_text(
            json.dumps(summary, indent=2, default=float) + "\n"
        )
        decision = {
            key: summary[key]
            for key in (
                "candidate",
                "status",
                "eligible_policy_count",
                "standalone_selection_passed",
                "integration_permitted",
                "promoted_candidates",
                "live_ready",
                "real_leverage_authorized",
                "profitability_proven",
            )
        }
        (results / "FROZEN_DECISION.json").write_text(
            json.dumps(decision, indent=2) + "\n"
        )
        best = diagnostics[0] if diagnostics else {}
        report = [
            "# Active V221–V228 — BTC/ETH shock spillover",
            "",
            "Status: `rejected_before_validation`.",
            "",
            f"Eligible development policies: **0 / {len(POLICIES)}**.",
            "",
            "2024 validation, 2025 holdout and 2026 H1 final were not opened.",
        ]
        if best:
            report.extend(
                [
                    "",
                    f"Best diagnostic: `{best['policy']}`.",
                    f"Development CAGR: {float(best['development_cagr']):.2%}; "
                    f"Sharpe: {float(best['development_sharpe']):.3f}; "
                    f"trades: {int(best['development_trade_count'])}.",
                ]
            )
        (results / "REPORT_RU.md").write_text("\n".join(report) + "\n")
        write_manifest(root)
        print(json.dumps(summary, indent=2, default=float))
        return 0

    selected_policies = [policy_by_name(name) for name in selected_names]
    prepared_full = prepare(panel)
    audit_rows: list[dict[str, Any]] = []
    audit_accounts: dict[str, pd.DataFrame] = {}
    base_trades: list[pd.DataFrame] = []
    for audit in AUDITS:
        accounts = []
        for policy in selected_policies:
            account, trades = simulate(prepared_full, policy, audit)
            accounts.append(account)
            if audit.name == "base" and not trades.empty:
                base_trades.append(
                    trades.assign(component=policy.name, component_weight=1.0 / len(selected_policies))
                )
        combined = ensemble(accounts)
        audit_accounts[audit.name] = combined
        combined.to_csv(results / f"{audit.name}_equity.csv")
        full = metrics(combined)
        dev = metrics(slice_account(combined, START, DEVELOPMENT_END))
        validation = metrics(slice_account(combined, VALIDATION_START, VALIDATION_END))
        holdout = metrics(slice_account(combined, HOLDOUT_START, HOLDOUT_END))
        final = metrics(slice_account(combined, FINAL_START, END))
        audit_rows.append(
            {
                "audit": audit.name,
                **asdict(audit),
                **full,
                "development_cagr": dev["cagr"],
                "development_sharpe": dev["sharpe"],
                "validation_return": validation["total_return"],
                "holdout_return": holdout["total_return"],
                "final_return": final["total_return"],
            }
        )
    audit_frame = pd.DataFrame(audit_rows)
    audit_frame.to_csv(results / "audit_metrics.csv", index=False)
    trades = pd.concat(base_trades, ignore_index=True) if base_trades else pd.DataFrame()
    trades.to_csv(results / "selected_trades.csv", index=False)

    candidate = audit_accounts["base"]
    candidate_full = metrics(candidate)
    candidate_dev = metrics(slice_account(candidate, START, DEVELOPMENT_END))
    candidate_validation = metrics(slice_account(candidate, VALIDATION_START, VALIDATION_END))
    candidate_holdout = metrics(slice_account(candidate, HOLDOUT_START, HOLDOUT_END))
    candidate_final = metrics(slice_account(candidate, FINAL_START, END))
    severe_full = metrics(audit_accounts["severe"])
    delay_full = metrics(audit_accounts["delay_1bar"])
    quarters = quarterly_returns(candidate)
    worst_quarter = float(quarters["return"].min()) if not quarters.empty else 0.0
    concentration = monthly_pnl_share(trades)
    checks = {
        "development_candidate_cagr": candidate_dev["cagr"] >= DEVELOPMENT_GATES["cagr_min"],
        "development_candidate_sharpe": candidate_dev["sharpe"] >= DEVELOPMENT_GATES["sharpe_min"],
        "validation_return_positive": candidate_validation["total_return"] > 0.0,
        "holdout_return_positive": candidate_holdout["total_return"] > 0.0,
        "final_return_positive": candidate_final["total_return"] > 0.0,
        "severe_full_cagr_positive": severe_full["cagr"] > 0.0,
        "delay_full_cagr_positive": delay_full["cagr"] > 0.0,
        "worst_quarter": worst_quarter >= POST_SELECTION_GATES["worst_quarter_min"],
        "monthly_concentration": concentration <= POST_SELECTION_GATES["top_month_positive_pnl_share_max"],
        "zero_forced_exits": bool((audit_frame.forced_exits == 0).all()),
        "data_coverage": gate["passed"],
    }
    standalone_passed = all(checks.values())
    status = (
        "frozen_historical_candidate_needs_forward"
        if standalone_passed
        else "rejected_after_oos"
    )
    annual = yearly_returns(candidate, "V221_spillover")
    annual.to_csv(results / "ANNUAL_RETURNS.csv", index=False)
    quarters.to_csv(results / "QUARTERLY_RETURNS.csv", index=False)
    summary = {
        "candidate": CANDIDATE,
        "status": status,
        "eligible_policy_count": len(selected_names),
        "standalone_selection_passed": standalone_passed,
        "integration_permitted": standalone_passed,
        "integration_tested": False,
        "promoted_candidates": [CANDIDATE] if standalone_passed else [],
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
        "selection": proof,
        "coverage_gate": gate,
        "checks": checks,
        "candidate_full": candidate_full,
        "candidate_development": candidate_dev,
        "candidate_validation_2024": candidate_validation,
        "candidate_holdout_2025": candidate_holdout,
        "candidate_final_2026h1": candidate_final,
        "worst_quarter": worst_quarter,
        "top_month_positive_pnl_share": concentration,
        "audit_metrics": audit_frame.to_dict(orient="records"),
        "provenance_sha256": canonical_hash(provenance),
        "limitations": [
            "Public 5m klines are completed exchange observations, not executable order-book quotes.",
            "The program-level 2024-2026 windows are not pristine because the broader program has inspected them.",
            "A historical pass would still require nonzero paper-forward fills before any live authorization.",
        ],
    }
    (results / "summary.json").write_text(
        json.dumps(summary, indent=2, default=float) + "\n"
    )
    decision = {
        "candidate": CANDIDATE,
        "status": status,
        "standalone_selection_passed": standalone_passed,
        "integration_permitted": standalone_passed,
        "promoted_candidates": summary["promoted_candidates"],
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    (results / "FROZEN_DECISION.json").write_text(
        json.dumps(decision, indent=2) + "\n"
    )
    report = [
        "# Active V221–V228 — BTC/ETH shock spillover",
        "",
        f"Status: `{status}`.",
        "",
        f"Selected components: {', '.join(selected_names)}.",
        "",
        "| Metric | Full | Development | Validation 2024 | Holdout 2025 | Final 2026 H1 |",
        "|---|---:|---:|---:|---:|---:|",
        f"| Total return | {candidate_full['total_return']:.2%} | {candidate_dev['total_return']:.2%} | {candidate_validation['total_return']:.2%} | {candidate_holdout['total_return']:.2%} | {candidate_final['total_return']:.2%} |",
        f"| CAGR | {candidate_full['cagr']:.2%} | {candidate_dev['cagr']:.2%} | {candidate_validation['cagr']:.2%} | {candidate_holdout['cagr']:.2%} | {candidate_final['cagr']:.2%} |",
        f"| Max DD | {candidate_full['max_drawdown']:.2%} | {candidate_dev['max_drawdown']:.2%} | {candidate_validation['max_drawdown']:.2%} | {candidate_holdout['max_drawdown']:.2%} | {candidate_final['max_drawdown']:.2%} |",
        f"| Sharpe | {candidate_full['sharpe']:.3f} | {candidate_dev['sharpe']:.3f} | {candidate_validation['sharpe']:.3f} | {candidate_holdout['sharpe']:.3f} | {candidate_final['sharpe']:.3f} |",
        "",
        "No live trading or real leverage is authorized.",
    ]
    (results / "REPORT_RU.md").write_text("\n".join(report) + "\n")
    write_manifest(root)
    print(json.dumps(summary, indent=2, default=float))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--cache", type=Path, default=Path(".cache/v221_spillover"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
