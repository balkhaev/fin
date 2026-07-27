#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
LOTTERY_ROOT = REPO_ROOT / "research" / "active_v269_v276"
LOTTERY_SOURCE = LOTTERY_ROOT / "run_research.py"
_spec = importlib.util.spec_from_file_location("v269_lottery_base", LOTTERY_SOURCE)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"cannot import V269 engine from {LOTTERY_SOURCE}")
lottery = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = lottery
_spec.loader.exec_module(lottery)

CANDIDATE = "ACTIVE_V277_RISK_NORMALIZED_LOW_SKEW"
SOURCE_POLICY_NAME = "low_idiosyncratic_skewness_l180_k3_r28_beta"
POLICY = lottery.Policy("low_idiosyncratic_skewness", 180, 3, 28, "beta")
if POLICY.name != SOURCE_POLICY_NAME:
    raise RuntimeError(POLICY.name)

SYMBOLS = tuple(lottery.SYMBOLS)
START = "2021-01-01"
DEVELOPMENT_END_EXCLUSIVE = "2024-01-01"
VALIDATION_START = "2024-01-01"
VALIDATION_END_EXCLUSIVE = "2025-01-01"
HOLDOUT_START = "2025-01-01"
HOLDOUT_END_EXCLUSIVE = "2026-01-01"
FINAL_START = "2026-01-01"
END_EXCLUSIVE = "2026-07-01"
INITIAL_EQUITY = 10_000.0
TARGET_GROSS = 0.45
MAX_REALIZED_GROSS = 0.70
FORCED_EXIT_PENALTY_BPS = 100.0

lottery.INITIAL_EQUITY = INITIAL_EQUITY
lottery.TARGET_GROSS = TARGET_GROSS
lottery.MAX_REALIZED_GROSS = MAX_REALIZED_GROSS
lottery.base.INITIAL_EQUITY = INITIAL_EQUITY
lottery.base.TARGET_GROSS = TARGET_GROSS
lottery.base.MAX_REALIZED_GROSS = MAX_REALIZED_GROSS
lottery.base.FORCED_EXIT_PENALTY_BPS = FORCED_EXIT_PENALTY_BPS


@dataclass(frozen=True, slots=True)
class Audit:
    name: str
    cost_bps_per_side: float
    execution_delay_days: int = 0

    @property
    def cost_rate(self) -> float:
        return self.cost_bps_per_side / 10_000.0


AUDITS = (
    Audit("base", 30.0),
    Audit("severe", 60.0),
    Audit("extreme", 100.0),
    Audit("delay_1d", 30.0, execution_delay_days=1),
)
DEVELOPMENT_GATES = {
    "cagr_min": 0.04,
    "sharpe_min": 0.80,
    "max_drawdown_min": -0.15,
    "rebalance_events_min": 18,
    "annual_turnover_max": 12.0,
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
    "full_cagr_min": 0.08,
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
SOURCE_EVIDENCE = {
    "workflow_run": 30252073440,
    "artifact": 8647414440,
    "artifact_digest": "sha256:d1c62d3f0a3b741357020f4ee76eaa86c35bf87d6c04b14da0e01138c62ad1e9",
    "ranking_sha256": "9fd6e8a5f2d37c9695da08c5920ea92f592650b7ab4e3694831f44ca700154e1",
    "selection_proof_sha256": "975aa95a02ef503f82f9f61347fc6e88551c3b23b1e5e7bfbb478335b95ff025",
}


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
    path.write_text(
        json.dumps(clean(value), indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        clean(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_source_row() -> dict[str, Any]:
    source_summary_path = LOTTERY_ROOT / "results" / "summary.json"
    source_ranking_path = LOTTERY_ROOT / "results" / "selection_ranking_before_validation.csv"
    if not source_summary_path.exists() or not source_ranking_path.exists():
        raise RuntimeError("committed V269 evidence is required")
    summary = json.loads(source_summary_path.read_text())
    if summary.get("selection", {}).get("selection_uses_2024") is not False:
        raise RuntimeError("V269 selection chronology invalid")
    ranking = pd.read_csv(source_ranking_path)
    selected = ranking[ranking.policy == SOURCE_POLICY_NAME]
    if len(selected) != 1:
        raise RuntimeError(f"expected one V269 source row, got {len(selected)}")
    row = selected.iloc[0].to_dict()
    required_pass = {
        "cagr": float(row["development_cagr"]) >= 0.04,
        "sharpe": float(row["development_sharpe"]) >= 0.80,
        "drawdown": float(row["development_max_drawdown"]) >= -0.15,
        "turnover": float(row["development_annual_turnover"]) <= 12.0,
        "years": bool(row["all_development_years_positive"]),
        "long_leg": float(row["long_leg_pnl"]) > 0.0,
        "short_leg": float(row["short_leg_pnl"]) > 0.0,
        "breadth": int(row["symbol_count_traded"]) >= 10,
        "concentration": float(row["top_positive_asset_pnl_share"]) <= 0.35,
    }
    if not all(required_pass.values()):
        raise RuntimeError(f"V269 source policy was not the declared sole-gate near-miss: {required_pass}")
    if float(row["development_max_gross"]) <= 0.70:
        raise RuntimeError("V269 source policy did not fail the declared gross gate")
    return clean(row)


def development_gate_results(
    values: dict[str, Any], diagnostics: dict[str, Any], yearly: pd.DataFrame
) -> dict[str, bool]:
    all_years_positive = bool(
        not yearly.empty and (pd.to_numeric(yearly["return"], errors="coerce") > 0.0).all()
    )
    return {
        "cagr": float(values["cagr"]) >= DEVELOPMENT_GATES["cagr_min"],
        "sharpe": float(values["sharpe"]) >= DEVELOPMENT_GATES["sharpe_min"],
        "max_drawdown": float(values["max_drawdown"]) >= DEVELOPMENT_GATES["max_drawdown_min"],
        "rebalance_events": int(diagnostics["rebalance_events"])
        >= DEVELOPMENT_GATES["rebalance_events_min"],
        "annual_turnover": float(values["annual_turnover"])
        <= DEVELOPMENT_GATES["annual_turnover_max"],
        "max_realized_gross": float(values["max_gross"])
        <= DEVELOPMENT_GATES["max_realized_gross"],
        "all_development_years_positive": all_years_positive,
        "net_long_leg_pnl_positive": float(diagnostics["long_leg_pnl"]) > 0.0,
        "net_short_leg_pnl_positive": float(diagnostics["short_leg_pnl"]) > 0.0,
        "symbols_traded": int(diagnostics["symbol_count_traded"])
        >= DEVELOPMENT_GATES["symbols_traded_min"],
        "concentration": float(diagnostics["top_positive_asset_pnl_share"])
        <= DEVELOPMENT_GATES["top_positive_asset_pnl_share_max"],
    }


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


def self_test() -> None:
    assert TARGET_GROSS == 0.45
    assert POLICY.name == SOURCE_POLICY_NAME
    lottery.self_test()
    print("V277-V284 exact-policy self-test passed")


def run(root: Path, cache: Path) -> int:
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
    source_row = load_source_row()
    config = lottery.base.V9Config(
        symbols=SYMBOLS,
        start="2020-01-01",
        end_exclusive=END_EXCLUSIVE,
        interval="1d",
        starting_equity=INITIAL_EQUITY,
        max_gross=TARGET_GROSS,
        forced_exit_penalty_bps=FORCED_EXIT_PENALTY_BPS,
    )
    klines, funding, records, quality = lottery.base.load_v9(config, cache, False)
    pd.DataFrame(records).to_csv(results / "data_manifest.csv", index=False)
    pd.DataFrame(quality).to_csv(results / "data_quality.csv", index=False)
    gate = lottery.base.data_gate(klines, records)
    gate = {**gate, "candidate": "V277_FIXED_UNIVERSE_DATA_COVERAGE"}
    write_json(results / "coverage_gate.json", gate)
    if not gate["passed"]:
        proof = {
            "candidate": CANDIDATE,
            "selection_not_run": True,
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
        summary = {**decision, "coverage_gate": gate, "selection": proof}
        write_json(results / "selection_proof_before_oos.json", proof)
        write_json(results / "FROZEN_DECISION.json", decision)
        write_json(results / "summary.json", summary)
        pd.DataFrame().to_csv(results / "audit_metrics.csv", index=False)
        (results / "REPORT_RU.md").write_text(
            "# Active V277–V284\n\nStatus: `data_access_insufficient`. OOS not opened.\n"
        )
        write_manifest(root)
        return 0

    market = lottery.base.Market(klines, funding)
    weights = lottery.build_weights(market, POLICY, {}, {})
    weights.to_csv(results / "frozen_weights.csv")
    development_account, development_diagnostics = lottery.base.simulate(
        market, weights, START, DEVELOPMENT_END_EXCLUSIVE, AUDITS[0]
    )
    development_metrics = lottery.base.account_metrics(development_account)
    development_years = lottery.base.yearly_returns(development_account, "return")
    development_years.to_csv(results / "DEVELOPMENT_ANNUAL_RETURNS.csv", index=False)
    dev_gates = development_gate_results(
        development_metrics, development_diagnostics, development_years
    )
    development_passed = bool(all(dev_gates.values()))

    proof = {
        "candidate": CANDIDATE,
        "hypothesis_generated_from_v269_development": True,
        "source_policy": SOURCE_POLICY_NAME,
        "source_evidence": SOURCE_EVIDENCE,
        "source_row": source_row,
        "frozen_target_gross": TARGET_GROSS,
        "maximum_realized_close_gross": MAX_REALIZED_GROSS,
        "neighboring_scales_tested": 0,
        "selection_uses_2024": False,
        "selection_uses_2025": False,
        "selection_uses_2026": False,
        "program_level_holdout_pristine": False,
        "development_metrics": development_metrics,
        "development_diagnostics": development_diagnostics,
        "development_years": development_years.to_dict(orient="records"),
        "development_gate_results": dev_gates,
        "development_reproof_passed": development_passed,
        "coverage_gate": gate,
        "design_sha256": sha256_file(root / "V277_V284_DESIGN.json"),
        "source_summary_sha256": sha256_file(LOTTERY_ROOT / "results" / "summary.json"),
        "source_ranking_file_sha256": sha256_file(
            LOTTERY_ROOT / "results" / "selection_ranking_before_validation.csv"
        ),
    }
    proof["selection_proof_sha256"] = canonical_hash(proof)
    write_json(results / "selection_proof_before_oos.json", proof)

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
            "failed_development_gates": [key for key, value in dev_gates.items() if not value],
        }
        write_json(results / "FROZEN_DECISION.json", decision)
        write_json(results / "summary.json", summary)
        pd.DataFrame().to_csv(results / "audit_metrics.csv", index=False)
        (results / "REPORT_RU.md").write_text(
            "# Active V277–V284 — exact low-skew risk normalization\n\n"
            "Status: `rejected_before_oos`. The 0.45x development reproof did not pass; OOS remained closed.\n"
        )
        write_manifest(root)
        print(json.dumps(clean(summary), indent=2))
        return 0

    period_bounds = {
        "development": (START, DEVELOPMENT_END_EXCLUSIVE),
        "validation": (VALIDATION_START, VALIDATION_END_EXCLUSIVE),
        "holdout": (HOLDOUT_START, HOLDOUT_END_EXCLUSIVE),
        "final": (FINAL_START, END_EXCLUSIVE),
        "full": (START, END_EXCLUSIVE),
    }
    audit_rows: list[dict[str, Any]] = []
    diagnostics_by_audit: dict[str, dict[str, Any]] = {}
    accounts: dict[tuple[str, str], pd.DataFrame] = {}
    for audit in AUDITS:
        audit_diagnostics: dict[str, Any] = {}
        row: dict[str, Any] = {"audit": audit.name, **asdict(audit)}
        for period, (period_start, period_end) in period_bounds.items():
            account, diagnostics = lottery.base.simulate(
                market, weights, period_start, period_end, audit
            )
            accounts[(audit.name, period)] = account
            audit_diagnostics[period] = diagnostics
            metrics = lottery.base.account_metrics(account)
            for key, value in metrics.items():
                row[f"{period}_{key}"] = value
            row[f"{period}_long_leg_pnl"] = diagnostics.get("long_leg_pnl", 0.0)
            row[f"{period}_short_leg_pnl"] = diagnostics.get("short_leg_pnl", 0.0)
            row[f"{period}_top_positive_asset_pnl_share"] = diagnostics.get(
                "top_positive_asset_pnl_share", 1.0
            )
            row[f"{period}_symbol_count_traded"] = diagnostics.get("symbol_count_traded", 0)
            row[f"{period}_forced_exit_count"] = diagnostics.get("forced_exit_count", 0)
        diagnostics_by_audit[audit.name] = audit_diagnostics
        audit_rows.append(row)

    audits = pd.DataFrame(audit_rows)
    audits.to_csv(results / "audit_metrics.csv", index=False)
    for audit in AUDITS:
        accounts[(audit.name, "full")].to_csv(results / f"equity_{audit.name}.csv")

    base_row = audits[audits.audit == "base"].iloc[0]
    severe_row = audits[audits.audit == "severe"].iloc[0]
    extreme_row = audits[audits.audit == "extreme"].iloc[0]
    delay_row = audits[audits.audit == "delay_1d"].iloc[0]
    annual = lottery.base.yearly_returns(accounts[("base", "full")], SOURCE_POLICY_NAME)
    annual.to_csv(results / "ANNUAL_RETURNS.csv", index=False)
    worst_year = float(pd.to_numeric(annual[SOURCE_POLICY_NAME], errors="coerce").min())
    all_audits_max_gross = float(audits.full_max_gross.max())
    base_full_diagnostics = diagnostics_by_audit["base"]["full"]
    post_gate_results = {
        "validation_return_positive": float(base_row.validation_total_return) > 0.0,
        "holdout_return_positive": float(base_row.holdout_total_return) > 0.0,
        "final_return_positive": float(base_row.final_total_return) > 0.0,
        "full_cagr": float(base_row.full_cagr) >= POST_OOS_GATES["full_cagr_min"],
        "full_sharpe": float(base_row.full_sharpe) >= POST_OOS_GATES["full_sharpe_min"],
        "full_max_drawdown": float(base_row.full_max_drawdown)
        >= POST_OOS_GATES["full_max_drawdown_min"],
        "severe_full_cagr_positive": float(severe_row.full_cagr) > 0.0,
        "extreme_full_cagr_positive": float(extreme_row.full_cagr) > 0.0,
        "latency_full_cagr_positive": float(delay_row.full_cagr) > 0.0,
        "worst_calendar_year": worst_year >= POST_OOS_GATES["worst_calendar_year_min"],
        "full_long_leg_pnl_positive": float(base_full_diagnostics["long_leg_pnl"]) > 0.0,
        "full_short_leg_pnl_positive": float(base_full_diagnostics["short_leg_pnl"]) > 0.0,
        "all_audits_max_gross": all_audits_max_gross
        <= POST_OOS_GATES["all_audits_max_gross"],
        "concentration": float(base_full_diagnostics["top_positive_asset_pnl_share"])
        <= POST_OOS_GATES["top_positive_asset_pnl_share_max"],
        "forced_exit_count": int(base_full_diagnostics["forced_exit_count"])
        <= POST_OOS_GATES["forced_exit_count_max"],
    }
    standalone_passed = bool(all(post_gate_results.values()))
    decision = {
        "candidate": CANDIDATE,
        "status": (
            "paper_forward_candidate_non_pristine_oos"
            if standalone_passed
            else "rejected_after_oos"
        ),
        "development_reproof_passed": True,
        "oos_opened": True,
        "standalone_selection_passed": standalone_passed,
        "integration_permitted": False,
        "promoted_candidates": [SOURCE_POLICY_NAME] if standalone_passed else [],
        "paper_forward_earliest_start": "2026-07-27" if standalone_passed else None,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    summary = {
        **decision,
        "selection": proof,
        "coverage_gate": gate,
        "post_oos_gate_results": post_gate_results,
        "base_audit": clean(base_row.to_dict()),
        "severe_audit": clean(severe_row.to_dict()),
        "extreme_audit": clean(extreme_row.to_dict()),
        "delay_audit": clean(delay_row.to_dict()),
        "annual_returns": annual.to_dict(orient="records"),
        "diagnostics_by_audit": diagnostics_by_audit,
        "all_audits_max_gross": all_audits_max_gross,
        "limitations": [
            "The exact policy was generated from V269 development diagnostics.",
            "The program-level 2024–2026 holdout is not pristine.",
            "Public daily archives are not executable bid/ask or queue observations.",
            "A historical pass permits only paper-forward observation after 2026-07-27.",
        ],
        "data_manifest_sha256": canonical_hash(records),
    }
    write_json(results / "FROZEN_DECISION.json", decision)
    write_json(results / "summary.json", summary)
    (results / "REPORT_RU.md").write_text(
        "# Active V277–V284 — exact low-skew risk normalization\n\n"
        f"Status: `{decision['status']}`.\n\n"
        f"Development reproof CAGR: {float(development_metrics['cagr']):+.2%}.\n\n"
        f"Validation: {float(base_row.validation_total_return):+.2%}; "
        f"holdout: {float(base_row.holdout_total_return):+.2%}; "
        f"final: {float(base_row.final_total_return):+.2%}.\n\n"
        f"Full base CAGR: {float(base_row.full_cagr):+.2%}; "
        f"Sharpe: {float(base_row.full_sharpe):.3f}; "
        f"Max DD: {float(base_row.full_max_drawdown):+.2%}.\n"
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
