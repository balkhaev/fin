#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / "research" / "active_v341_v348" / "run_research.py"
_SPEC = importlib.util.spec_from_file_location("v341_exact_ensemble_source", SOURCE)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(SOURCE)
alpha = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = alpha
_SPEC.loader.exec_module(alpha)

v = alpha.v
base = v.base

CANDIDATE = "ACTIVE_V365_DOWNSIDE_VOL_COMPRESSION_ENSEMBLE"
COMPONENTS = (
    v.Policy("low_downside_vol_ratio", 14, 3, 7, "dollar"),
    v.Policy("low_downside_vol_ratio", 60, 3, 7, "beta"),
)
COMPONENT_WEIGHTS = (0.5, 0.5)
START = "2021-01-01"
DEVELOPMENT_END_EXCLUSIVE = "2024-01-01"
VALIDATION_START = "2024-01-01"
VALIDATION_END_EXCLUSIVE = "2025-01-01"
HOLDOUT_START = "2025-01-01"
HOLDOUT_END_EXCLUSIVE = "2026-01-01"
FINAL_START = "2026-01-01"
END_EXCLUSIVE = "2026-07-01"
TARGET_GROSS = 0.40
MAX_REALIZED_GROSS = 0.70
FORCED_EXIT_PENALTY_BPS = 100.0
DEVELOPMENT_GATES = {
    "cagr_min": 0.05,
    "sharpe_min": 1.00,
    "max_drawdown_min": -0.15,
    "rebalance_events_min": 24,
    "annual_turnover_max": 20.0,
    "max_realized_gross": MAX_REALIZED_GROSS,
    "all_development_years_positive": True,
    "net_long_leg_pnl_positive": True,
    "net_short_leg_pnl_positive": True,
    "symbols_traded_min": 10,
    "top_positive_asset_pnl_share_max": 0.35,
}
POST_OOS_GATES = {
    "validation_return_positive": True,
    "holdout_return_positive": True,
    "final_return_positive": True,
    "full_cagr_min": 0.06,
    "full_sharpe_min": 0.80,
    "full_max_drawdown_min": -0.15,
    "severe_full_cagr_positive": True,
    "extreme_full_cagr_positive": True,
    "latency_full_cagr_positive": True,
    "worst_calendar_year_min": -0.10,
    "full_long_leg_pnl_positive": True,
    "full_short_leg_pnl_positive": True,
    "all_audits_max_gross": MAX_REALIZED_GROSS,
    "top_positive_asset_pnl_share_max": 0.35,
    "forced_exit_count_max": 4,
}
AUDITS = (
    v.Audit("base", 30.0),
    v.Audit("severe", 60.0),
    v.Audit("extreme", 100.0),
    v.Audit("delay_1d", 30.0, execution_delay_days=1),
)


def clean(value: Any) -> Any:
    return v.clean(value)


def write_json(path: Path, value: Any) -> None:
    v.write_json(path, value)


def canonical_hash(value: Any) -> str:
    return v.canonical_hash(value)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def policy_dict(policy: Any) -> dict[str, Any]:
    return asdict(policy) | {"name": policy.name}


def write_manifest(root: Path) -> None:
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative = str(path.relative_to(root))
        if relative in {"MANIFEST.json", "run.log"}:
            continue
        files[relative] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    write_json(root / "MANIFEST.json", {"candidate": CANDIDATE, "files": files})


def configure_engine() -> None:
    alpha.configure_engine()
    alpha.CANDIDATE = CANDIDATE
    v.CANDIDATE = CANDIDATE
    base.TARGET_GROSS = TARGET_GROSS
    base.MAX_REALIZED_GROSS = MAX_REALIZED_GROSS
    base.FORCED_EXIT_PENALTY_BPS = FORCED_EXIT_PENALTY_BPS


def build_ensemble(market: Any) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    score_cache: dict[tuple[str, int], pd.DataFrame] = {}
    raw_cache: dict[tuple[str, int, int, str], pd.DataFrame] = {}
    component_books: dict[str, pd.DataFrame] = {}
    for policy in COMPONENTS:
        component_books[policy.name] = v.build_weights(
            market, policy, score_cache, raw_cache
        )
    ensemble = sum(
        component_books[policy.name] * weight
        for policy, weight in zip(COMPONENTS, COMPONENT_WEIGHTS, strict=True)
    )
    gross = ensemble.abs().sum(axis=1)
    if float(gross.max()) > TARGET_GROSS + 1e-10:
        raise AssertionError(f"ensemble target gross exceeded: {float(gross.max())}")
    return ensemble, component_books


def yearly_positive(account: pd.DataFrame) -> tuple[bool, pd.DataFrame]:
    annual = base.yearly_returns(account, "return")
    passed = bool(
        not annual.empty
        and (pd.to_numeric(annual["return"], errors="coerce") > 0.0).all()
    )
    return passed, annual


def development_gate_results(
    metrics: dict[str, Any], diagnostics: dict[str, Any], all_years_positive: bool
) -> dict[str, bool]:
    return {
        "cagr": float(metrics["cagr"]) >= DEVELOPMENT_GATES["cagr_min"],
        "sharpe": float(metrics["sharpe"]) >= DEVELOPMENT_GATES["sharpe_min"],
        "max_drawdown": float(metrics["max_drawdown"])
        >= DEVELOPMENT_GATES["max_drawdown_min"],
        "rebalance_events": int(diagnostics["rebalance_events"])
        >= DEVELOPMENT_GATES["rebalance_events_min"],
        "annual_turnover": float(metrics["annual_turnover"])
        <= DEVELOPMENT_GATES["annual_turnover_max"],
        "max_realized_gross": float(metrics["max_gross"])
        <= DEVELOPMENT_GATES["max_realized_gross"],
        "all_development_years_positive": all_years_positive,
        "net_long_leg_pnl_positive": float(diagnostics["long_leg_pnl"]) > 0.0,
        "net_short_leg_pnl_positive": float(diagnostics["short_leg_pnl"]) > 0.0,
        "symbols_traded": int(diagnostics["symbol_count_traded"])
        >= DEVELOPMENT_GATES["symbols_traded_min"],
        "concentration": float(diagnostics["top_positive_asset_pnl_share"])
        <= DEVELOPMENT_GATES["top_positive_asset_pnl_share_max"],
    }


def data_failure_outputs(root: Path, gate: dict[str, Any], records: list[dict[str, Any]]) -> None:
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
    proof = {
        "candidate": CANDIDATE,
        "selection_not_run": True,
        "reason": "data gate failed",
        "component_policies": [policy_dict(policy) for policy in COMPONENTS],
        "component_weights": list(COMPONENT_WEIGHTS),
        "selection_uses_2024": False,
        "selection_uses_2025": False,
        "selection_uses_2026": False,
        "coverage_gate": gate,
    }
    proof["selection_proof_sha256"] = canonical_hash(proof)
    decision = {
        "candidate": CANDIDATE,
        "status": "data_access_insufficient",
        "development_reproof_passed": False,
        "oos_opened": False,
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
        "selection": proof,
        "data_manifest_sha256": canonical_hash(records),
    }
    write_json(results / "coverage_gate.json", gate)
    write_json(results / "selection_proof_before_oos.json", proof)
    write_json(results / "FROZEN_DECISION.json", decision)
    write_json(results / "summary.json", summary)
    pd.DataFrame().to_csv(results / "audit_metrics.csv", index=False)
    (results / "REPORT_RU.md").write_text(
        "# Active V365–V372 — downside-volatility compression ensemble\n\n"
        "Status: `data_access_insufficient`. OOS was not opened.\n"
    )
    write_manifest(root)


def self_test() -> None:
    configure_engine()
    assert [policy.name for policy in COMPONENTS] == [
        "low_downside_vol_ratio_l14_k3_r7_dollar",
        "low_downside_vol_ratio_l60_k3_r7_beta",
    ]
    assert COMPONENT_WEIGHTS == (0.5, 0.5)
    market = alpha.synthetic_market()
    weights, components = build_ensemble(market)
    assert set(components) == {policy.name for policy in COMPONENTS}
    account, diagnostics = base.simulate(
        market, weights, "2021-01-01", "2023-01-01", AUDITS[0]
    )
    assert not account.empty
    assert np.isfinite(account.equity).all()
    assert diagnostics["symbol_count_traded"] >= 6

    changed_frames: dict[str, pd.DataFrame] = {}
    for symbol in market.symbols:
        changed_frames[symbol] = pd.DataFrame(
            {
                "open": market.open[symbol],
                "high": market.high[symbol],
                "low": market.low[symbol],
                "close": market.close[symbol],
                "volume": 1.0,
                "quote_volume": 1.0,
                "trades": 1.0,
                "taker_buy_base": 0.5,
                "taker_buy_quote": 0.5,
            },
            index=market.index,
        )
    first = market.symbols[0]
    changed_frames[first].iloc[-1, changed_frames[first].columns.get_loc("close")] *= 5.0
    changed_market = base.Market(
        changed_frames,
        {symbol: pd.Series(0.0, index=market.index) for symbol in market.symbols},
    )
    changed_weights, _ = build_ensemble(changed_market)
    pd.testing.assert_frame_equal(weights.iloc[:-1], changed_weights.iloc[:-1])
    print("V365-V372 exact ensemble causal self-test passed")


def run(root: Path, cache: Path) -> int:
    configure_engine()
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
    config = base.V9Config(
        symbols=tuple(v.SYMBOLS),
        start="2020-01-01",
        end_exclusive=END_EXCLUSIVE,
        interval="1d",
        starting_equity=base.INITIAL_EQUITY,
        max_gross=TARGET_GROSS,
        forced_exit_penalty_bps=FORCED_EXIT_PENALTY_BPS,
    )
    klines, funding, records, quality = base.load_v9(config, cache, False)
    pd.DataFrame(records).to_csv(results / "data_manifest.csv", index=False)
    pd.DataFrame(quality).to_csv(results / "data_quality.csv", index=False)
    gate = base.data_gate(klines, records)
    gate = {**gate, "candidate": "V365_FIXED_UNIVERSE_DATA_COVERAGE"}
    write_json(results / "coverage_gate.json", gate)
    if not gate["passed"]:
        data_failure_outputs(root, gate, records)
        return 0

    market = base.Market(klines, funding)
    weights, component_books = build_ensemble(market)
    weights.to_csv(results / "frozen_ensemble_weights.csv")
    for name, frame in component_books.items():
        frame.to_csv(results / f"component_{name}.csv")

    development_account, development_diagnostics = base.simulate(
        market, weights, START, DEVELOPMENT_END_EXCLUSIVE, AUDITS[0]
    )
    development_metrics = base.account_metrics(development_account)
    all_years_positive, development_years = yearly_positive(development_account)
    development_years.to_csv(results / "DEVELOPMENT_ANNUAL_RETURNS.csv", index=False)
    development_gates = development_gate_results(
        development_metrics, development_diagnostics, all_years_positive
    )
    development_passed = bool(all(development_gates.values()))

    proof = {
        "candidate": CANDIDATE,
        "source_cycle": "V341-V348",
        "component_policies": [policy_dict(policy) for policy in COMPONENTS],
        "component_weights": list(COMPONENT_WEIGHTS),
        "neighboring_component_policies_tested": 0,
        "neighboring_ensemble_weights_tested": 0,
        "neighboring_gross_scales_tested": 0,
        "selection_cutoff": "2023-12-31T23:59:59Z",
        "selection_uses_2024": False,
        "selection_uses_2025": False,
        "selection_uses_2026": False,
        "program_level_holdout_pristine": False,
        "development_gates": DEVELOPMENT_GATES,
        "post_oos_gates": POST_OOS_GATES,
        "development_metrics": development_metrics,
        "development_diagnostics": development_diagnostics,
        "development_gate_results": development_gates,
        "development_reproof_passed": development_passed,
        "oos_opened": development_passed,
        "coverage_gate": gate,
        "design_sha256": sha256_file(root / "V365_V372_DESIGN.json"),
        "data_manifest_sha256": canonical_hash(records),
    }
    proof["selection_proof_sha256"] = canonical_hash(proof)
    write_json(results / "selection_proof_before_oos.json", proof)

    limitations = [
        "The ensemble was selected after V341 development results; program-level OOS is not pristine.",
        "Public daily archives are not executable bid/ask or queue observations.",
        "The fixed universe includes delisted assets; forced exits carry a 100 bps penalty.",
        "A historical pass could authorize only paper-forward monitoring after 27 July 2026.",
    ]

    if not development_passed:
        decision = {
            "candidate": CANDIDATE,
            "status": "rejected_before_oos",
            "development_reproof_passed": False,
            "oos_opened": False,
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
            "coverage_gate": gate,
            "development_metrics": development_metrics,
            "development_diagnostics": development_diagnostics,
            "development_annual_returns": development_years.to_dict(orient="records"),
            "limitations": limitations,
        }
        write_json(results / "FROZEN_DECISION.json", decision)
        write_json(results / "summary.json", summary)
        pd.DataFrame().to_csv(results / "audit_metrics.csv", index=False)
        (results / "REPORT_RU.md").write_text(
            "# Active V365–V372 — downside-volatility compression ensemble\n\n"
            "Status: `rejected_before_oos`. Development reproof failed; later windows were not opened.\n"
        )
        write_manifest(root)
        print(json.dumps(clean(summary), indent=2))
        return 0

    audit_rows: list[dict[str, Any]] = []
    accounts: dict[str, pd.DataFrame] = {}
    diagnostics_by_audit: dict[str, dict[str, Any]] = {}
    for audit in AUDITS:
        account, diagnostics = base.simulate(market, weights, START, END_EXCLUSIVE, audit)
        accounts[audit.name] = account
        diagnostics_by_audit[audit.name] = diagnostics
        segments = {
            "full": base.account_metrics(account),
            "development": base.account_metrics(
                base.slice_account(account, START, DEVELOPMENT_END_EXCLUSIVE)
            ),
            "validation": base.account_metrics(
                base.slice_account(account, VALIDATION_START, VALIDATION_END_EXCLUSIVE)
            ),
            "holdout": base.account_metrics(
                base.slice_account(account, HOLDOUT_START, HOLDOUT_END_EXCLUSIVE)
            ),
            "final": base.account_metrics(
                base.slice_account(account, FINAL_START, END_EXCLUSIVE)
            ),
        }
        row: dict[str, Any] = {"audit": audit.name, **asdict(audit)}
        for segment_name, values in segments.items():
            for key, value in values.items():
                row[f"{segment_name}_{key}"] = value
        row["long_leg_pnl"] = diagnostics["long_leg_pnl"]
        row["short_leg_pnl"] = diagnostics["short_leg_pnl"]
        row["symbol_count_traded"] = diagnostics["symbol_count_traded"]
        row["top_positive_asset_pnl_share"] = diagnostics[
            "top_positive_asset_pnl_share"
        ]
        row["forced_exit_count"] = diagnostics["forced_exit_count"]
        audit_rows.append(row)

    audits = pd.DataFrame(audit_rows)
    audits.to_csv(results / "audit_metrics.csv", index=False)
    base_row = audits[audits.audit == "base"].iloc[0]
    severe_row = audits[audits.audit == "severe"].iloc[0]
    extreme_row = audits[audits.audit == "extreme"].iloc[0]
    delay_row = audits[audits.audit == "delay_1d"].iloc[0]
    annual = base.yearly_returns(accounts["base"], "V365_ensemble")
    annual.to_csv(results / "ANNUAL_RETURNS.csv", index=False)
    worst_year = float(pd.to_numeric(annual["V365_ensemble"], errors="coerce").min())
    base_diagnostics = diagnostics_by_audit["base"]
    post_gates = {
        "validation_return_positive": float(base_row.validation_total_return) > 0.0,
        "holdout_return_positive": float(base_row.holdout_total_return) > 0.0,
        "final_return_positive": float(base_row.final_total_return) > 0.0,
        "full_cagr": float(base_row.full_cagr) >= POST_OOS_GATES["full_cagr_min"],
        "full_sharpe": float(base_row.full_sharpe)
        >= POST_OOS_GATES["full_sharpe_min"],
        "full_max_drawdown": float(base_row.full_max_drawdown)
        >= POST_OOS_GATES["full_max_drawdown_min"],
        "severe_full_cagr_positive": float(severe_row.full_cagr) > 0.0,
        "extreme_full_cagr_positive": float(extreme_row.full_cagr) > 0.0,
        "latency_full_cagr_positive": float(delay_row.full_cagr) > 0.0,
        "worst_calendar_year": worst_year >= POST_OOS_GATES["worst_calendar_year_min"],
        "full_long_leg_pnl_positive": float(base_diagnostics["long_leg_pnl"]) > 0.0,
        "full_short_leg_pnl_positive": float(base_diagnostics["short_leg_pnl"]) > 0.0,
        "all_audits_max_gross": bool(
            (pd.to_numeric(audits.full_max_gross, errors="coerce") <= MAX_REALIZED_GROSS).all()
        ),
        "concentration": float(base_diagnostics["top_positive_asset_pnl_share"])
        <= POST_OOS_GATES["top_positive_asset_pnl_share_max"],
        "forced_exit_count": int(base_diagnostics["forced_exit_count"])
        <= POST_OOS_GATES["forced_exit_count_max"],
    }
    standalone_passed = bool(all(post_gates.values()))
    status = (
        "paper_forward_candidate_non_pristine_oos"
        if standalone_passed
        else "rejected_after_oos"
    )
    decision = {
        "candidate": CANDIDATE,
        "status": status,
        "development_reproof_passed": True,
        "oos_opened": True,
        "standalone_selection_passed": standalone_passed,
        "integration_permitted": False,
        "promoted_candidates": [CANDIDATE] if standalone_passed else [],
        "paper_forward_earliest_start": "2026-07-27" if standalone_passed else None,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    summary = {
        **decision,
        "selection": proof,
        "coverage_gate": gate,
        "post_oos_gate_results": post_gates,
        "audit_metrics": audits.to_dict(orient="records"),
        "annual_returns": annual.to_dict(orient="records"),
        "diagnostics": diagnostics_by_audit,
        "worst_year": worst_year,
        "limitations": limitations,
    }
    write_json(results / "FROZEN_DECISION.json", decision)
    write_json(results / "summary.json", summary)
    (results / "REPORT_RU.md").write_text(
        "# Active V365–V372 — downside-volatility compression ensemble\n\n"
        f"Status: `{status}`.\n\n"
        f"Validation: {float(base_row.validation_total_return):+.2%}; "
        f"holdout: {float(base_row.holdout_total_return):+.2%}; "
        f"final: {float(base_row.final_total_return):+.2%}.\n"
    )
    write_manifest(root)
    print(json.dumps(clean(summary), indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--cache", type=Path, default=Path(".cache/v9"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return run(args.root, args.cache)


if __name__ == "__main__":
    raise SystemExit(main())
