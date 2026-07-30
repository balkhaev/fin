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
    START,
    VALIDATION_END,
    VALIDATION_START,
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
    simulate,
    slice_account,
    subset,
    yearly_returns,
)

CANDIDATE = "ACTIVE_V213_SPOT_PERP_LEAD_LAG"
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


def policy_by_name(name: str) -> Policy:
    return next(policy for policy in POLICIES if policy.name == name)


def period_months(start: str, end: str) -> pd.PeriodIndex:
    return pd.period_range(pd.Timestamp(start).to_period("M"), pd.Timestamp(end).to_period("M"), freq="M")


def coverage_gate(markets: dict[str, pd.DataFrame], provenance: dict[str, Any]) -> dict[str, Any]:
    periods = {
        "development": (START, DEVELOPMENT_END),
        "validation": (VALIDATION_START, VALIDATION_END),
        "holdout": (HOLDOUT_START, HOLDOUT_END),
        "final": (FINAL_START, END),
    }
    assets: dict[str, Any] = {}
    passed = True
    for asset in ASSETS:
        frame = markets[asset].copy()
        frame["timestamp"] = pd.to_datetime(frame.timestamp, utc=True)
        frame["month"] = frame.timestamp.dt.to_period("M")
        monthly = frame.groupby("month").complete.mean()
        asset_periods: dict[str, Any] = {}
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
            asset_periods[name] = row
            passed = passed and row["passed"]
        meta = provenance["assets"][asset]
        archive_ok = (
            meta["spot"]["valid_months"] >= 60
            and meta["perp"]["valid_months"] >= 60
        )
        passed = passed and archive_ok
        assets[asset] = {
            "periods": asset_periods,
            "spot_valid_months": meta["spot"]["valid_months"],
            "perp_valid_months": meta["perp"]["valid_months"],
            "archive_gate_passed": archive_ok,
        }
    return {
        "candidate": "V213_SPOT_PERP_PANEL_COVERAGE",
        "required_month_coverage": REQUIRED_MONTH_COVERAGE,
        "required_full_month_share": REQUIRED_FULL_MONTH_SHARE,
        "assets": assets,
        "passed": bool(passed),
    }


def asset_pnl(trades: pd.DataFrame) -> dict[str, float]:
    if trades.empty:
        return {asset: 0.0 for asset in ASSETS}
    return {
        asset: float(pd.to_numeric(trades.loc[trades.asset == asset, "net_pnl"], errors="coerce").sum())
        for asset in ASSETS
    }


def self_test() -> None:
    from config import Audit

    index = pd.date_range("2022-01-01", periods=3500, freq="5min", tz="UTC")
    rng = np.random.default_rng(213)
    markets: dict[str, pd.DataFrame] = {}
    for number, asset in enumerate(ASSETS):
        common = np.cumsum(rng.normal(0.0, 0.0008, len(index)))
        spot = 100.0 * (1 + number) * np.exp(common)
        lead = np.sin(np.arange(len(index)) / 25.0) * 0.002
        perp = spot * np.exp(lead)
        flow_spot = rng.normal(0.0, 0.05, len(index))
        flow_perp = rng.normal(0.0, 0.05, len(index))
        # Deterministic completed-bar spot shocks exercise next-open entries.
        for j in range(600 + number * 17, len(index) - 20, 400):
            spot[j:] *= 1.012
            flow_spot[j] = 1.0
            flow_perp[j] = 0.0
        markets[asset] = pd.DataFrame(
            {
                "timestamp": index,
                "open_spot": spot,
                "close_spot": spot * 1.00001,
                "flow_spot": flow_spot,
                "open_perp": perp,
                "close_perp": perp * 1.00001,
                "flow_perp": flow_perp,
                "complete": True,
            }
        )
    prepared = prepare(markets)
    policy = next(
        policy
        for policy in POLICIES
        if policy.family == "spot_lead_continuation"
        and policy.shock_z == 1.5
        and policy.gap_z == 0.5
        and policy.hold_bars == 3
        and policy.persistence == 1
    )
    account, trades = simulate(
        prepared,
        policy,
        Audit("test", single_round_trip_bps=0.0, pair_round_trip_bps=0.0),
    )
    assert len(account) == len(index)
    assert account.equity.notna().all()
    assert account.gross.max() <= 0.25
    assert not trades.empty
    changed = {asset: frame.copy() for asset, frame in markets.items()}
    changed["BTC"].loc[3000:, "close_perp"] *= 2.0
    before = prepared.arrays["BTC"]["basis_z"][:3000]
    after = prepare(changed).arrays["BTC"]["basis_z"][:3000]
    np.testing.assert_allclose(before, after, equal_nan=True)
    print(f"V213-V220 self-test passed; trades={len(trades)}")


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


def run(args: argparse.Namespace) -> int:
    root = args.root
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
    markets, provenance = load_all(args.cache)
    gate = coverage_gate(markets, provenance)
    (results / "coverage_gate.json").write_text(json.dumps(gate, indent=2) + "\n")
    (results / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    if not gate["passed"]:
        proof = {
            "candidate": CANDIDATE,
            "selection_not_run": True,
            "reason": "data coverage gate failed",
            "selection_uses_2024": False,
            "selection_uses_2025": False,
            "selection_uses_2026": False,
            "coverage_gate": gate,
        }
        (results / "selection_proof_before_validation.json").write_text(
            json.dumps(proof, indent=2) + "\n"
        )
        summary = {
            "candidate": CANDIDATE,
            "status": "data_access_insufficient",
            "coverage_gate": gate,
            "selection": proof,
            "selection_run": False,
            "full_backtest_run": False,
            "integration_permitted": False,
            "live_ready": False,
            "real_leverage_authorized": False,
            "profitability_proven": False,
        }
        (results / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        (results / "FROZEN_DECISION.json").write_text(json.dumps(summary, indent=2) + "\n")
        (results / "REPORT_RU.md").write_text(
            "# Active V213–V220 — spot/perpetual lead-lag\n\n"
            "Status: `data_access_insufficient`. Strategy selection and P&L were not run.\n"
        )
        write_manifest(root)
        print(json.dumps(summary, indent=2))
        return 0

    prepared = prepare(markets)
    development_prepared = subset(prepared, DEVELOPMENT_END)
    base_audit = next(audit for audit in AUDITS if audit.name == "base")
    rows: list[dict[str, Any]] = []
    development_accounts: dict[str, pd.DataFrame] = {}
    development_trades: dict[str, pd.DataFrame] = {}
    for number, policy in enumerate(POLICIES, start=1):
        account, trades = simulate(development_prepared, policy, base_audit)
        development_accounts[policy.name] = account
        development_trades[policy.name] = trades
        values = metrics(account)
        yearly = yearly_returns(account, "return")
        all_positive = bool(not yearly.empty and (yearly["return"] > 0).all())
        pnl = asset_pnl(trades)
        eligible = bool(
            values["cagr"] >= DEVELOPMENT_GATES["cagr_min"]
            and values["sharpe"] >= DEVELOPMENT_GATES["sharpe_min"]
            and values["max_drawdown"] >= DEVELOPMENT_GATES["max_drawdown_min"]
            and values["trade_count"] >= DEVELOPMENT_GATES["closed_trades_min"]
            and values["annual_turnover"] <= DEVELOPMENT_GATES["annual_turnover_max"]
            and all_positive
            and pnl["BTC"] > 0
            and pnl["ETH"] > 0
        )
        score = (
            float(values["cagr"])
            + 0.05 * float(values["sharpe"])
            + 0.10 * float(values["max_drawdown"])
            - 0.0002 * float(values["annual_turnover"])
        )
        rows.append(
            {
                "policy": policy.name,
                **asdict(policy),
                "eligible_development": eligible,
                "all_development_years_positive": all_positive,
                "btc_development_pnl": pnl["BTC"],
                "eth_development_pnl": pnl["ETH"],
                "score": score,
                **{f"development_{key}": value for key, value in values.items()},
            }
        )
        if number % 12 == 0:
            print(f"processed {number}/{len(POLICIES)} policies", flush=True)

    ranking = pd.DataFrame(rows).sort_values(
        ["eligible_development", "score"], ascending=[False, False]
    )
    ranking.to_csv(results / "selection_ranking_before_validation.csv", index=False)
    eligible = ranking[ranking.eligible_development]
    proof = {
        "candidate": CANDIDATE,
        "selection_cutoff": "2023-12-31T23:59:59Z",
        "selection_uses_2024": False,
        "selection_uses_2025": False,
        "selection_uses_2026": False,
        "program_level_holdout_pristine": False,
        "policy_count": len(POLICIES),
        "eligible_policy_count": int(len(eligible)),
        "development_gates": DEVELOPMENT_GATES,
        "post_selection_gates": POST_SELECTION_GATES,
        "coverage_gate": gate,
        "ranking_sha256": hashlib.sha256(
            ranking.to_csv(index=False).encode("utf-8")
        ).hexdigest(),
        "design_sha256": sha256_file(root / "V213_V220_DESIGN.json"),
    }

    if eligible.empty:
        proof["selected"] = None
        proof["selection_proof_sha256"] = canonical_hash(proof)
        (results / "selection_proof_before_validation.json").write_text(
            json.dumps(proof, indent=2, default=float) + "\n"
        )
        diagnostics = ranking.head(12).to_dict(orient="records")
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
        (results / "FROZEN_DECISION.json").write_text(
            json.dumps(
                {
                    "candidate": CANDIDATE,
                    "status": summary["status"],
                    "standalone_selection_passed": False,
                    "integration_permitted": False,
                    "promoted_candidates": [],
                    "live_ready": False,
                    "real_leverage_authorized": False,
                },
                indent=2,
            )
            + "\n"
        )
        best = diagnostics[0]
        report = [
            "# Active V213–V220 — spot/perpetual lead-lag",
            "",
            "Status: `rejected_before_validation`.",
            "",
            f"Eligible development policies: `0 / {len(POLICIES)}`.",
            "",
            f"Best diagnostic policy: `{best['policy']}`.",
            "",
            f"Development CAGR: {best['development_cagr']:.2%}; Sharpe: {best['development_sharpe']:.3f}; trades: {best['development_trade_count']}.",
            "",
            "Validation, 2025 holdout and 2026 H1 were not opened. No live trading or leverage is authorized.",
        ]
        (results / "REPORT_RU.md").write_text("\n".join(report) + "\n")
        write_manifest(root)
        print(json.dumps(summary, indent=2, default=float))
        return 0

    selected_names: list[str] = []
    families: set[str] = set()
    holds: set[int] = set()
    for _, row in eligible.iterrows():
        family = str(row.family)
        hold = int(row.hold_bars)
        if not selected_names or family not in families or hold not in holds:
            selected_names.append(str(row.policy))
            families.add(family)
            holds.add(hold)
        if len(selected_names) == 3:
            break
    for name in eligible.policy.astype(str):
        if len(selected_names) == 3:
            break
        if name not in selected_names:
            selected_names.append(name)
    selected = [policy_by_name(name) for name in selected_names]
    proof["selected"] = [policy_dict(policy) for policy in selected]
    proof["selection_proof_sha256"] = canonical_hash(proof)
    (results / "selection_proof_before_validation.json").write_text(
        json.dumps(proof, indent=2, default=float) + "\n"
    )

    audit_rows: list[dict[str, Any]] = []
    audit_accounts: dict[str, pd.DataFrame] = {}
    selected_trades: list[pd.DataFrame] = []
    for audit in AUDITS:
        accounts = []
        for policy in selected:
            account, trades = simulate(prepared, policy, audit)
            accounts.append(account)
            if audit.name == "base" and not trades.empty:
                selected_trades.append(trades.assign(component=policy.name))
        combined = ensemble(accounts)
        audit_accounts[audit.name] = combined
        combined.to_csv(results / f"{audit.name}_equity.csv")
        full = metrics(combined)
        validation = metrics(slice_account(combined, VALIDATION_START, VALIDATION_END))
        holdout = metrics(slice_account(combined, HOLDOUT_START, HOLDOUT_END))
        final = metrics(slice_account(combined, FINAL_START, END))
        audit_rows.append(
            {
                "audit": audit.name,
                **asdict(audit),
                **full,
                "validation_return": validation["total_return"],
                "holdout_return": holdout["total_return"],
                "final_return": final["total_return"],
            }
        )
    audit_frame = pd.DataFrame(audit_rows)
    audit_frame.to_csv(results / "audit_metrics.csv", index=False)
    trades = pd.concat(selected_trades, ignore_index=True) if selected_trades else pd.DataFrame()
    trades.to_csv(results / "selected_trades.csv", index=False)

    candidate = audit_accounts["base"]
    full = metrics(candidate)
    development = metrics(slice_account(candidate, START, DEVELOPMENT_END))
    validation = metrics(slice_account(candidate, VALIDATION_START, VALIDATION_END))
    holdout = metrics(slice_account(candidate, HOLDOUT_START, HOLDOUT_END))
    final = metrics(slice_account(candidate, FINAL_START, END))
    severe = metrics(audit_accounts["severe"])
    delay = metrics(audit_accounts["delay_5m"])
    quarters = quarterly_returns(candidate)
    worst_quarter = float(quarters.min()) if not quarters.empty else 0.0
    top_month_share = monthly_pnl_share(candidate)
    checks = {
        "eligible_development": True,
        "validation_return_positive": validation["total_return"] > 0,
        "holdout_return_positive": holdout["total_return"] > 0,
        "final_return_positive": final["total_return"] > 0,
        "severe_full_cagr_positive": severe["cagr"] > 0,
        "latency_full_cagr_positive": delay["cagr"] > 0,
        "worst_calendar_quarter": worst_quarter >= POST_SELECTION_GATES["worst_calendar_quarter_min"],
        "top_month_positive_pnl_share": top_month_share <= POST_SELECTION_GATES["top_month_positive_pnl_share_max"],
        "unexplained_events": full["unexplained_events"] <= POST_SELECTION_GATES["unexplained_events_max"],
    }
    passed = all(checks.values())
    status = "frozen_historical_candidate_needs_forward" if passed else "rejected_or_needs_iteration"
    summary = {
        "candidate": CANDIDATE,
        "status": status,
        "eligible_policy_count": int(len(eligible)),
        "standalone_selection_passed": passed,
        "integration_permitted": False,
        "promoted_candidates": [CANDIDATE] if passed else [],
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
        "selection": proof,
        "coverage_gate": gate,
        "checks": checks,
        "candidate_full": full,
        "candidate_development": development,
        "candidate_validation_2024": validation,
        "candidate_holdout_2025": holdout,
        "candidate_final_2026h1": final,
        "worst_calendar_quarter": worst_quarter,
        "top_month_positive_pnl_share": top_month_share,
        "audit_metrics": audit_frame.to_dict(orient="records"),
        "provenance_sha256": canonical_hash(provenance),
    }
    (results / "summary.json").write_text(json.dumps(summary, indent=2, default=float) + "\n")
    (results / "FROZEN_DECISION.json").write_text(
        json.dumps(
            {
                "candidate": CANDIDATE,
                "status": status,
                "standalone_selection_passed": passed,
                "integration_permitted": False,
                "promoted_candidates": [CANDIDATE] if passed else [],
                "live_ready": False,
                "real_leverage_authorized": False,
            },
            indent=2,
        )
        + "\n"
    )
    yearly_returns(candidate, "V213_spot_perp").to_csv(
        results / "ANNUAL_RETURNS.csv", index=False
    )
    report = [
        "# Active V213–V220 — spot/perpetual lead-lag",
        "",
        f"Status: `{status}`.",
        "",
        f"Eligible development policies: `{len(eligible)} / {len(POLICIES)}`.",
        "",
        f"Full CAGR: {full['cagr']:.2%}; validation: {validation['total_return']:.2%}; holdout: {holdout['total_return']:.2%}; final: {final['total_return']:.2%}.",
        "",
        "Integration remains disabled; no live trading or real leverage is authorized.",
    ]
    (results / "REPORT_RU.md").write_text("\n".join(report) + "\n")
    write_manifest(root)
    print(json.dumps(summary, indent=2, default=float))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--cache", type=Path, default=Path(".cache/v213_spot_perp"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
