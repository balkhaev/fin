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

ROOT = Path(__file__).resolve().parent
BASE_SOURCE = ROOT / "v261_base.py"
if not BASE_SOURCE.exists():
    raise RuntimeError(
        "v261_base.py must be materialized from commit "
        "ac2f43fe592513fde9d85e4b09921015b34c9419 before execution"
    )
_SPEC = importlib.util.spec_from_file_location("v261_attention_base", BASE_SOURCE)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(BASE_SOURCE)
q = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = q
_SPEC.loader.exec_module(q)

CANDIDATE = "ACTIVE_V349_CRYPTO_ATTENTION_FLOW"
FAMILIES = (
    "persistent_taker_buy_share",
    "low_abnormal_quote_attention",
    "low_abnormal_trade_intensity",
    "reversed_high_attention_control",
)
PROMOTABLE_FAMILIES = FAMILIES[:-1]
WINDOWS = {14: (14, 90), 30: (30, 180), 60: (60, 240)}
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

OriginalMarket = q.base.Market


class AttentionMarket(OriginalMarket):
    def __init__(self, klines: dict[str, pd.DataFrame], funding: dict[str, pd.Series]):
        super().__init__(klines, funding)
        self.trades = pd.DataFrame(
            {symbol: klines[symbol].trades.reindex(self.index) for symbol in self.symbols}
        )
        self.taker_buy_quote = pd.DataFrame(
            {
                symbol: klines[symbol].taker_buy_quote.reindex(self.index)
                for symbol in self.symbols
            }
        )


def clean(value: Any) -> Any:
    return q.clean(value)


def write_json(path: Path, value: Any) -> None:
    q.write_json(path, value)


def canonical_hash(value: Any) -> str:
    return q.canonical_hash(value)


def safe_ratio(short_value: pd.DataFrame, long_value: pd.DataFrame) -> pd.DataFrame:
    return short_value / long_value.replace(0.0, np.nan)


def score_frame(
    market: AttentionMarket,
    quote_volume: pd.DataFrame,
    family: str,
    lookback_key: int,
) -> pd.DataFrame:
    short_window, long_window = WINDOWS[int(lookback_key)]
    quote = quote_volume.reindex(market.index).where(lambda value: value > 0.0)
    trades = market.trades.where(lambda value: value > 0.0)
    taker = market.taker_buy_quote.where(lambda value: value >= 0.0)

    short_quote = quote.rolling(short_window, min_periods=short_window).median()
    long_quote = quote.rolling(long_window, min_periods=long_window).median()
    attention = np.log(safe_ratio(short_quote, long_quote)).replace(
        [np.inf, -np.inf], np.nan
    )

    if family == "low_abnormal_quote_attention":
        raw = -attention
    elif family == "reversed_high_attention_control":
        raw = attention
    elif family == "persistent_taker_buy_share":
        imbalance = (2.0 * taker / quote - 1.0).clip(-1.0, 1.0)
        short_mean = imbalance.rolling(short_window, min_periods=short_window).mean()
        long_scale = imbalance.rolling(long_window, min_periods=long_window).std(ddof=1)
        raw = short_mean / long_scale.replace(0.0, np.nan)
    elif family == "low_abnormal_trade_intensity":
        intensity = trades / quote
        short_intensity = intensity.rolling(
            short_window, min_periods=short_window
        ).median()
        long_intensity = intensity.rolling(long_window, min_periods=long_window).median()
        raw = -np.log(safe_ratio(short_intensity, long_intensity)).replace(
            [np.inf, -np.inf], np.nan
        )
    else:
        raise ValueError(family)
    return q.winsorize_cross_section(raw)


def configure_engine() -> None:
    q.CANDIDATE = CANDIDATE
    q.FAMILIES = FAMILIES
    q.PROMOTABLE_FAMILIES = PROMOTABLE_FAMILIES
    q.POLICIES = tuple(
        q.Policy(*values)
        for values in product(
            FAMILIES,
            tuple(WINDOWS),
            (3, 4),
            (7, 14, 28),
            ("dollar", "beta"),
        )
    )
    q.DEVELOPMENT_GATES = DEVELOPMENT_GATES
    q.POST_SELECTION_GATES = POST_SELECTION_GATES
    q.score_frame = score_frame
    q.base.Market = AttentionMarket
    q.base.TARGET_GROSS = TARGET_GROSS
    q.base.MAX_REALIZED_GROSS = MAX_REALIZED_GROSS


def synthetic_inputs() -> tuple[dict[str, pd.DataFrame], dict[str, pd.Series]]:
    index = pd.date_range("2018-01-01", periods=2100, freq="1D", tz="UTC")
    rng = np.random.default_rng(349)
    common = rng.normal(0.0001, 0.012, len(index))
    klines: dict[str, pd.DataFrame] = {}
    funding: dict[str, pd.Series] = {}
    for number, symbol in enumerate(q.SYMBOLS[:10]):
        attention_regime = np.exp(
            0.5 * np.sin(np.arange(len(index)) / (35.0 + number))
            + rng.normal(0.0, 0.15, len(index))
        )
        residual = rng.normal(0.0, 0.007 + 0.0003 * number, len(index))
        returns = 0.65 * common + residual
        close = 100.0 * np.exp(np.cumsum(returns))
        open_price = np.r_[
            close[0],
            close[:-1] * np.exp(rng.normal(0.0, 0.0015, len(index) - 1)),
        ]
        quote = np.exp(16.0 - number * 0.20) * attention_regime
        trades = quote / (1500.0 + 100.0 * number) * np.exp(
            rng.normal(0.0, 0.08, len(index))
        )
        imbalance = np.tanh(
            0.4 * np.sin(np.arange(len(index)) / (25.0 + number))
            + rng.normal(0.0, 0.20, len(index))
        )
        taker_buy_quote = quote * (imbalance + 1.0) / 2.0
        klines[symbol] = pd.DataFrame(
            {
                "open": open_price,
                "high": np.maximum(open_price, close) * 1.01,
                "low": np.minimum(open_price, close) * 0.99,
                "close": close,
                "volume": quote / np.maximum(close, 1e-9),
                "quote_volume": quote,
                "trades": trades,
                "taker_buy_base": taker_buy_quote / np.maximum(close, 1e-9),
                "taker_buy_quote": taker_buy_quote,
            },
            index=index,
        )
        funding[symbol] = pd.Series(0.0, index=index)
    return klines, funding


def self_test() -> None:
    configure_engine()
    assert len(q.POLICIES) == 144
    assert sum(policy.family in PROMOTABLE_FAMILIES for policy in q.POLICIES) == 108
    klines, funding = synthetic_inputs()
    market = AttentionMarket(klines, funding)
    quote = pd.DataFrame(
        {symbol: klines[symbol].quote_volume.reindex(market.index) for symbol in market.symbols}
    )
    policy = q.Policy("persistent_taker_buy_share", 30, 3, 14, "dollar")
    weights = q.build_weights(market, quote, policy, {}, {})
    account, diagnostics = q.base.simulate(
        market, weights, "2021-01-01", "2023-01-01", q.base.AUDITS[0]
    )
    assert not account.empty
    assert np.isfinite(account.equity).all()
    assert diagnostics["symbol_count_traded"] >= 6
    assert diagnostics["rebalance_events"] >= 20

    changed = {symbol: frame.copy() for symbol, frame in klines.items()}
    first = market.symbols[0]
    changed[first].iloc[
        -1, changed[first].columns.get_loc("taker_buy_quote")
    ] *= 0.01
    changed_market = AttentionMarket(changed, funding)
    changed_quote = pd.DataFrame(
        {
            symbol: changed[symbol].quote_volume.reindex(changed_market.index)
            for symbol in changed_market.symbols
        }
    )
    changed_weights = q.build_weights(changed_market, changed_quote, policy, {}, {})
    pd.testing.assert_frame_equal(weights.iloc[:-1], changed_weights.iloc[:-1])
    print("V349-V356 causal attention-flow self-test passed")


def postprocess(root: Path) -> None:
    results = root / "results"
    proof_path = results / "selection_proof_before_validation.json"
    summary_path = results / "summary.json"
    decision_path = results / "FROZEN_DECISION.json"
    proof = json.loads(proof_path.read_text())
    proof["candidate"] = CANDIDATE
    proof["development_gates"] = DEVELOPMENT_GATES
    proof["post_selection_gates"] = POST_SELECTION_GATES
    if proof.get("coverage_gate"):
        proof["coverage_gate"]["candidate"] = "V349_FIXED_UNIVERSE_DATA_COVERAGE"
    proof["window_pairs"] = {str(key): list(value) for key, value in WINDOWS.items()}
    proof["design_sha256"] = hashlib.sha256(
        (root / "V349_V356_DESIGN.json").read_bytes()
    ).hexdigest()
    proof.pop("selection_proof_sha256", None)
    proof["selection_proof_sha256"] = canonical_hash(proof)
    write_json(proof_path, proof)

    summary = json.loads(summary_path.read_text())
    summary["candidate"] = CANDIDATE
    summary["selection"] = proof
    if summary.get("coverage_gate"):
        summary["coverage_gate"]["candidate"] = "V349_FIXED_UNIVERSE_DATA_COVERAGE"
    summary["limitations"] = [
        "Daily taker-buy and trade-count fields are bar aggregates, not order-level executions.",
        "Public daily archives are not executable bid/ask or queue observations.",
        "The fixed universe includes delisted assets; forced exits carry a 100 bps penalty.",
        "Program-level OOS is not pristine.",
    ]

    selected = proof.get("selected")
    if selected is not None:
        audits = pd.read_csv(results / "audit_metrics.csv")
        base_row = audits[audits.audit == "base"].iloc[0]
        severe_row = audits[audits.audit == "severe"].iloc[0]
        extreme_row = audits[audits.audit == "extreme"].iloc[0]
        delay_row = audits[audits.audit == "delay_1d"].iloc[0]
        annual = pd.read_csv(results / "ANNUAL_RETURNS.csv")
        return_column = next(column for column in annual.columns if column != "year")
        worst_year = float(pd.to_numeric(annual[return_column], errors="coerce").min())
        diagnostics = summary["base_diagnostics"]
        gates = {
            "validation_return_positive": float(base_row.validation_return) > 0.0,
            "holdout_return_positive": float(base_row.holdout_return) > 0.0,
            "final_return_positive": float(base_row.final_return) > 0.0,
            "full_cagr": float(base_row.cagr) >= POST_SELECTION_GATES["full_cagr_min"],
            "full_sharpe": float(base_row.sharpe)
            >= POST_SELECTION_GATES["full_sharpe_min"],
            "full_max_drawdown": float(base_row.max_drawdown)
            >= POST_SELECTION_GATES["full_max_drawdown_min"],
            "severe_full_cagr_positive": float(severe_row.cagr) > 0.0,
            "extreme_full_cagr_positive": float(extreme_row.cagr) > 0.0,
            "latency_full_cagr_positive": float(delay_row.cagr) > 0.0,
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
        summary["status"] = (
            "paper_forward_candidate_non_pristine_oos" if passed else "rejected_after_oos"
        )
        summary["standalone_selection_passed"] = passed
        summary["promoted_candidates"] = [selected["name"]] if passed else []
        summary["paper_forward_earliest_start"] = "2026-07-27" if passed else None
    else:
        summary["status"] = "rejected_before_validation"
        summary["standalone_selection_passed"] = False
        summary["promoted_candidates"] = []

    summary["integration_permitted"] = False
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

    if selected is None:
        report = (
            "# Active V349–V356 — attention and taker flow\n\n"
            "Status: `rejected_before_validation`.\n\n"
            f"Eligible promotable policies: `0/{proof.get('promotable_policy_count', 108)}`. "
            "Validation 2024, holdout 2025 and final 2026 H1 were not opened.\n"
        )
    else:
        report = (
            "# Active V349–V356 — attention and taker flow\n\n"
            f"Status: `{summary['status']}`.\n\n"
            f"Selected policy: `{selected['name']}`.\n"
        )
    (results / "REPORT_RU.md").write_text(report)
    q.write_manifest(root)


def run(root: Path, cache: Path) -> int:
    configure_engine()
    alias = root / "V261_V268_DESIGN.json"
    alias.write_bytes((root / "V349_V356_DESIGN.json").read_bytes())
    try:
        result = q.run(root, cache)
    finally:
        alias.unlink(missing_ok=True)
    postprocess(root)
    print(json.dumps(clean(json.loads((root / "results/summary.json").read_text())), indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--cache", type=Path, default=Path(".cache/v9"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return run(args.root, args.cache)


if __name__ == "__main__":
    raise SystemExit(main())
