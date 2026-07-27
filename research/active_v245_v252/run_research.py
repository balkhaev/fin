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
    Policy,
)
from data import load_all
from engine import metrics, policy_dict, prepare, simulate, slice_account, yearly_returns

CANDIDATE = "ACTIVE_V245_USDM_COINM_DUAL_PERP"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(value: Any) -> str:
    raw = json.dumps(
        clean(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if np.isfinite(number) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(clean(value), indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def write_manifest(root: Path) -> None:
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        rel = str(path.relative_to(root))
        if rel in {"MANIFEST.json", "run.log"}:
            continue
        files[rel] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    write_json(root / "MANIFEST.json", {"candidate": CANDIDATE, "files": files})


def self_test() -> None:
    index = pd.date_range("2022-01-01", periods=1400, freq="1h", tz="UTC")
    rng = np.random.default_rng(245)
    base = 30_000.0 * np.exp(np.cumsum(rng.normal(0.0, 0.0007, len(index))))
    markets: dict[str, pd.DataFrame] = {}
    for number, asset in enumerate(ASSETS):
        wave = 24.0 * np.sin(np.arange(len(index)) / (18.0 + number * 4))
        wave[400:410] += 70.0
        wave[850:860] -= 70.0
        coin = base * np.exp(wave / 10_000.0) * (1.0 + number * 0.01)
        usd = base * (1.0 + number * 0.01)
        funding_usd = np.zeros(len(index))
        funding_coin = np.zeros(len(index))
        funding_usd[::8] = 0.0001
        funding_coin[::8] = -0.00005
        markets[asset] = pd.DataFrame(
            {
                "timestamp": index,
                "asset": asset,
                "open_usdm": usd,
                "close_usdm": usd,
                "open_coinm": coin,
                "close_coinm": coin,
                "funding_usdm": funding_usd,
                "funding_coinm": funding_coin,
                "funding_interval_hours_usdm": np.where(funding_usd != 0, 8.0, 0.0),
                "funding_interval_hours_coinm": np.where(funding_coin != 0, 8.0, 0.0),
                "funding_event_usdm": funding_usd != 0,
                "funding_event_coinm": funding_coin != 0,
                "price_complete": True,
                "basis_close_bps": wave,
                "basis_open_bps": wave,
                "funding_spread_event": funding_usd - funding_coin,
            }
        )
    prepared = prepare(markets)
    policy = Policy("dual_perp_basis_convergence", 168, 1.5, 5.0, 24)
    base_audit = next(value for value in AUDITS if value.name == "base")
    account, trades = simulate(prepared, policy, base_audit)
    assert not trades.empty
    assert metrics(account)["trade_count"] > 0
    assert float(account.gross.max()) < 0.35

    changed = {key: value.copy() for key, value in markets.items()}
    changed[ASSETS[0]].loc[900, "basis_close_bps"] += 10_000.0
    changed[ASSETS[0]].loc[900, "close_coinm"] *= 2.0
    before = prepared.frames[ASSETS[0]]["basis_z_168"].iloc[:900]
    after = prepare(changed).frames[ASSETS[0]]["basis_z_168"].iloc[:900]
    np.testing.assert_allclose(before, after, equal_nan=True)
    print("V245-V252 self-test passed")


def development_asset_pnl(trades: pd.DataFrame) -> dict[str, float]:
    result = {asset: 0.0 for asset in ASSETS}
    if trades.empty:
        return result
    values = trades.copy()
    values["exit_time"] = pd.to_datetime(values["exit_time"], utc=True)
    cutoff = pd.Timestamp(DEVELOPMENT_END, tz="UTC")
    values = values[values.exit_time <= cutoff]
    for asset, part in values.groupby("asset"):
        result[str(asset)] = float(pd.to_numeric(part.net_pnl, errors="coerce").fillna(0.0).sum())
    return result


def data_failure(root: Path, gate: dict[str, Any], provenance: dict[str, Any]) -> None:
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
    write_json(results / "coverage_gate.json", gate)
    pd.DataFrame().to_csv(results / "selection_ranking_before_validation.csv", index=False)
    pd.DataFrame().to_csv(results / "selected_trades.csv", index=False)
    pd.DataFrame().to_csv(results / "audit_metrics.csv", index=False)
    decision = {
        "candidate": CANDIDATE,
        "status": "data_access_insufficient",
        "standalone_selection_passed": False,
        "integration_permitted": False,
        "promoted_candidates": [],
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    summary = {
        **decision,
        "coverage_gate": gate,
        "selection": None,
        "collateral_audit_required": True,
        "provenance_sha256": canonical_hash(provenance),
    }
    write_json(results / "FROZEN_DECISION.json", decision)
    write_json(results / "summary.json", summary)
    write_json(
        results / "selection_proof_before_validation.json",
        {"candidate": CANDIDATE, "selection_not_run": True, "reason": "data gate failed"},
    )
    (results / "REPORT_RU.md").write_text(
        "# Active V245–V252 — USD-M/COIN-M dual perpetual\n\n"
        "Status: `data_access_insufficient`. P&L и selection не запускались.\n"
    )
    write_manifest(root)


def run(root: Path, cache: Path) -> int:
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
    markets, provenance, coverage_gate = load_all(root, cache)
    write_json(results / "coverage_gate.json", coverage_gate)
    if not coverage_gate["passed"]:
        data_failure(root, coverage_gate, provenance)
        print(json.dumps(clean({"status": "data_access_insufficient", "coverage": coverage_gate}), indent=2))
        return 0

    prepared = prepare(markets)
    base_audit = next(value for value in AUDITS if value.name == "base")
    ranking_rows: list[dict[str, Any]] = []
    base_accounts: dict[str, pd.DataFrame] = {}
    base_trades: dict[str, pd.DataFrame] = {}
    for number, policy in enumerate(POLICIES, start=1):
        account, trades = simulate(prepared, policy, base_audit)
        base_accounts[policy.name] = account
        base_trades[policy.name] = trades
        development = slice_account(account, START, DEVELOPMENT_END)
        values = metrics(development)
        years = yearly_returns(development, "return")
        all_years_positive = bool(
            not years.empty and (pd.to_numeric(years["return"], errors="coerce") > 0.0).all()
        )
        pnl = development_asset_pnl(trades)
        promotable = policy.family in PROMOTABLE_FAMILIES
        eligible = bool(
            promotable
            and values["cagr"] >= DEVELOPMENT_GATES["cagr_min"]
            and values["sharpe"] >= DEVELOPMENT_GATES["sharpe_min"]
            and values["max_drawdown"] >= DEVELOPMENT_GATES["max_drawdown_min"]
            and values["trade_count"] >= DEVELOPMENT_GATES["closed_trades_min"]
            and values["annual_turnover"] <= DEVELOPMENT_GATES["annual_turnover_max"]
            and all_years_positive
            and pnl["BTC"] > 0.0
            and pnl["ETH"] > 0.0
        )
        score = (
            float(values["cagr"])
            + 0.05 * float(values["sharpe"])
            + 0.10 * float(values["max_drawdown"])
            - 0.0002 * float(values["annual_turnover"])
        )
        ranking_rows.append(
            {
                "policy": policy.name,
                **asdict(policy),
                "promotable_family": promotable,
                "eligible_development": eligible,
                "all_development_years_positive": all_years_positive,
                "btc_development_pnl": pnl["BTC"],
                "eth_development_pnl": pnl["ETH"],
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
    eligible = ranking[ranking.eligible_development.astype(bool)]
    selected_name = str(eligible.iloc[0].policy) if not eligible.empty else None
    selected_policy = next((policy for policy in POLICIES if policy.name == selected_name), None)
    proof = {
        "candidate": CANDIDATE,
        "selection_cutoff": "2023-12-31T23:59:59Z",
        "selection_uses_2024": False,
        "selection_uses_2025": False,
        "selection_uses_2026": False,
        "program_level_holdout_pristine": False,
        "policy_count": len(POLICIES),
        "promotable_policy_count": sum(policy.family in PROMOTABLE_FAMILIES for policy in POLICIES),
        "eligible_policy_count": int(len(eligible)),
        "development_gates": DEVELOPMENT_GATES,
        "post_selection_gates": POST_SELECTION_GATES,
        "coverage_gate": coverage_gate,
        "ranking_sha256": hashlib.sha256(ranking.to_csv(index=False).encode("utf-8")).hexdigest(),
        "design_sha256": sha256_file(root / "V245_V252_DESIGN.json"),
        "selected": policy_dict(selected_policy) if selected_policy is not None else None,
    }
    proof["selection_proof_sha256"] = canonical_hash(proof)
    write_json(results / "selection_proof_before_validation.json", proof)

    if selected_policy is None:
        diagnostics = ranking.head(12).to_dict(orient="records")
        decision = {
            "candidate": CANDIDATE,
            "status": "rejected_before_validation",
            "eligible_policy_count": 0,
            "standalone_selection_passed": False,
            "integration_permitted": False,
            "promoted_candidates": [],
            "live_ready": False,
            "real_leverage_authorized": False,
            "profitability_proven": False,
        }
        summary = {
            **decision,
            "selection": proof,
            "coverage_gate": coverage_gate,
            "development_diagnostics": diagnostics,
            "collateral_audit_required": True,
            "limitations": [
                "USD-M and COIN-M collateral wallets are segregated and are not netted for liquidation.",
                "Public hourly archives are not executable bid/ask or queue observations.",
                "Program-level holdout is not pristine.",
            ],
            "provenance_sha256": canonical_hash(provenance),
        }
        write_json(results / "FROZEN_DECISION.json", decision)
        write_json(results / "summary.json", summary)
        pd.DataFrame().to_csv(results / "selected_trades.csv", index=False)
        pd.DataFrame().to_csv(results / "audit_metrics.csv", index=False)
        (results / "REPORT_RU.md").write_text(
            "# Active V245–V252 — USD-M/COIN-M dual perpetual\n\n"
            "Status: `rejected_before_validation`.\n\n"
            f"Eligible development policies: `0/{sum(policy.family in PROMOTABLE_FAMILIES for policy in POLICIES)}`. "
            "Validation 2024, holdout 2025 и final 2026 H1 не открывались.\n"
        )
        write_manifest(root)
        print(json.dumps(clean(summary), indent=2))
        return 0

    audit_rows: list[dict[str, Any]] = []
    audit_accounts: dict[str, pd.DataFrame] = {}
    selected_trades: pd.DataFrame | None = None
    for audit in AUDITS:
        if audit.name == "base":
            account = base_accounts[selected_policy.name]
            trades = base_trades[selected_policy.name]
        else:
            account, trades = simulate(prepared, selected_policy, audit)
        audit_accounts[audit.name] = account
        if audit.name == "base":
            selected_trades = trades.copy()
        full = metrics(account)
        development = metrics(slice_account(account, START, DEVELOPMENT_END))
        validation = metrics(slice_account(account, VALIDATION_START, VALIDATION_END))
        holdout = metrics(slice_account(account, HOLDOUT_START, HOLDOUT_END))
        final = metrics(slice_account(account, FINAL_START, END))
        audit_rows.append(
            {
                "audit": audit.name,
                **asdict(audit),
                **full,
                "development_cagr": development["cagr"],
                "validation_return": validation["total_return"],
                "holdout_return": holdout["total_return"],
                "final_return": final["total_return"],
            }
        )
    audit_frame = pd.DataFrame(audit_rows)
    audit_frame.to_csv(results / "audit_metrics.csv", index=False)
    if selected_trades is None:
        selected_trades = pd.DataFrame()
    selected_trades.to_csv(results / "selected_trades.csv", index=False)

    base_account = audit_accounts["base"]
    full = metrics(base_account)
    development = metrics(slice_account(base_account, START, DEVELOPMENT_END))
    validation = metrics(slice_account(base_account, VALIDATION_START, VALIDATION_END))
    holdout = metrics(slice_account(base_account, HOLDOUT_START, HOLDOUT_END))
    final = metrics(slice_account(base_account, FINAL_START, END))
    yearly = yearly_returns(base_account, "V245_dual_perp")
    yearly.to_csv(results / "ANNUAL_RETURNS.csv", index=False)
    severe = metrics(audit_accounts["severe"])
    delayed = metrics(audit_accounts["delay_1h"])
    worst_year = float(pd.to_numeric(yearly.V245_dual_perp).min()) if not yearly.empty else -1.0
    checks = {
        "eligible_development": True,
        "validation_return_positive": validation["total_return"] > 0.0,
        "holdout_return_positive": holdout["total_return"] > 0.0,
        "final_return_positive": final["total_return"] > 0.0,
        "severe_full_cagr_positive": severe["cagr"] > 0.0,
        "latency_full_cagr_positive": delayed["cagr"] > 0.0,
        "worst_calendar_year": worst_year >= POST_SELECTION_GATES["worst_calendar_year_min"],
        "zero_unplanned_forced_exits": all(int(row["forced_exits"]) == 0 for row in audit_rows),
        "data_coverage": coverage_gate["passed"],
    }
    standalone_passed = all(checks.values())
    status = (
        "frozen_historical_candidate_needs_collateral_audit"
        if standalone_passed
        else "rejected_after_validation"
    )
    decision = {
        "candidate": CANDIDATE,
        "status": status,
        "eligible_policy_count": int(len(eligible)),
        "standalone_selection_passed": standalone_passed,
        "integration_permitted": False,
        "promoted_candidates": [CANDIDATE] if standalone_passed else [],
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    summary = {
        **decision,
        "selection": proof,
        "coverage_gate": coverage_gate,
        "checks": checks,
        "candidate_full": full,
        "candidate_development": development,
        "candidate_validation_2024": validation,
        "candidate_holdout_2025": holdout,
        "candidate_final_2026h1": final,
        "worst_year": worst_year,
        "audit_metrics": audit_frame.to_dict(orient="records"),
        "collateral_audit_required": True,
        "limitations": [
            "USD-M and COIN-M collateral wallets are segregated; no cross-wallet liquidation netting is assumed.",
            "A historical pass cannot be integrated before leg-level maintenance-margin and collateral stress.",
            "Public hourly archives are not executable bid/ask or queue observations.",
            "Program-level holdout is not pristine.",
        ],
        "provenance_sha256": canonical_hash(provenance),
    }
    write_json(results / "FROZEN_DECISION.json", decision)
    write_json(results / "summary.json", summary)
    (results / "REPORT_RU.md").write_text(
        "# Active V245–V252 — USD-M/COIN-M dual perpetual\n\n"
        f"Status: `{status}`.\n\n"
        f"Selected: `{selected_policy.name}`. Standalone pass: `{standalone_passed}`. "
        "Integration remains forbidden pending a separate collateral audit.\n"
    )
    write_manifest(root)
    print(json.dumps(clean(summary), indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--cache", type=Path, default=Path(".cache/v245_dual_perp"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return run(args.root, args.cache)


if __name__ == "__main__":
    raise SystemExit(main())
