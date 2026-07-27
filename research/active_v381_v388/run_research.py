#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from dataclasses import asdict
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / "research" / "active_v269_v276" / "run_research.py"
_SPEC = importlib.util.spec_from_file_location("v269_vol_term_base", SOURCE)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot import fixed-universe engine from {SOURCE}")
v = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = v
_SPEC.loader.exec_module(v)

CANDIDATE = "ACTIVE_V381_CRYPTO_RESIDUAL_ENTROPY"
FAMILIES = (
    "low_residual_sign_entropy",
    "low_residual_state_entropy",
    "low_residual_transition_entropy",
    "reversed_high_entropy_control",
)
PROMOTABLE_FAMILIES = FAMILIES[:-1]
WINDOWS = {60: (60, 60), 120: (120, 120), 240: (240, 240)}
TARGET_GROSS = 0.40
MAX_REALIZED_GROSS = 0.70
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
POST_SELECTION_GATES = {
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
    "top_positive_asset_pnl_share_max": 0.35,
    "forced_exit_count_max": 4,
}


def clean(value: Any) -> Any:
    return v.clean(value)


def write_json(path: Path, value: Any) -> None:
    v.write_json(path, value)


def canonical_hash(value: Any) -> str:
    return v.canonical_hash(value)


def normalized_state_entropy(
    states: pd.DataFrame,
    window: int,
    categories: tuple[float, ...],
) -> pd.DataFrame:
    entropy = pd.DataFrame(0.0, index=states.index, columns=states.columns)
    for category in categories:
        indicator = states.eq(category).where(states.notna()).astype(float)
        probability = indicator.rolling(window, min_periods=window).mean()
        term = -(probability * np.log(probability.where(probability > 0.0)))
        entropy = entropy.add(term.fillna(0.0), fill_value=0.0)
    complete = (
        states.notna().astype(float).rolling(window, min_periods=window).sum()
        >= float(window)
    )
    return (entropy / np.log(float(len(categories)))).where(complete)


def score_frame(market: Any, family: str, lookback_key: int) -> pd.DataFrame:
    lookback = int(lookback_key)
    beta = market.beta(90).shift(1)
    residual = market.logret - beta.mul(market.market, axis=0)

    sign_state = residual.gt(0.0).astype(float).where(residual.notna())
    sign_entropy = normalized_state_entropy(sign_state, lookback, (0.0, 1.0))

    scale = residual.rolling(60, min_periods=60).std(ddof=1).shift(1)
    standardized = residual / scale.replace(0.0, np.nan)
    state = pd.DataFrame(np.nan, index=residual.index, columns=residual.columns)
    state = state.mask(standardized < -0.5, -1.0)
    state = state.mask(standardized.abs() <= 0.5, 0.0)
    state = state.mask(standardized > 0.5, 1.0)
    state_entropy = normalized_state_entropy(state, lookback, (-1.0, 0.0, 1.0))

    previous_sign = sign_state.shift(1)
    transition = (2.0 * previous_sign + sign_state).where(
        previous_sign.notna() & sign_state.notna()
    )
    transition_entropy = normalized_state_entropy(
        transition, lookback, (0.0, 1.0, 2.0, 3.0)
    )

    if family == "low_residual_sign_entropy":
        score = -sign_entropy
    elif family == "low_residual_state_entropy":
        score = -state_entropy
    elif family == "low_residual_transition_entropy":
        score = -transition_entropy
    elif family == "reversed_high_entropy_control":
        score = state_entropy
    else:
        raise ValueError(family)
    return v.winsorize_cross_section(score)


def configure_engine() -> None:
    v.CANDIDATE = CANDIDATE
    v.FAMILIES = FAMILIES
    v.PROMOTABLE_FAMILIES = PROMOTABLE_FAMILIES
    v.TARGET_GROSS = TARGET_GROSS
    v.MAX_REALIZED_GROSS = MAX_REALIZED_GROSS
    v.base.TARGET_GROSS = TARGET_GROSS
    v.base.MAX_REALIZED_GROSS = MAX_REALIZED_GROSS
    v.DEVELOPMENT_GATES = DEVELOPMENT_GATES
    v.POST_SELECTION_GATES = POST_SELECTION_GATES
    v.POLICIES = tuple(
        v.Policy(*values)
        for values in product(
            FAMILIES,
            tuple(WINDOWS),
            (3, 4),
            (14, 28, 56),
            ("dollar", "beta"),
        )
    )
    v.AUDITS = (
        v.Audit("base", 30.0),
        v.Audit("severe", 60.0),
        v.Audit("extreme", 100.0),
        v.Audit("delay_1d", 30.0, execution_delay_days=1),
    )
    v.score_frame = score_frame


def synthetic_market() -> Any:
    index = pd.date_range("2018-01-01", periods=2100, freq="1D", tz="UTC")
    rng = np.random.default_rng(341)
    common = rng.normal(0.0001, 0.012, len(index))
    klines: dict[str, pd.DataFrame] = {}
    funding: dict[str, pd.Series] = {}
    for number, symbol in enumerate(v.SYMBOLS[:10]):
        regime = np.where((np.arange(len(index)) // 120 + number) % 2 == 0, 0.65, 1.35)
        residual = rng.normal(0.0, 0.006 + number * 0.00035, len(index)) * regime
        returns = 0.65 * common + residual
        close = 100.0 * np.exp(np.cumsum(returns))
        gap = rng.normal(0.0, 0.0015, len(index))
        open_price = np.r_[close[0], close[:-1] * np.exp(gap[1:])]
        width = np.abs(rng.normal(0.012, 0.004, len(index))) * regime
        klines[symbol] = pd.DataFrame(
            {
                "open": open_price,
                "high": np.maximum(open_price, close) * (1.0 + width),
                "low": np.minimum(open_price, close) / (1.0 + width),
                "close": close,
                "volume": 1.0,
                "quote_volume": 1.0,
                "trades": 1.0,
                "taker_buy_base": 0.5,
                "taker_buy_quote": 0.5,
            },
            index=index,
        )
        funding[symbol] = pd.Series(0.0, index=index)
    return v.base.Market(klines, funding)


def self_test() -> None:
    configure_engine()
    assert len(v.POLICIES) == 144
    assert sum(policy.family in PROMOTABLE_FAMILIES for policy in v.POLICIES) == 108
    market = synthetic_market()
    policy = v.Policy("low_residual_sign_entropy", 60, 3, 14, "dollar")
    weights = v.build_weights(market, policy, {}, {})
    account, diagnostics = v.base.simulate(
        market, weights, "2021-01-01", "2023-01-01", v.AUDITS[0]
    )
    assert not account.empty
    assert np.isfinite(account.equity).all()
    assert diagnostics["symbol_count_traded"] >= 6
    assert diagnostics["rebalance_events"] >= 20

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
    changed_frames[first].iloc[-1, changed_frames[first].columns.get_loc("close")] *= 4.0
    changed_market = v.base.Market(
        changed_frames,
        {symbol: pd.Series(0.0, index=market.index) for symbol in market.symbols},
    )
    changed_weights = v.build_weights(changed_market, policy, {}, {})
    pd.testing.assert_frame_equal(weights.iloc[:-1], changed_weights.iloc[:-1])
    print("V381-V388 causal residual-entropy self-test passed")


def stricter_postprocess(root: Path) -> None:
    results = root / "results"
    proof_path = results / "selection_proof_before_validation.json"
    summary_path = results / "summary.json"
    decision_path = results / "FROZEN_DECISION.json"
    proof = json.loads(proof_path.read_text())
    proof["candidate"] = CANDIDATE
    proof["development_gates"] = DEVELOPMENT_GATES
    proof["post_selection_gates"] = POST_SELECTION_GATES
    proof["coverage_gate"]["candidate"] = "V381_FIXED_UNIVERSE_DATA_COVERAGE"
    proof["lookbacks"] = list(WINDOWS)
    proof["design_sha256"] = hashlib.sha256(
        (root / "V381_V388_DESIGN.json").read_bytes()
    ).hexdigest()
    proof.pop("selection_proof_sha256", None)
    proof["selection_proof_sha256"] = canonical_hash(proof)
    write_json(proof_path, proof)

    summary = json.loads(summary_path.read_text())
    summary["candidate"] = CANDIDATE
    summary["selection"] = proof
    summary["coverage_gate"]["candidate"] = "V381_FIXED_UNIVERSE_DATA_COVERAGE"
    summary["limitations"] = [
        "Public daily archives are not executable bid/ask or queue observations.",
        "The fixed universe includes delisted assets; forced exits carry a 100 bps penalty.",
        "Program-level OOS is not pristine.",
        "Residual entropy is a statistical path-complexity ranking, not an issuer-quality claim.",
    ]

    if proof.get("selected") is not None:
        audits = pd.read_csv(results / "audit_metrics.csv")
        base_row = audits[audits.audit == "base"].iloc[0]
        severe_row = audits[audits.audit == "severe"].iloc[0]
        extreme_row = audits[audits.audit == "extreme"].iloc[0]
        delay_row = audits[audits.audit == "delay_1d"].iloc[0]
        annual = pd.read_csv(results / "ANNUAL_RETURNS.csv")
        policy_name = str(proof["selected"]["name"])
        worst_year = float(pd.to_numeric(annual[policy_name], errors="coerce").min())
        diagnostics = summary["diagnostics"]["base"]
        gates = {
            "validation_return_positive": float(base_row.validation_total_return) > 0.0,
            "holdout_return_positive": float(base_row.holdout_total_return) > 0.0,
            "final_return_positive": float(base_row.final_total_return) > 0.0,
            "full_cagr": float(base_row.full_cagr) >= POST_SELECTION_GATES["full_cagr_min"],
            "full_sharpe": float(base_row.full_sharpe)
            >= POST_SELECTION_GATES["full_sharpe_min"],
            "full_max_drawdown": float(base_row.full_max_drawdown)
            >= POST_SELECTION_GATES["full_max_drawdown_min"],
            "severe_full_cagr_positive": float(severe_row.full_cagr) > 0.0,
            "extreme_full_cagr_positive": float(extreme_row.full_cagr) > 0.0,
            "latency_full_cagr_positive": float(delay_row.full_cagr) > 0.0,
            "worst_calendar_year": worst_year
            >= POST_SELECTION_GATES["worst_calendar_year_min"],
            "full_long_leg_pnl_positive": float(diagnostics["long_leg_pnl"]) > 0.0,
            "full_short_leg_pnl_positive": float(diagnostics["short_leg_pnl"]) > 0.0,
            "concentration": float(diagnostics["top_positive_asset_pnl_share"])
            <= POST_SELECTION_GATES["top_positive_asset_pnl_share_max"],
            "forced_exit_count": int(diagnostics["forced_exit_count"])
            <= POST_SELECTION_GATES["forced_exit_count_max"],
        }
        passed = bool(all(gates.values()))
        summary["post_selection_gate_results"] = gates
        summary["extreme_audit"] = clean(extreme_row.to_dict())
        summary["status"] = (
            "paper_forward_candidate_non_pristine_oos" if passed else "rejected_after_oos"
        )
        summary["standalone_selection_passed"] = passed
        summary["integration_permitted"] = False
        summary["promoted_candidates"] = [policy_name] if passed else []
        summary["paper_forward_earliest_start"] = "2026-07-27" if passed else None
    else:
        summary["status"] = "rejected_before_validation"
        summary["standalone_selection_passed"] = False
        summary["integration_permitted"] = False
        summary["promoted_candidates"] = []

    summary["live_ready"] = False
    summary["real_leverage_authorized"] = False
    summary["profitability_proven"] = False
    write_json(summary_path, summary)
    decision = {
        key: summary.get(key)
        for key in (
            "candidate",
            "status",
            "eligible_policy_count",
            "selected_policy",
            "standalone_selection_passed",
            "integration_permitted",
            "promoted_candidates",
            "paper_forward_earliest_start",
            "live_ready",
            "real_leverage_authorized",
            "profitability_proven",
        )
        if key in summary
    }
    write_json(decision_path, decision)

    selected = proof.get("selected")
    if selected is None:
        report = (
            "# Active V381–V388 — residual entropy\n\n"
            "Status: `rejected_before_validation`.\n\n"
            f"Eligible promotable policies: `0/{proof['promotable_policy_count']}`. "
            "Validation 2024, holdout 2025 and final 2026 H1 were not opened.\n"
        )
    else:
        base_audit = summary["base_audit"]
        report = (
            "# Active V381–V388 — residual entropy\n\n"
            f"Status: `{summary['status']}`.\n\n"
            f"Selected policy: `{selected['name']}`.\n\n"
            f"Validation: {float(base_audit['validation_total_return']):+.2%}; "
            f"holdout: {float(base_audit['holdout_total_return']):+.2%}; "
            f"final: {float(base_audit['final_total_return']):+.2%}.\n"
        )
    (results / "REPORT_RU.md").write_text(report)
    v.write_manifest(root)


def run(root: Path, cache: Path) -> int:
    configure_engine()
    alias = root / "V269_V276_DESIGN.json"
    alias.write_bytes((root / "V381_V388_DESIGN.json").read_bytes())
    try:
        result = v.run(root, cache)
    finally:
        alias.unlink(missing_ok=True)
    stricter_postprocess(root)
    summary = json.loads((root / "results" / "summary.json").read_text())
    print(json.dumps(clean(summary), indent=2))
    return result


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
