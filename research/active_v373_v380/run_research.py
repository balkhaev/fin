#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / "research" / "active_v341_v348" / "run_research.py"
_SPEC = importlib.util.spec_from_file_location("v341_jump_harness", SOURCE)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(SOURCE)
h = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = h
_SPEC.loader.exec_module(h)

CANDIDATE = "ACTIVE_V373_CRYPTO_IDIOSYNCRATIC_JUMPS"
FAMILIES = (
    "low_downside_jump_incidence",
    "low_absolute_jump_incidence",
    "low_downside_jump_clustering",
    "reversed_high_downside_jump_control",
)
PROMOTABLE_FAMILIES = FAMILIES[:-1]
LOOKBACKS = (60, 120, 240)
BETA_LOOKBACK = 90
SCALE_LOOKBACK = 60
JUMP_THRESHOLD = 1.5
TARGET_GROSS = 0.40
MAX_REALIZED_GROSS = 0.70
DEVELOPMENT_GATES = {
    "cagr_min": 0.05,
    "sharpe_min": 1.00,
    "max_drawdown_min": -0.15,
    "rebalance_events_min": 18,
    "annual_turnover_max": 15.0,
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


def score_frame(market: Any, family: str, lookback: int) -> pd.DataFrame:
    beta = market.beta(BETA_LOOKBACK).shift(1)
    residual = market.logret - beta.mul(market.market, axis=0)
    scale = residual.rolling(
        SCALE_LOOKBACK, min_periods=SCALE_LOOKBACK
    ).std(ddof=1).shift(1)
    standardized = residual / scale.replace(0.0, np.nan)
    downside_excess = (-standardized - JUMP_THRESHOLD).clip(lower=0.0)
    absolute_excess = (standardized.abs() - JUMP_THRESHOLD).clip(lower=0.0)

    if family == "low_downside_jump_incidence":
        raw = -downside_excess.rolling(
            int(lookback), min_periods=int(lookback)
        ).mean()
    elif family == "reversed_high_downside_jump_control":
        raw = downside_excess.rolling(
            int(lookback), min_periods=int(lookback)
        ).mean()
    elif family == "low_absolute_jump_incidence":
        raw = -absolute_excess.rolling(
            int(lookback), min_periods=int(lookback)
        ).mean()
    elif family == "low_downside_jump_clustering":
        consecutive = downside_excess * downside_excess.shift(1)
        raw = -consecutive.rolling(
            int(lookback), min_periods=int(lookback)
        ).mean()
    else:
        raise ValueError(family)
    return h.v.winsorize_cross_section(raw)


def configure() -> None:
    h.CANDIDATE = CANDIDATE
    h.FAMILIES = FAMILIES
    h.PROMOTABLE_FAMILIES = PROMOTABLE_FAMILIES
    h.WINDOWS = {value: (value, value) for value in LOOKBACKS}
    h.TARGET_GROSS = TARGET_GROSS
    h.MAX_REALIZED_GROSS = MAX_REALIZED_GROSS
    h.DEVELOPMENT_GATES = DEVELOPMENT_GATES
    h.POST_SELECTION_GATES = POST_SELECTION_GATES
    h.score_frame = score_frame

    h.v.CANDIDATE = CANDIDATE
    h.v.FAMILIES = FAMILIES
    h.v.PROMOTABLE_FAMILIES = PROMOTABLE_FAMILIES
    h.v.TARGET_GROSS = TARGET_GROSS
    h.v.MAX_REALIZED_GROSS = MAX_REALIZED_GROSS
    h.v.base.TARGET_GROSS = TARGET_GROSS
    h.v.base.MAX_REALIZED_GROSS = MAX_REALIZED_GROSS
    h.v.DEVELOPMENT_GATES = DEVELOPMENT_GATES
    h.v.POST_SELECTION_GATES = POST_SELECTION_GATES
    h.v.POLICIES = tuple(
        h.v.Policy(*values)
        for values in product(
            FAMILIES,
            LOOKBACKS,
            (3, 4),
            (14, 28, 56),
            ("dollar", "beta"),
        )
    )
    h.v.score_frame = score_frame


def self_test() -> None:
    configure()
    assert len(h.v.POLICIES) == 144
    assert sum(policy.family in PROMOTABLE_FAMILIES for policy in h.v.POLICIES) == 108
    market = h.synthetic_market()
    policy = h.v.Policy("low_downside_jump_incidence", 60, 3, 14, "dollar")
    weights = h.v.build_weights(market, policy, {}, {})
    account, diagnostics = h.v.base.simulate(
        market, weights, "2021-01-01", "2023-01-01", h.v.AUDITS[0]
    )
    assert not account.empty
    assert np.isfinite(account.equity).all()
    assert diagnostics["symbol_count_traded"] >= 6
    assert diagnostics["rebalance_events"] >= 15

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
    changed_market = h.v.base.Market(
        changed_frames,
        {symbol: pd.Series(0.0, index=market.index) for symbol in market.symbols},
    )
    changed_weights = h.v.build_weights(changed_market, policy, {}, {})
    pd.testing.assert_frame_equal(weights.iloc[:-1], changed_weights.iloc[:-1])
    print("V373-V380 causal residual-jump self-test passed")


def adjust_outputs(root: Path) -> None:
    results = root / "results"
    proof_path = results / "selection_proof_before_validation.json"
    summary_path = results / "summary.json"
    decision_path = results / "FROZEN_DECISION.json"
    proof = json.loads(proof_path.read_text())
    proof["candidate"] = CANDIDATE
    proof["coverage_gate"]["candidate"] = "V373_FIXED_UNIVERSE_DATA_COVERAGE"
    proof.pop("window_pairs", None)
    proof["lookbacks"] = list(LOOKBACKS)
    proof["standardization"] = {
        "market_beta_lookback_days": BETA_LOOKBACK,
        "residual_scale_lookback_days": SCALE_LOOKBACK,
        "scale_lag_days": 1,
        "jump_threshold_sigma": JUMP_THRESHOLD,
    }
    proof["design_sha256"] = hashlib.sha256(
        (root / "V373_V380_DESIGN.json").read_bytes()
    ).hexdigest()
    proof.pop("selection_proof_sha256", None)
    proof["selection_proof_sha256"] = h.v.canonical_hash(proof)
    h.v.write_json(proof_path, proof)

    summary = json.loads(summary_path.read_text())
    summary["candidate"] = CANDIDATE
    summary["selection"] = proof
    summary["coverage_gate"]["candidate"] = "V373_FIXED_UNIVERSE_DATA_COVERAGE"
    summary["limitations"] = [
        "Jump exceedances depend on a rolling residual-scale estimate and are not structural issuer events.",
        "Public daily archives are not executable bid/ask or queue observations.",
        "The fixed universe includes delisted assets; forced exits carry a 100 bps penalty.",
        "Program-level OOS is not pristine.",
    ]
    summary["integration_permitted"] = False
    summary["live_ready"] = False
    summary["real_leverage_authorized"] = False
    summary["profitability_proven"] = False
    h.v.write_json(summary_path, summary)

    decision = json.loads(decision_path.read_text())
    decision["candidate"] = CANDIDATE
    decision["integration_permitted"] = False
    decision["live_ready"] = False
    decision["real_leverage_authorized"] = False
    decision["profitability_proven"] = False
    h.v.write_json(decision_path, decision)

    selected = proof.get("selected")
    if selected is None:
        report = (
            "# Active V373–V380 — idiosyncratic jump clustering\n\n"
            "Status: `rejected_before_validation`.\n\n"
            f"Eligible promotable policies: `0/{proof['promotable_policy_count']}`. "
            "Validation 2024, holdout 2025 and final 2026 H1 were not opened.\n"
        )
    else:
        base_audit = summary["base_audit"]
        report = (
            "# Active V373–V380 — idiosyncratic jump clustering\n\n"
            f"Status: `{summary['status']}`.\n\n"
            f"Selected policy: `{selected['name']}`.\n\n"
            f"Validation: {float(base_audit['validation_total_return']):+.2%}; "
            f"holdout: {float(base_audit['holdout_total_return']):+.2%}; "
            f"final: {float(base_audit['final_total_return']):+.2%}.\n"
        )
    (results / "REPORT_RU.md").write_text(report)
    h.v.write_manifest(root)


def run(root: Path, cache: Path) -> int:
    configure()
    alias = root / "V341_V348_DESIGN.json"
    alias.write_bytes((root / "V373_V380_DESIGN.json").read_bytes())
    try:
        result = h.run(root, cache)
    finally:
        alias.unlink(missing_ok=True)
    adjust_outputs(root)
    print(json.dumps(h.v.clean(json.loads((root / "results/summary.json").read_text())), indent=2))
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
