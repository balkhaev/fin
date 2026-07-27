#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from dataclasses import asdict, dataclass
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_PATH = REPO_ROOT / "research" / "active_v253_v260" / "run_research.py"
SPEC = importlib.util.spec_from_file_location("v253_materialized_base", BASE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(BASE_PATH)
base = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = base
SPEC.loader.exec_module(base)

CANDIDATE = "ACTIVE_V261_CRYPTO_LIQUIDITY_QUALITY"
SYMBOLS = base.SYMBOLS
FAMILIES = (
    "high_quote_liquidity",
    "low_amihud_impact",
    "stable_quote_liquidity",
    "reversed_illiquidity_control",
)
PROMOTABLE_FAMILIES = FAMILIES[:-1]


@dataclass(frozen=True, slots=True)
class Policy:
    family: str
    lookback_days: int
    long_short_k: int
    rebalance_days: int
    neutralization: str

    @property
    def name(self) -> str:
        return (
            f"{self.family}_l{self.lookback_days}_k{self.long_short_k}_"
            f"r{self.rebalance_days}_{self.neutralization}"
        )


POLICIES = tuple(
    Policy(*values)
    for values in product(
        FAMILIES,
        (30, 60, 120),
        (2, 3),
        (7, 14, 28),
        ("dollar", "beta"),
    )
)
DEVELOPMENT_GATES = {
    "cagr_min": 0.04,
    "sharpe_min": 0.80,
    "max_drawdown_min": -0.15,
    "rebalance_events_min": 24,
    "annual_turnover_max": 20.0,
    "max_realized_gross": 0.70,
    "all_development_years_positive": True,
    "net_long_leg_pnl_positive": True,
    "net_short_leg_pnl_positive": True,
    "symbols_traded_min": 8,
    "top_positive_asset_pnl_share_max": 0.40,
}
POST_SELECTION_GATES = dict(base.POST_SELECTION_GATES)


def clean(value: Any) -> Any:
    return base.clean(value)


def write_json(path: Path, value: Any) -> None:
    base.write_json(path, value)


def sha256_file(path: Path) -> str:
    return base.sha256_file(path)


def canonical_hash(value: Any) -> str:
    return base.canonical_hash(value)


def winsorize_cross_section(frame: pd.DataFrame) -> pd.DataFrame:
    low = frame.quantile(0.05, axis=1)
    high = frame.quantile(0.95, axis=1)
    return frame.clip(lower=low, upper=high, axis=0)


def score_frame(
    market: Any,
    quote_volume: pd.DataFrame,
    family: str,
    lookback: int,
) -> pd.DataFrame:
    quote = quote_volume.reindex(market.index).where(lambda value: value > 0.0)
    log_quote = np.log(quote)
    if family == "high_quote_liquidity":
        raw = log_quote.rolling(lookback, min_periods=lookback).median()
    elif family in {"low_amihud_impact", "reversed_illiquidity_control"}:
        impact = market.logret.abs() / quote
        amihud = impact.rolling(lookback, min_periods=lookback).median()
        raw = -amihud if family == "low_amihud_impact" else amihud
    elif family == "stable_quote_liquidity":
        raw = -log_quote.diff().rolling(lookback, min_periods=lookback).std(ddof=1)
    else:
        raise ValueError(family)
    return winsorize_cross_section(raw)


def build_weights(
    market: Any,
    quote_volume: pd.DataFrame,
    policy: Policy,
    score_cache: dict[tuple[str, int], pd.DataFrame],
    raw_cache: dict[tuple[str, int, int, str], pd.DataFrame],
) -> pd.DataFrame:
    score_key = (policy.family, policy.lookback_days)
    if score_key not in score_cache:
        score_cache[score_key] = score_frame(
            market, quote_volume, policy.family, policy.lookback_days
        )
    raw_key = (
        policy.family,
        policy.lookback_days,
        policy.long_short_k,
        policy.neutralization,
    )
    if raw_key not in raw_cache:
        raw_cache[raw_key] = base.raw_weights(
            market,
            score_cache[score_key],
            policy.long_short_k,
            policy.neutralization,
        )
    return base.schedule_weights(
        raw_cache[raw_key], market.available, policy.rebalance_days
    )


def policy_dict(policy: Policy) -> dict[str, Any]:
    return asdict(policy) | {"name": policy.name}


def self_test() -> None:
    assert len(POLICIES) == 144
    index = pd.date_range("2020-01-01", periods=1100, freq="1D", tz="UTC")
    rng = np.random.default_rng(261)
    klines: dict[str, pd.DataFrame] = {}
    funding: dict[str, pd.Series] = {}
    for number, symbol in enumerate(SYMBOLS[:8]):
        returns = rng.normal(0.0, 0.01 + 0.001 * number, len(index))
        close = 100.0 * np.exp(np.cumsum(returns))
        open_price = np.r_[close[0], close[:-1] * np.exp(rng.normal(0.0, 0.001, len(index) - 1))]
        quote_volume = np.exp(16.0 - number * 0.5 + rng.normal(0.0, 0.15, len(index)))
        klines[symbol] = pd.DataFrame(
            {
                "open": open_price,
                "high": np.maximum(open_price, close) * 1.01,
                "low": np.minimum(open_price, close) * 0.99,
                "close": close,
                "volume": quote_volume / np.maximum(close, 1e-9),
                "quote_volume": quote_volume,
                "trades": 1.0,
                "taker_buy_base": 0.5,
                "taker_buy_quote": quote_volume * 0.5,
            },
            index=index,
        )
        funding[symbol] = pd.Series(0.0, index=index)
    market = base.Market(klines, funding)
    quote = pd.DataFrame(
        {symbol: klines[symbol].quote_volume.reindex(market.index) for symbol in market.symbols}
    )
    policy = Policy("low_amihud_impact", 60, 2, 7, "dollar")
    weights = build_weights(market, quote, policy, {}, {})
    account, diagnostics = base.simulate(
        market,
        weights,
        "2021-01-01",
        "2023-01-01",
        base.AUDITS[0],
    )
    assert not account.empty
    assert np.isfinite(account.equity).all()
    assert diagnostics["symbol_count_traded"] >= 4
    assert diagnostics["rebalance_events"] < len(account) // 3

    changed = {symbol: frame.copy() for symbol, frame in klines.items()}
    changed[SYMBOLS[0]].iloc[-1, changed[SYMBOLS[0]].columns.get_loc("quote_volume")] *= 1000.0
    changed_quote = pd.DataFrame(
        {symbol: changed[symbol].quote_volume.reindex(market.index) for symbol in market.symbols}
    )
    changed_weights = build_weights(market, changed_quote, policy, {}, {})
    pd.testing.assert_frame_equal(weights.iloc[:-1], changed_weights.iloc[:-1])
    print("V261-V268 self-test passed")


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


def failure_outputs(
    root: Path,
    gate: dict[str, Any],
    records: list[dict[str, Any]],
) -> None:
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
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
        "data_manifest_sha256": canonical_hash(records),
    }
    write_json(results / "coverage_gate.json", gate)
    write_json(results / "FROZEN_DECISION.json", decision)
    write_json(results / "summary.json", summary)
    write_json(
        results / "selection_proof_before_validation.json",
        {"candidate": CANDIDATE, "selection_not_run": True, "reason": "data gate failed"},
    )
    pd.DataFrame().to_csv(results / "selection_ranking_before_validation.csv", index=False)
    pd.DataFrame().to_csv(results / "audit_metrics.csv", index=False)
    (results / "REPORT_RU.md").write_text(
        "# Active V261–V268 — crypto liquidity quality\n\n"
        "Status: `data_access_insufficient`. P&L и selection не запускались.\n"
    )
    write_manifest(root)


def run(root: Path, cache: Path) -> int:
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
    config = base.V9Config(
        symbols=SYMBOLS,
        start="2020-01-01",
        end_exclusive=base.END_EXCLUSIVE,
        interval="1d",
        starting_equity=base.INITIAL_EQUITY,
        max_gross=base.TARGET_GROSS,
        forced_exit_penalty_bps=base.FORCED_EXIT_PENALTY_BPS,
    )
    klines, funding, records, quality = base.load_v9(config, cache, False)
    pd.DataFrame(records).to_csv(results / "data_manifest.csv", index=False)
    pd.DataFrame(quality).to_csv(results / "data_quality.csv", index=False)
    gate = base.data_gate(klines, records)
    gate = {**gate, "candidate": "V261_FIXED_UNIVERSE_DATA_COVERAGE"}
    write_json(results / "coverage_gate.json", gate)
    if not gate["passed"]:
        failure_outputs(root, gate, records)
        print(json.dumps(clean({"status": "data_access_insufficient", "coverage": gate}), indent=2))
        return 0

    market = base.Market(klines, funding)
    quote_volume = pd.DataFrame(
        {
            symbol: klines[symbol].quote_volume.reindex(market.index)
            for symbol in market.symbols
        }
    )
    score_cache: dict[tuple[str, int], pd.DataFrame] = {}
    raw_cache: dict[tuple[str, int, int, str], pd.DataFrame] = {}
    weights_cache: dict[str, pd.DataFrame] = {}
    ranking_rows: list[dict[str, Any]] = []
    for number, policy in enumerate(POLICIES, start=1):
        weights = build_weights(
            market, quote_volume, policy, score_cache, raw_cache
        )
        weights_cache[policy.name] = weights
        account, diagnostics = base.simulate(
            market,
            weights,
            base.START,
            base.DEVELOPMENT_END_EXCLUSIVE,
            base.AUDITS[0],
        )
        values = base.account_metrics(account)
        years = base.yearly_returns(account, "return")
        all_years_positive = bool(
            not years.empty
            and (pd.to_numeric(years["return"], errors="coerce") > 0.0).all()
        )
        promotable = policy.family in PROMOTABLE_FAMILIES
        eligible = bool(
            promotable
            and values["cagr"] >= DEVELOPMENT_GATES["cagr_min"]
            and values["sharpe"] >= DEVELOPMENT_GATES["sharpe_min"]
            and values["max_drawdown"] >= DEVELOPMENT_GATES["max_drawdown_min"]
            and diagnostics["rebalance_events"]
            >= DEVELOPMENT_GATES["rebalance_events_min"]
            and values["annual_turnover"]
            <= DEVELOPMENT_GATES["annual_turnover_max"]
            and values["max_gross"] <= DEVELOPMENT_GATES["max_realized_gross"]
            and all_years_positive
            and diagnostics["long_leg_pnl"] > 0.0
            and diagnostics["short_leg_pnl"] > 0.0
            and diagnostics["symbol_count_traded"]
            >= DEVELOPMENT_GATES["symbols_traded_min"]
            and diagnostics["top_positive_asset_pnl_share"]
            <= DEVELOPMENT_GATES["top_positive_asset_pnl_share_max"]
        )
        score = (
            float(values["cagr"])
            + 0.06 * float(values["sharpe"])
            + 0.12 * float(values["max_drawdown"])
            - 0.0005 * float(values["annual_turnover"])
        )
        ranking_rows.append(
            {
                "policy": policy.name,
                **asdict(policy),
                "promotable_family": promotable,
                "eligible_development": eligible,
                "all_development_years_positive": all_years_positive,
                "long_leg_pnl": diagnostics["long_leg_pnl"],
                "short_leg_pnl": diagnostics["short_leg_pnl"],
                "symbol_count_traded": diagnostics["symbol_count_traded"],
                "symbols_traded": "+".join(diagnostics["symbols_traded"]),
                "top_positive_asset_pnl_share": diagnostics[
                    "top_positive_asset_pnl_share"
                ],
                "asset_pnl_json": json.dumps(
                    clean(diagnostics["asset_pnl"]), sort_keys=True
                ),
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
    selected_policy = next(
        (policy for policy in POLICIES if policy.name == selected_name), None
    )
    proof = {
        "candidate": CANDIDATE,
        "selection_cutoff": "2023-12-31T23:59:59Z",
        "selection_uses_2024": False,
        "selection_uses_2025": False,
        "selection_uses_2026": False,
        "program_level_holdout_pristine": False,
        "policy_count": len(POLICIES),
        "promotable_policy_count": sum(
            policy.family in PROMOTABLE_FAMILIES for policy in POLICIES
        ),
        "eligible_policy_count": int(len(eligible)),
        "development_gates": DEVELOPMENT_GATES,
        "post_selection_gates": POST_SELECTION_GATES,
        "coverage_gate": gate,
        "ranking_sha256": hashlib.sha256(
            ranking.to_csv(index=False).encode("utf-8")
        ).hexdigest(),
        "design_sha256": sha256_file(root / "V261_V268_DESIGN.json"),
        "selected": policy_dict(selected_policy)
        if selected_policy is not None
        else None,
    }
    proof["selection_proof_sha256"] = canonical_hash(proof)
    write_json(results / "selection_proof_before_validation.json", proof)

    if selected_policy is None:
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
            "coverage_gate": gate,
            "development_diagnostics": ranking.head(12).to_dict(orient="records"),
            "limitations": [
                "Daily quote volume is an exchange-reported liquidity proxy, not executable order-book depth.",
                "Public daily archives are not executable bid/ask or queue observations.",
                "Fixed universe includes delisted names; forced exits carry a 100 bps penalty.",
                "Program-level holdout is not pristine.",
            ],
            "data_manifest_sha256": canonical_hash(records),
        }
        write_json(results / "FROZEN_DECISION.json", decision)
        write_json(results / "summary.json", summary)
        pd.DataFrame().to_csv(results / "audit_metrics.csv", index=False)
        (results / "REPORT_RU.md").write_text(
            "# Active V261–V268 — crypto liquidity quality\n\n"
            "Status: `rejected_before_validation`.\n\n"
            f"Eligible promotable policies: `0/{proof['promotable_policy_count']}`. "
            "2024–2026 не открывались.\n"
        )
        write_manifest(root)
        print(json.dumps(clean(summary), indent=2))
        return 0

    selected_weights = weights_cache[selected_policy.name]
    selected_weights.to_csv(results / "frozen_weights.csv")
    audit_rows: list[dict[str, Any]] = []
    audit_accounts: dict[str, pd.DataFrame] = {}
    audit_diagnostics: dict[str, dict[str, Any]] = {}
    for audit in base.AUDITS:
        account, diagnostics = base.simulate(
            market, selected_weights, base.START, base.END_EXCLUSIVE, audit
        )
        audit_accounts[audit.name] = account
        audit_diagnostics[audit.name] = diagnostics
        full = base.account_metrics(account)
        development = base.account_metrics(
            base.slice_account(
                account, base.START, base.DEVELOPMENT_END_EXCLUSIVE
            )
        )
        validation = base.account_metrics(
            base.slice_account(
                account, base.VALIDATION_START, base.VALIDATION_END_EXCLUSIVE
            )
        )
        holdout = base.account_metrics(
            base.slice_account(
                account, base.HOLDOUT_START, base.HOLDOUT_END_EXCLUSIVE
            )
        )
        final = base.account_metrics(
            base.slice_account(account, base.FINAL_START, base.END_EXCLUSIVE)
        )
        audit_rows.append(
            {
                "audit": audit.name,
                **asdict(audit),
                **full,
                "development_cagr": development["cagr"],
                "validation_return": validation["total_return"],
                "holdout_return": holdout["total_return"],
                "final_return": final["total_return"],
                "long_leg_pnl": diagnostics["long_leg_pnl"],
                "short_leg_pnl": diagnostics["short_leg_pnl"],
                "symbol_count_traded": diagnostics["symbol_count_traded"],
            }
        )
    audit_frame = pd.DataFrame(audit_rows)
    audit_frame.to_csv(results / "audit_metrics.csv", index=False)

    base_account = audit_accounts["base"]
    full = base.account_metrics(base_account)
    development = base.account_metrics(
        base.slice_account(base_account, base.START, base.DEVELOPMENT_END_EXCLUSIVE)
    )
    validation = base.account_metrics(
        base.slice_account(
            base_account, base.VALIDATION_START, base.VALIDATION_END_EXCLUSIVE
        )
    )
    holdout = base.account_metrics(
        base.slice_account(
            base_account, base.HOLDOUT_START, base.HOLDOUT_END_EXCLUSIVE
        )
    )
    final = base.account_metrics(
        base.slice_account(base_account, base.FINAL_START, base.END_EXCLUSIVE)
    )
    yearly = base.yearly_returns(base_account, "V261_liquidity_quality")
    yearly.to_csv(results / "ANNUAL_RETURNS.csv", index=False)
    severe = base.account_metrics(audit_accounts["severe"])
    delayed = base.account_metrics(audit_accounts["delay_1d"])
    worst_year = (
        float(pd.to_numeric(yearly.V261_liquidity_quality).min())
        if not yearly.empty
        else -1.0
    )
    checks = {
        "eligible_development": True,
        "validation_return_positive": validation["total_return"] > 0.0,
        "holdout_return_positive": holdout["total_return"] > 0.0,
        "final_return_positive": final["total_return"] > 0.0,
        "severe_full_cagr_positive": severe["cagr"] > 0.0,
        "latency_full_cagr_positive": delayed["cagr"] > 0.0,
        "worst_calendar_year": worst_year
        >= POST_SELECTION_GATES["worst_calendar_year_min"],
        "forced_exit_count": audit_diagnostics["base"]["forced_exit_count"]
        <= POST_SELECTION_GATES["forced_exit_count_max"],
        "data_coverage": gate["passed"],
    }
    standalone_passed = all(checks.values())
    status = (
        "frozen_historical_candidate_needs_forward"
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
        "coverage_gate": gate,
        "checks": checks,
        "candidate_full": full,
        "candidate_development": development,
        "candidate_validation_2024": validation,
        "candidate_holdout_2025": holdout,
        "candidate_final_2026h1": final,
        "worst_year": worst_year,
        "audit_metrics": audit_frame.to_dict(orient="records"),
        "base_diagnostics": audit_diagnostics["base"],
        "limitations": [
            "Daily quote volume is an exchange-reported liquidity proxy, not executable order-book depth.",
            "Public daily archives are not executable bid/ask or queue observations.",
            "Fixed universe includes delisted names; forced exits carry a 100 bps penalty.",
            "Program-level holdout is not pristine.",
        ],
        "data_manifest_sha256": canonical_hash(records),
    }
    write_json(results / "FROZEN_DECISION.json", decision)
    write_json(results / "summary.json", summary)
    (results / "REPORT_RU.md").write_text(
        "# Active V261–V268 — crypto liquidity quality\n\n"
        f"Status: `{status}`.\n\n"
        f"Selected: `{selected_policy.name}`. Standalone pass: `{standalone_passed}`. "
        "Integration remains disabled.\n"
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
