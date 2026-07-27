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
from data import AssetData, ContractData, load_all
from engine import (
    ensemble,
    metrics,
    policy_dict,
    prepare,
    simulate,
    slice_account,
    subset,
    yearly_returns,
)

CANDIDATE = "ACTIVE_V229_USDM_PERP_QUARTERLY_SPREAD"


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


def asset_pnl(trades: pd.DataFrame) -> dict[str, float]:
    if trades.empty:
        return {asset: 0.0 for asset in ASSETS}
    return {
        asset: float(
            pd.to_numeric(
                trades.loc[trades.asset == asset, "net_pnl"], errors="coerce"
            ).sum()
        )
        for asset in ASSETS
    }


def coverage_gate(markets: dict[str, AssetData]) -> dict[str, Any]:
    assets: dict[str, Any] = {}
    passed = True
    for asset in ASSETS:
        value = markets[asset]
        contracts = [item for item in value.contracts if not item.frame.empty]
        latest_ok = bool(
            contracts
            and max(item.expiry for item in contracts)
            >= pd.Timestamp("2026-06-26 08:00:00", tz="UTC")
        )
        row = {
            "valid_contracts": len(contracts),
            "expected_contracts": 22,
            "perpetual_rows": len(value.perp),
            "funding_rows": len(value.funding),
            "latest_required_contract_passed": latest_ok,
            "passed": bool(
                len(contracts) == 22
                and latest_ok
                and len(value.perp) >= 47_000
                and len(value.funding) >= 1_900
            ),
        }
        assets[asset] = row
        passed = passed and row["passed"]
    return {
        "candidate": "V230_FULL_PANEL_COVERAGE",
        "assets": assets,
        "passed": bool(passed),
        "coin_m_fallback_permitted": False,
    }


def self_test() -> None:
    index = pd.date_range("2022-01-01", periods=1800, freq="h", tz="UTC")
    rng = np.random.default_rng(229)
    markets: dict[str, AssetData] = {}
    for number, asset in enumerate(ASSETS):
        perp = 100.0 * (1 + number) * np.exp(
            np.cumsum(rng.normal(0.0, 0.002, len(index)))
        )
        perp_frame = pd.DataFrame(
            {"timestamp": index, "open": perp, "close": perp * 1.00001}
        )
        funding = pd.DataFrame(
            {
                "timestamp": index[::8],
                "rate": np.where(np.arange(len(index[::8])) % 2 == 0, 0.0001, 0.00005),
                "interval_hours": 8.0,
            }
        )
        contracts = []
        for j, expiry in enumerate(
            [
                pd.Timestamp("2022-03-25 08:00", tz="UTC"),
                pd.Timestamp("2022-06-24 08:00", tz="UTC"),
                pd.Timestamp("2022-09-30 08:00", tz="UTC"),
            ]
        ):
            basis = 0.0015 * np.sin(np.arange(len(index)) / (35 + j * 7)) + 0.0004 * j
            for shock in range(300 + 17 * j, len(index) - 48, 300):
                basis[shock : shock + 4] += 0.012
            price = perp * np.exp(basis)
            frame = pd.DataFrame(
                {"timestamp": index, "open": price, "close": price * 1.00001}
            )
            contracts.append(
                ContractData(
                    symbol=f"{asset}USDT_TEST{j}", expiry=expiry, frame=frame
                )
            )
        markets[asset] = AssetData(
            asset=asset,
            perp=perp_frame,
            funding=funding,
            contracts=contracts,
        )
    prepared_full = prepare(markets)
    prepared = subset(prepared_full, index[-1].isoformat())
    policy = next(
        policy
        for policy in POLICIES
        if policy.family == "perp_dated_basis_convergence"
        and policy.lookback_hours == 168
        and policy.entry_abs_z == 2.0
        and policy.minimum_expected_edge_bps == 20.0
        and policy.hold_hours == 24
    )
    account, trades = simulate(
        prepared, policy, Audit("test", pair_round_trip_bps=0.0)
    )
    assert len(account) == len(prepared.index)
    assert account.equity.notna().all()
    assert account.gross.max() <= 0.60
    assert not trades.empty
    changed = {asset: value for asset, value in markets.items()}
    changed["BTC"].contracts[0].frame.loc[1500:, "close"] *= 3.0
    start_pos = int(prepared.index.get_indexer([index[0]])[0])
    before = prepared.assets["BTC"].zscores[("front_basis", 168)][start_pos : start_pos + 1500]
    after_prepared = subset(prepare(changed), index[-1].isoformat())
    after = after_prepared.assets["BTC"].zscores[("front_basis", 168)][start_pos : start_pos + 1500]
    np.testing.assert_allclose(before, after, equal_nan=True)
    print(f"V229-V236 self-test passed; trades={len(trades)}")


def write_manifest(root: Path) -> None:
    files = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        rel = str(path.relative_to(root))
        if rel in {"MANIFEST.json", "run.log"} or rel.startswith("inputs/"):
            continue
        files[rel] = {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    (root / "MANIFEST.json").write_text(
        json.dumps({"candidate": CANDIDATE, "files": files}, indent=2) + "\n"
    )


def run(args: argparse.Namespace) -> int:
    root = args.root
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
    markets, provenance = load_all(args.cache)
    gate = coverage_gate(markets)
    (results / "coverage_gate.json").write_text(json.dumps(gate, indent=2) + "\n")
    (results / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n"
    )
    if not gate["passed"]:
        proof = {
            "candidate": CANDIDATE,
            "selection_not_run": True,
            "reason": "full panel coverage gate failed",
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
            "status": "full_panel_data_insufficient",
            "coverage_gate": gate,
            "selection": proof,
            "selection_run": False,
            "full_backtest_run": False,
            "standalone_selection_passed": False,
            "integration_permitted": False,
            "promoted_candidates": [],
            "live_ready": False,
            "real_leverage_authorized": False,
            "profitability_proven": False,
        }
        (results / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        (results / "FROZEN_DECISION.json").write_text(
            json.dumps(summary, indent=2) + "\n"
        )
        (results / "REPORT_RU.md").write_text(
            "# Active V229–V236 — USD-M perpetual/quarterly spread\n\n"
            "Status: `full_panel_data_insufficient`. P&L was not opened.\n"
        )
        write_manifest(root)
        print(json.dumps(summary, indent=2))
        return 0

    prepared = prepare(markets)
    development_prepared = subset(prepared, DEVELOPMENT_END)
    base_audit = next(item for item in AUDITS if item.name == "base")
    ranking_rows: list[dict[str, Any]] = []
    development_accounts: dict[str, pd.DataFrame] = {}
    development_trades: dict[str, pd.DataFrame] = {}
    for number, policy in enumerate(POLICIES, start=1):
        account, trades = simulate(development_prepared, policy, base_audit)
        development_accounts[policy.name] = account
        development_trades[policy.name] = trades
        values = metrics(account)
        yearly = yearly_returns(account, "return")
        all_positive = bool(not yearly.empty and (yearly["return"] > 0.0).all())
        pnl = asset_pnl(trades)
        promotable = policy.family in PROMOTABLE_FAMILIES
        eligible = bool(
            promotable
            and values["cagr"] >= DEVELOPMENT_GATES["cagr_min"]
            and values["sharpe"] >= DEVELOPMENT_GATES["sharpe_min"]
            and values["max_drawdown"] >= DEVELOPMENT_GATES["max_drawdown_min"]
            and values["trade_count"] >= DEVELOPMENT_GATES["closed_trades_min"]
            and values["annual_turnover"] <= DEVELOPMENT_GATES["annual_turnover_max"]
            and all_positive
            and pnl["BTC"] > 0.0
            and pnl["ETH"] > 0.0
        )
        score = (
            float(values["cagr"])
            + 0.06 * float(values["sharpe"])
            + 0.10 * float(values["max_drawdown"])
            - 0.0005 * float(values["annual_turnover"])
        )
        ranking_rows.append(
            {
                "policy": policy.name,
                **asdict(policy),
                "promotable_family": promotable,
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

    ranking = pd.DataFrame(ranking_rows).sort_values(
        ["eligible_development", "promotable_family", "score"],
        ascending=[False, False, False],
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
        "promotable_policy_count": int(
            sum(policy.family in PROMOTABLE_FAMILIES for policy in POLICIES)
        ),
        "eligible_policy_count": int(len(eligible)),
        "development_gates": DEVELOPMENT_GATES,
        "post_selection_gates": POST_SELECTION_GATES,
        "coverage_gate": gate,
        "ranking_sha256": hashlib.sha256(
            ranking.to_csv(index=False).encode("utf-8")
        ).hexdigest(),
        "design_sha256": sha256_file(root / "V229_V236_DESIGN.json"),
    }

    if eligible.empty:
        proof["selected"] = None
        proof["selection_proof_sha256"] = canonical_hash(proof)
        (results / "selection_proof_before_validation.json").write_text(
            json.dumps(proof, indent=2, default=float) + "\n"
        )
        diagnostics = ranking.head(15).to_dict(orient="records")
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
                    "status": "rejected_before_validation",
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
        (results / "REPORT_RU.md").write_text(
            "# Active V229–V236 — USD-M perpetual/quarterly spread\n\n"
            "Status: `rejected_before_validation`.\n\n"
            f"Eligible policies: 0 / {proof['promotable_policy_count']}.\n\n"
            f"Best diagnostic: `{best['policy']}`; CAGR {best['development_cagr']:.2%}; "
            f"Sharpe {best['development_sharpe']:.3f}; trades {best['development_trade_count']}.\n\n"
            "2024–2026 were not opened.\n"
        )
        write_manifest(root)
        print(json.dumps(summary, indent=2, default=float))
        return 0

    selected_names: list[str] = []
    families: set[str] = set()
    holds: set[int] = set()
    for _, row in eligible.iterrows():
        family = str(row.family)
        hold = int(row.hold_hours)
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

    audit_rows = []
    audit_accounts: dict[str, pd.DataFrame] = {}
    selected_trades = []
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
    delay = metrics(audit_accounts["delay_1h"])
    yearly = yearly_returns(candidate, "V229_calendar")
    worst_year = float(yearly.V229_calendar.min()) if not yearly.empty else 0.0
    checks = {
        "eligible_development": True,
        "validation_return_positive": validation["total_return"] > 0.0,
        "holdout_return_positive": holdout["total_return"] > 0.0,
        "final_return_positive": final["total_return"] > 0.0,
        "severe_full_cagr_positive": severe["cagr"] > 0.0,
        "latency_full_cagr_positive": delay["cagr"] > 0.0,
        "worst_calendar_year": worst_year >= POST_SELECTION_GATES["worst_calendar_year_min"],
        "zero_unplanned_forced_exits": all(
            int(row["forced_exits"]) == 0 for row in audit_rows
        ),
    }
    passed = all(checks.values())
    status = (
        "frozen_historical_candidate_needs_forward"
        if passed
        else "rejected_or_needs_iteration"
    )
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
        "worst_calendar_year": worst_year,
        "audit_metrics": audit_rows,
        "provenance_sha256": canonical_hash(provenance),
    }
    (results / "summary.json").write_text(
        json.dumps(summary, indent=2, default=float) + "\n"
    )
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
    yearly.to_csv(results / "ANNUAL_RETURNS.csv", index=False)
    (results / "REPORT_RU.md").write_text(
        "# Active V229–V236 — USD-M perpetual/quarterly spread\n\n"
        f"Status: `{status}`.\n\n"
        f"Eligible policies: {len(eligible)}; full CAGR {full['cagr']:.2%}; "
        f"validation {validation['total_return']:.2%}; holdout {holdout['total_return']:.2%}; "
        f"final {final['total_return']:.2%}.\n\n"
        "Integration remains disabled.\n"
    )
    write_manifest(root)
    print(json.dumps(summary, indent=2, default=float))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--cache", type=Path, default=Path(".cache/v230_usdm_calendar"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
