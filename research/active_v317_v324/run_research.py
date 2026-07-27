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
SOURCE = REPO_ROOT / "research" / "active_v301_v308" / "run_research.py"
_spec = importlib.util.spec_from_file_location("v301_exact_coskew_source", SOURCE)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"cannot import V301 source from {SOURCE}")
alpha = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = alpha
_spec.loader.exec_module(alpha)

v = alpha.v
base = v.base

CANDIDATE = "ACTIVE_V317_EXACT_COSKEW_BETA_HEDGE"
SOURCE_POLICY = "low_systematic_coskewness_l365_k4_r14_beta"
SYMBOLS = tuple(v.SYMBOLS)
START = "2021-01-01"
DEVELOPMENT_END_EXCLUSIVE = "2024-01-01"
VALIDATION_START = "2024-01-01"
VALIDATION_END_EXCLUSIVE = "2025-01-01"
HOLDOUT_START = "2025-01-01"
HOLDOUT_END_EXCLUSIVE = "2026-01-01"
FINAL_START = "2026-01-01"
END_EXCLUSIVE = "2026-07-01"
INITIAL_EQUITY = 10_000.0
ALPHA_LOOKBACK_DAYS = 365
BETA_LOOKBACK_DAYS = 90
LONG_ASSET_COUNT = 4
REBALANCE_DAYS = 14
LONG_GROSS = 0.25
MAX_HEDGE_GROSS = 0.35
MAX_TARGET_GROSS = 0.60
MAX_REALIZED_GROSS = 0.70
REALIZED_BETA_ABS_MAX = 0.20
FORCED_EXIT_PENALTY_BPS = 100.0

base.INITIAL_EQUITY = INITIAL_EQUITY
base.TARGET_GROSS = MAX_TARGET_GROSS
base.MAX_REALIZED_GROSS = MAX_REALIZED_GROSS
base.FORCED_EXIT_PENALTY_BPS = FORCED_EXIT_PENALTY_BPS

AUDITS = (
    v.Audit("base", 30.0),
    v.Audit("severe", 60.0),
    v.Audit("extreme", 100.0),
    v.Audit("delay_1d", 30.0, execution_delay_days=1),
)
DEVELOPMENT_GATES = {
    "cagr_min": 0.05,
    "sharpe_min": 1.00,
    "max_drawdown_min": -0.15,
    "rebalance_events_min": 18,
    "annual_turnover_max": 15.0,
    "max_realized_gross": MAX_REALIZED_GROSS,
    "all_development_years_positive": True,
    "net_long_side_pnl_positive": True,
    "symbols_traded_min": 8,
    "top_positive_asset_pnl_share_max": 0.35,
    "realized_beta_abs_max": REALIZED_BETA_ABS_MAX,
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
    "full_net_long_side_pnl_positive": True,
    "full_realized_beta_abs_max": REALIZED_BETA_ABS_MAX,
    "all_audits_max_gross": MAX_REALIZED_GROSS,
    "top_positive_asset_pnl_share_max": 0.35,
    "forced_exit_count_max": 4,
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


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        clean(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_raw_targets(market: Any) -> pd.DataFrame:
    score = alpha.score_frame(market, "low_systematic_coskewness", ALPHA_LOOKBACK_DAYS)
    values = score.reindex(market.index).to_numpy(float)
    available = market.available.to_numpy(bool)
    volatility = market.vol.to_numpy(float)
    beta = market.beta(BETA_LOOKBACK_DAYS).shift(1).to_numpy(float)
    output = np.zeros_like(values)
    btc = market.symbols.index("BTCUSDT")
    eth = market.symbols.index("ETHUSDT")
    hedge_indices = np.array([btc, eth], dtype=int)

    for i in range(len(values)):
        valid = np.flatnonzero(
            available[i]
            & np.isfinite(values[i])
            & np.isfinite(volatility[i])
            & (volatility[i] > 1e-6)
            & np.isfinite(beta[i])
        )
        if len(valid) < LONG_ASSET_COUNT:
            continue
        if not all(index in valid for index in hedge_indices):
            continue
        order = valid[np.argsort(values[i, valid])]
        long_indices = order[-LONG_ASSET_COUNT:]
        long_weights = 1.0 / volatility[i, long_indices]
        long_weights *= LONG_GROSS / long_weights.sum()

        hedge_weights = 1.0 / volatility[i, hedge_indices]
        hedge_weights /= hedge_weights.sum()
        long_beta_exposure = float(np.sum(long_weights * beta[i, long_indices]))
        hedge_beta_per_gross = float(np.sum(hedge_weights * beta[i, hedge_indices]))
        hedge_gross = 0.0
        if (
            np.isfinite(long_beta_exposure)
            and np.isfinite(hedge_beta_per_gross)
            and long_beta_exposure > 0.0
            and hedge_beta_per_gross > 1e-6
        ):
            hedge_gross = float(
                np.clip(long_beta_exposure / hedge_beta_per_gross, 0.0, MAX_HEDGE_GROSS)
            )

        row = np.zeros(len(market.symbols), dtype=float)
        row[long_indices] += long_weights
        row[hedge_indices] -= hedge_gross * hedge_weights
        gross = float(np.abs(row).sum())
        if gross > MAX_TARGET_GROSS:
            row *= MAX_TARGET_GROSS / gross
        output[i] = row

    return pd.DataFrame(output, index=market.index, columns=market.symbols)


def build_weights(market: Any) -> pd.DataFrame:
    raw = build_raw_targets(market)
    return base.schedule_weights(raw, market.available, REBALANCE_DAYS)


def elapsed_years(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 0.0
    return max((index[-1] - index[0]).total_seconds() / (365.2425 * 86400.0), 1 / 365.2425)


def realized_beta(account: pd.DataFrame, market: Any) -> dict[str, float]:
    if account.empty:
        return {"beta": 0.0, "correlation": 0.0, "observations": 0}
    strategy_return = account.equity.pct_change().replace([np.inf, -np.inf], np.nan)
    market_return = market.market.reindex(account.index)
    frame = pd.concat(
        [strategy_return.rename("strategy"), market_return.rename("market")], axis=1
    ).dropna()
    if len(frame) < 20:
        return {"beta": 0.0, "correlation": 0.0, "observations": int(len(frame))}
    variance = float(frame.market.var(ddof=1))
    beta_value = float(frame.strategy.cov(frame.market) / variance) if variance > 0 else 0.0
    correlation = float(frame.strategy.corr(frame.market))
    return {
        "beta": beta_value,
        "correlation": correlation if np.isfinite(correlation) else 0.0,
        "observations": int(len(frame)),
    }


def target_beta_diagnostics(weights: pd.DataFrame, market: Any) -> dict[str, float]:
    beta = market.beta(BETA_LOOKBACK_DAYS).shift(1).reindex(weights.index)
    exposure = (weights * beta).sum(axis=1, min_count=1).dropna()
    if exposure.empty:
        return {"mean_abs_target_beta": 0.0, "max_abs_target_beta": 0.0}
    return {
        "mean_abs_target_beta": float(exposure.abs().mean()),
        "max_abs_target_beta": float(exposure.abs().max()),
    }


def yearly_positive(account: pd.DataFrame) -> tuple[bool, pd.DataFrame]:
    years = base.yearly_returns(account, "return")
    passed = bool(
        not years.empty and (pd.to_numeric(years["return"], errors="coerce") > 0.0).all()
    )
    return passed, years


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
    market = alpha.synthetic_market()
    weights = build_weights(market)
    assert np.isfinite(weights.to_numpy()).all()
    assert float(weights.abs().sum(axis=1).max()) <= MAX_TARGET_GROSS + 1e-12
    account, diagnostics = base.simulate(
        market, weights, "2021-01-01", "2023-01-01", AUDITS[0]
    )
    assert not account.empty
    assert np.isfinite(account.equity).all()
    assert diagnostics["rebalance_events"] >= 18

    changed: dict[str, pd.DataFrame] = {}
    for symbol in market.symbols:
        changed[symbol] = pd.DataFrame(
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
    changed[market.symbols[0]].iloc[-1, changed[market.symbols[0]].columns.get_loc("close")] *= 4.0
    changed_market = base.Market(
        changed, {symbol: pd.Series(0.0, index=market.index) for symbol in market.symbols}
    )
    changed_weights = build_weights(changed_market)
    pd.testing.assert_frame_equal(weights.iloc[:-1], changed_weights.iloc[:-1])
    print("V317-V324 exact coskewness beta-hedge self-test passed")


def failure_outputs(root: Path, gate: dict[str, Any], records: list[dict[str, Any]]) -> None:
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
    proof = {
        "candidate": CANDIDATE,
        "selection_not_run": True,
        "oos_opened": False,
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
        "# Active V317–V324 — exact coskewness beta hedge\n\n"
        "Status: `data_access_insufficient`. P&L and OOS were not opened.\n"
    )
    write_manifest(root)


def run(root: Path, cache: Path) -> int:
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
    config = base.V9Config(
        symbols=SYMBOLS,
        start="2020-01-01",
        end_exclusive=END_EXCLUSIVE,
        interval="1d",
        starting_equity=INITIAL_EQUITY,
        max_gross=MAX_TARGET_GROSS,
        forced_exit_penalty_bps=FORCED_EXIT_PENALTY_BPS,
    )
    klines, funding, records, quality = base.load_v9(config, cache, False)
    pd.DataFrame(records).to_csv(results / "data_manifest.csv", index=False)
    pd.DataFrame(quality).to_csv(results / "data_quality.csv", index=False)
    gate = base.data_gate(klines, records)
    gate = {**gate, "candidate": "V317_FIXED_UNIVERSE_DATA_COVERAGE"}
    write_json(results / "coverage_gate.json", gate)
    if not gate["passed"]:
        failure_outputs(root, gate, records)
        return 0

    market = base.Market(klines, funding)
    weights = build_weights(market)
    weights.to_csv(results / "frozen_weights.csv")
    target_beta = target_beta_diagnostics(weights, market)

    development_account, development_diagnostics = base.simulate(
        market, weights, START, DEVELOPMENT_END_EXCLUSIVE, AUDITS[0]
    )
    development_metrics = base.account_metrics(development_account)
    development_beta = realized_beta(development_account, market)
    all_years_positive, development_years = yearly_positive(development_account)
    development_years.to_csv(results / "DEVELOPMENT_ANNUAL_RETURNS.csv", index=False)

    development_gate_results = {
        "cagr": float(development_metrics["cagr"]) >= DEVELOPMENT_GATES["cagr_min"],
        "sharpe": float(development_metrics["sharpe"]) >= DEVELOPMENT_GATES["sharpe_min"],
        "max_drawdown": float(development_metrics["max_drawdown"])
        >= DEVELOPMENT_GATES["max_drawdown_min"],
        "rebalance_events": int(development_diagnostics["rebalance_events"])
        >= DEVELOPMENT_GATES["rebalance_events_min"],
        "annual_turnover": float(development_metrics["annual_turnover"])
        <= DEVELOPMENT_GATES["annual_turnover_max"],
        "max_realized_gross": float(development_metrics["max_gross"])
        <= DEVELOPMENT_GATES["max_realized_gross"],
        "all_development_years_positive": all_years_positive,
        "net_long_side_pnl_positive": float(development_diagnostics["long_leg_pnl"]) > 0.0,
        "symbols_traded": int(development_diagnostics["symbol_count_traded"])
        >= DEVELOPMENT_GATES["symbols_traded_min"],
        "concentration": float(development_diagnostics["top_positive_asset_pnl_share"])
        <= DEVELOPMENT_GATES["top_positive_asset_pnl_share_max"],
        "realized_beta": abs(float(development_beta["beta"]))
        <= DEVELOPMENT_GATES["realized_beta_abs_max"],
    }
    development_passed = bool(all(development_gate_results.values()))
    proof = {
        "candidate": CANDIDATE,
        "source_policy": SOURCE_POLICY,
        "source_cycle": "V301-V308",
        "source_selection_reason": (
            "unique V301 promotable process passing every development gate except "
            "all-years-positive and cross-sectional short-leg profitability"
        ),
        "neighboring_alpha_parameters_tested": 0,
        "neighboring_hedge_parameters_tested": 0,
        "selection_cutoff": "2023-12-31T23:59:59Z",
        "selection_uses_2024": False,
        "selection_uses_2025": False,
        "selection_uses_2026": False,
        "program_level_holdout_pristine": False,
        "long_gross": LONG_GROSS,
        "maximum_hedge_gross": MAX_HEDGE_GROSS,
        "maximum_target_gross": MAX_TARGET_GROSS,
        "rebalance_days": REBALANCE_DAYS,
        "development_gates": DEVELOPMENT_GATES,
        "post_oos_gates": POST_OOS_GATES,
        "development_metrics": development_metrics,
        "development_diagnostics": development_diagnostics,
        "development_beta": development_beta,
        "target_beta_diagnostics": target_beta,
        "development_gate_results": development_gate_results,
        "development_reproof_passed": development_passed,
        "oos_opened": development_passed,
        "coverage_gate": gate,
        "design_sha256": sha256_file(root / "V317_V324_DESIGN.json"),
        "data_manifest_sha256": canonical_hash(records),
    }
    proof["selection_proof_sha256"] = canonical_hash(proof)
    write_json(results / "selection_proof_before_oos.json", proof)

    limitations = [
        "Program-level OOS is not pristine.",
        "Public daily archives are not executable bid/ask or queue observations.",
        "The fixed universe includes delisted assets; forced exits carry a 100 bps penalty.",
        "The BTC/ETH hedge is estimated from rolling daily beta and cannot guarantee intraday neutrality.",
        "A historical pass can authorize only paper-forward monitoring after 27 July 2026.",
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
            "development_beta": development_beta,
            "target_beta_diagnostics": target_beta,
            "development_gate_results": development_gate_results,
            "limitations": limitations,
        }
        write_json(results / "FROZEN_DECISION.json", decision)
        write_json(results / "summary.json", summary)
        pd.DataFrame().to_csv(results / "audit_metrics.csv", index=False)
        (results / "REPORT_RU.md").write_text(
            "# Active V317–V324 — exact coskewness beta hedge\n\n"
            "Status: `rejected_before_oos`. Development reproof failed; 2024–2026 were not opened.\n"
        )
        write_manifest(root)
        print(json.dumps(clean(summary), indent=2))
        return 0

    audit_rows: list[dict[str, Any]] = []
    audit_accounts: dict[str, pd.DataFrame] = {}
    audit_diagnostics: dict[str, dict[str, Any]] = {}
    audit_betas: dict[str, dict[str, Any]] = {}
    for audit in AUDITS:
        account, diagnostics = base.simulate(market, weights, START, END_EXCLUSIVE, audit)
        audit_accounts[audit.name] = account
        audit_diagnostics[audit.name] = diagnostics
        segments = {
            "full": account,
            "development": base.slice_account(account, START, DEVELOPMENT_END_EXCLUSIVE),
            "validation": base.slice_account(account, VALIDATION_START, VALIDATION_END_EXCLUSIVE),
            "holdout": base.slice_account(account, HOLDOUT_START, HOLDOUT_END_EXCLUSIVE),
            "final": base.slice_account(account, FINAL_START, END_EXCLUSIVE),
        }
        row: dict[str, Any] = {"audit": audit.name, **asdict(audit)}
        beta_by_segment: dict[str, Any] = {}
        for segment_name, segment in segments.items():
            values = base.account_metrics(segment)
            beta_values = realized_beta(segment, market)
            beta_by_segment[segment_name] = beta_values
            for key, value in values.items():
                row[f"{segment_name}_{key}"] = value
            row[f"{segment_name}_realized_beta"] = beta_values["beta"]
        row["long_leg_pnl"] = diagnostics["long_leg_pnl"]
        row["short_hedge_pnl"] = diagnostics["short_leg_pnl"]
        row["symbol_count_traded"] = diagnostics["symbol_count_traded"]
        row["top_positive_asset_pnl_share"] = diagnostics["top_positive_asset_pnl_share"]
        audit_rows.append(row)
        audit_betas[audit.name] = beta_by_segment

    audits = pd.DataFrame(audit_rows)
    audits.to_csv(results / "audit_metrics.csv", index=False)
    base_row = audits[audits.audit == "base"].iloc[0]
    severe_row = audits[audits.audit == "severe"].iloc[0]
    extreme_row = audits[audits.audit == "extreme"].iloc[0]
    delay_row = audits[audits.audit == "delay_1d"].iloc[0]
    annual = base.yearly_returns(audit_accounts["base"], CANDIDATE)
    annual.to_csv(results / "ANNUAL_RETURNS.csv", index=False)
    worst_year = float(pd.to_numeric(annual[CANDIDATE], errors="coerce").min())
    base_diagnostics = audit_diagnostics["base"]
    all_audits_gross_pass = bool(
        all(float(row.full_max_gross) <= POST_OOS_GATES["all_audits_max_gross"] for _, row in audits.iterrows())
    )
    post_results = {
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
        "full_net_long_side_pnl_positive": float(base_diagnostics["long_leg_pnl"]) > 0.0,
        "full_realized_beta": abs(float(base_row.full_realized_beta))
        <= POST_OOS_GATES["full_realized_beta_abs_max"],
        "all_audits_max_gross": all_audits_gross_pass,
        "concentration": float(base_diagnostics["top_positive_asset_pnl_share"])
        <= POST_OOS_GATES["top_positive_asset_pnl_share_max"],
        "forced_exit_count": int(base_diagnostics["forced_exit_count"])
        <= POST_OOS_GATES["forced_exit_count_max"],
    }
    passed = bool(all(post_results.values()))
    decision = {
        "candidate": CANDIDATE,
        "status": "paper_forward_candidate_non_pristine_oos" if passed else "rejected_after_oos",
        "development_reproof_passed": True,
        "oos_opened": True,
        "standalone_selection_passed": passed,
        "integration_permitted": False,
        "promoted_candidates": [CANDIDATE] if passed else [],
        "paper_forward_earliest_start": "2026-07-27" if passed else None,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    summary = {
        **decision,
        "selection": proof,
        "coverage_gate": gate,
        "development_gate_results": development_gate_results,
        "post_oos_gate_results": post_results,
        "base_audit": clean(base_row.to_dict()),
        "severe_audit": clean(severe_row.to_dict()),
        "extreme_audit": clean(extreme_row.to_dict()),
        "delay_audit": clean(delay_row.to_dict()),
        "audit_diagnostics": audit_diagnostics,
        "audit_betas": audit_betas,
        "target_beta_diagnostics": target_beta,
        "annual_returns": annual.to_dict(orient="records"),
        "limitations": limitations,
    }
    write_json(results / "FROZEN_DECISION.json", decision)
    write_json(results / "summary.json", summary)
    (results / "REPORT_RU.md").write_text(
        "# Active V317–V324 — exact coskewness beta hedge\n\n"
        f"Status: `{decision['status']}`.\n\n"
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
