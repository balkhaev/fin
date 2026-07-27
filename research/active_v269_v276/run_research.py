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
BASE_ROOT = REPO_ROOT / "research" / "active_v253_v260"
BASE_SOURCE = BASE_ROOT / "run_research.py"
_spec = importlib.util.spec_from_file_location("v253_corrected_base", BASE_SOURCE)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"cannot import corrected base engine from {BASE_SOURCE}")
base = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = base
_spec.loader.exec_module(base)

CANDIDATE = "ACTIVE_V269_CRYPTO_LOTTERY_QUALITY"
SYMBOLS = tuple(base.SYMBOLS)
FAMILIES = (
    "low_idiosyncratic_skewness",
    "low_maximum_residual_return",
    "low_normalized_upside_tail",
    "reversed_high_lottery_control",
)
PROMOTABLE_FAMILIES = FAMILIES[:-1]
START = "2021-01-01"
DEVELOPMENT_END_EXCLUSIVE = "2024-01-01"
VALIDATION_START = "2024-01-01"
VALIDATION_END_EXCLUSIVE = "2025-01-01"
HOLDOUT_START = "2025-01-01"
HOLDOUT_END_EXCLUSIVE = "2026-01-01"
FINAL_START = "2026-01-01"
END_EXCLUSIVE = "2026-07-01"
INITIAL_EQUITY = 10_000.0
TARGET_GROSS = 0.50
MAX_REALIZED_GROSS = 0.70
FORCED_EXIT_PENALTY_BPS = 100.0

base.INITIAL_EQUITY = INITIAL_EQUITY
base.TARGET_GROSS = TARGET_GROSS
base.MAX_REALIZED_GROSS = MAX_REALIZED_GROSS
base.FORCED_EXIT_PENALTY_BPS = FORCED_EXIT_PENALTY_BPS


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


@dataclass(frozen=True, slots=True)
class Audit:
    name: str
    cost_bps_per_side: float
    execution_delay_days: int = 0

    @property
    def cost_rate(self) -> float:
        return self.cost_bps_per_side / 10_000.0


POLICIES = tuple(
    Policy(*values)
    for values in product(
        FAMILIES,
        (90, 180, 365),
        (3, 4),
        (14, 28, 56),
        ("dollar", "beta"),
    )
)
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
POST_SELECTION_GATES = {
    "validation_return_positive": True,
    "holdout_return_positive": True,
    "final_return_positive": True,
    "severe_full_cagr_positive": True,
    "latency_full_cagr_positive": True,
    "worst_calendar_year_min": -0.10,
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


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        clean(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def policy_dict(policy: Policy) -> dict[str, Any]:
    return asdict(policy) | {"name": policy.name}


def winsorize_cross_section(frame: pd.DataFrame) -> pd.DataFrame:
    def clip_row(row: pd.Series) -> pd.Series:
        valid = row.dropna()
        if len(valid) < 4:
            return row
        lower = float(valid.quantile(0.05))
        upper = float(valid.quantile(0.95))
        return row.clip(lower=lower, upper=upper)

    return frame.apply(clip_row, axis=1)


def score_frame(market: Any, family: str, lookback: int) -> pd.DataFrame:
    beta = market.beta(lookback).shift(1)
    residual = market.logret - beta.mul(market.market, axis=0)
    minimum = lookback
    residual_std = residual.rolling(lookback, min_periods=minimum).std(ddof=1)
    maximum = residual.rolling(lookback, min_periods=minimum).max()

    if family == "low_idiosyncratic_skewness":
        score = -residual.rolling(lookback, min_periods=minimum).skew()
    elif family == "low_maximum_residual_return":
        score = -maximum
    elif family == "low_normalized_upside_tail":
        upside = residual.clip(lower=0.0).rolling(
            lookback, min_periods=minimum
        ).quantile(0.90)
        score = -(upside / residual_std.replace(0.0, np.nan))
    elif family == "reversed_high_lottery_control":
        score = maximum
    else:
        raise ValueError(family)
    return winsorize_cross_section(score)


def build_weights(
    market: Any,
    policy: Policy,
    score_cache: dict[tuple[str, int], pd.DataFrame],
    raw_cache: dict[tuple[str, int, int, str], pd.DataFrame],
) -> pd.DataFrame:
    score_key = (policy.family, policy.lookback_days)
    if score_key not in score_cache:
        score_cache[score_key] = score_frame(market, *score_key)
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


def self_test() -> None:
    assert len(POLICIES) == 144
    assert sum(policy.family in PROMOTABLE_FAMILIES for policy in POLICIES) == 108
    index = pd.date_range("2019-01-01", periods=1700, freq="1D", tz="UTC")
    rng = np.random.default_rng(269)
    klines: dict[str, pd.DataFrame] = {}
    funding: dict[str, pd.Series] = {}
    for number, symbol in enumerate(SYMBOLS[:9]):
        volatility = 0.008 + number * 0.0015
        innovations = rng.standard_t(df=5 + number % 3, size=len(index)) * volatility
        close = 100.0 * np.exp(np.cumsum(innovations))
        open_price = np.r_[
            close[0],
            close[:-1] * np.exp(rng.normal(0.0, 0.001, len(index) - 1)),
        ]
        klines[symbol] = pd.DataFrame(
            {
                "open": open_price,
                "high": np.maximum(open_price, close) * 1.01,
                "low": np.minimum(open_price, close) * 0.99,
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
    market = base.Market(klines, funding)
    policy = Policy("low_maximum_residual_return", 90, 3, 14, "dollar")
    weights = build_weights(market, policy, {}, {})
    account, diagnostics = base.simulate(
        market, weights, "2021-01-01", "2023-01-01", AUDITS[0]
    )
    assert not account.empty
    assert np.isfinite(account.equity).all()
    assert float(account.gross.max()) <= 0.80
    assert diagnostics["symbol_count_traded"] >= 6
    assert diagnostics["rebalance_events"] < len(account) // 3

    changed = {symbol: frame.copy() for symbol, frame in klines.items()}
    changed[SYMBOLS[0]].iloc[-1, changed[SYMBOLS[0]].columns.get_loc("close")] *= 5.0
    changed_market = base.Market(changed, funding)
    changed_weights = build_weights(changed_market, policy, {}, {})
    pd.testing.assert_frame_equal(weights.iloc[:-1], changed_weights.iloc[:-1])
    print("V269-V276 causal self-test passed")


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


def data_failure_outputs(root: Path, gate: dict[str, Any], records: list[dict[str, Any]]) -> None:
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
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
        "coverage_gate": gate,
        "selection": proof,
        "data_manifest_sha256": canonical_hash(records),
    }
    write_json(results / "selection_proof_before_validation.json", proof)
    write_json(results / "FROZEN_DECISION.json", decision)
    write_json(results / "summary.json", summary)
    pd.DataFrame().to_csv(results / "selection_ranking_before_validation.csv", index=False)
    pd.DataFrame().to_csv(results / "audit_metrics.csv", index=False)
    (results / "REPORT_RU.md").write_text(
        "# Active V269–V276 — crypto lottery quality\n\n"
        "Status: `data_access_insufficient`. P&L and selection were not run.\n"
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
        max_gross=TARGET_GROSS,
        forced_exit_penalty_bps=FORCED_EXIT_PENALTY_BPS,
    )
    klines, funding, records, quality = base.load_v9(config, cache, False)
    pd.DataFrame(records).to_csv(results / "data_manifest.csv", index=False)
    pd.DataFrame(quality).to_csv(results / "data_quality.csv", index=False)
    gate = base.data_gate(klines, records)
    gate = {**gate, "candidate": "V269_FIXED_UNIVERSE_DATA_COVERAGE"}
    write_json(results / "coverage_gate.json", gate)
    if not gate["passed"]:
        data_failure_outputs(root, gate, records)
        print(json.dumps(clean({"status": "data_access_insufficient", "coverage": gate}), indent=2))
        return 0

    market = base.Market(klines, funding)
    score_cache: dict[tuple[str, int], pd.DataFrame] = {}
    raw_cache: dict[tuple[str, int, int, str], pd.DataFrame] = {}
    weights_cache: dict[str, pd.DataFrame] = {}
    ranking_rows: list[dict[str, Any]] = []

    for number, policy in enumerate(POLICIES, start=1):
        weights = build_weights(market, policy, score_cache, raw_cache)
        weights_cache[policy.name] = weights
        account, diagnostics = base.simulate(
            market, weights, START, DEVELOPMENT_END_EXCLUSIVE, AUDITS[0]
        )
        values = base.account_metrics(account)
        years = base.yearly_returns(account, "return")
        all_years_positive = bool(
            not years.empty and (pd.to_numeric(years["return"], errors="coerce") > 0.0).all()
        )
        promotable = policy.family in PROMOTABLE_FAMILIES
        eligible = bool(
            promotable
            and values["cagr"] >= DEVELOPMENT_GATES["cagr_min"]
            and values["sharpe"] >= DEVELOPMENT_GATES["sharpe_min"]
            and values["max_drawdown"] >= DEVELOPMENT_GATES["max_drawdown_min"]
            and diagnostics["rebalance_events"] >= DEVELOPMENT_GATES["rebalance_events_min"]
            and values["annual_turnover"] <= DEVELOPMENT_GATES["annual_turnover_max"]
            and values["max_gross"] <= DEVELOPMENT_GATES["max_realized_gross"]
            and all_years_positive
            and diagnostics["long_leg_pnl"] > 0.0
            and diagnostics["short_leg_pnl"] > 0.0
            and diagnostics["symbol_count_traded"] >= DEVELOPMENT_GATES["symbols_traded_min"]
            and diagnostics["top_positive_asset_pnl_share"]
            <= DEVELOPMENT_GATES["top_positive_asset_pnl_share_max"]
        )
        score = (
            float(values["cagr"])
            + 0.06 * float(values["sharpe"])
            + 0.12 * float(values["max_drawdown"])
            - 0.0005 * float(values["annual_turnover"])
            - 0.02 * max(0.0, float(values["max_gross"]) - MAX_REALIZED_GROSS)
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
                "top_positive_asset_pnl_share": diagnostics["top_positive_asset_pnl_share"],
                "asset_pnl_json": json.dumps(clean(diagnostics["asset_pnl"]), sort_keys=True),
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
    ranking_csv = ranking.to_csv(index=False)
    (results / "selection_ranking_before_validation.csv").write_text(ranking_csv)
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
        "coverage_gate": gate,
        "ranking_sha256": hashlib.sha256(ranking_csv.encode("utf-8")).hexdigest(),
        "design_sha256": sha256_file(root / "V269_V276_DESIGN.json"),
        "selected": policy_dict(selected_policy) if selected_policy is not None else None,
    }
    proof["selection_proof_sha256"] = canonical_hash(proof)
    write_json(results / "selection_proof_before_validation.json", proof)

    limitations = [
        "Public daily archives are not executable bid/ask or queue observations.",
        "The fixed universe includes delisted assets; forced exits carry a 100 bps penalty.",
        "The program-level holdout is not pristine.",
        "Lottery-shape ranking is not a claim about token fundamentals or issuer quality.",
    ]

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
            "development_diagnostics": ranking.head(15).to_dict(orient="records"),
            "limitations": limitations,
            "data_manifest_sha256": canonical_hash(records),
        }
        write_json(results / "FROZEN_DECISION.json", decision)
        write_json(results / "summary.json", summary)
        pd.DataFrame().to_csv(results / "audit_metrics.csv", index=False)
        (results / "REPORT_RU.md").write_text(
            "# Active V269–V276 — crypto lottery quality\n\n"
            "Status: `rejected_before_validation`.\n\n"
            f"Eligible promotable policies: `0/{proof['promotable_policy_count']}`. "
            "Validation 2024, holdout 2025 and final 2026 H1 were not opened.\n"
        )
        write_manifest(root)
        print(json.dumps(clean(summary), indent=2))
        return 0

    selected_weights = weights_cache[selected_policy.name]
    selected_weights.to_csv(results / "frozen_weights.csv")
    audit_rows: list[dict[str, Any]] = []
    audit_accounts: dict[str, pd.DataFrame] = {}
    audit_diagnostics: dict[str, dict[str, Any]] = {}
    for audit in AUDITS:
        account, diagnostics = base.simulate(market, selected_weights, START, END_EXCLUSIVE, audit)
        audit_accounts[audit.name] = account
        audit_diagnostics[audit.name] = diagnostics
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
        row["top_positive_asset_pnl_share"] = diagnostics["top_positive_asset_pnl_share"]
        audit_rows.append(row)

    audits = pd.DataFrame(audit_rows)
    audits.to_csv(results / "audit_metrics.csv", index=False)
    base_row = audits[audits.audit == "base"].iloc[0]
    severe_row = audits[audits.audit == "severe"].iloc[0]
    delay_row = audits[audits.audit == "delay_1d"].iloc[0]
    annual = base.yearly_returns(audit_accounts["base"], selected_policy.name)
    annual.to_csv(results / "ANNUAL_RETURNS.csv", index=False)
    worst_year = float(pd.to_numeric(annual[selected_policy.name], errors="coerce").min())
    post_gate = {
        "validation_return_positive": float(base_row.validation_total_return) > 0.0,
        "holdout_return_positive": float(base_row.holdout_total_return) > 0.0,
        "final_return_positive": float(base_row.final_total_return) > 0.0,
        "severe_full_cagr_positive": float(severe_row.full_cagr) > 0.0,
        "latency_full_cagr_positive": float(delay_row.full_cagr) > 0.0,
        "worst_calendar_year_min": worst_year >= POST_SELECTION_GATES["worst_calendar_year_min"],
        "forced_exit_count_max": int(audit_diagnostics["base"]["forced_exit_count"])
        <= POST_SELECTION_GATES["forced_exit_count_max"],
    }
    standalone_passed = bool(all(post_gate.values()))
    decision = {
        "candidate": CANDIDATE,
        "status": "paper_forward_candidate" if standalone_passed else "rejected_after_oos",
        "eligible_policy_count": int(len(eligible)),
        "selected_policy": selected_policy.name,
        "standalone_selection_passed": standalone_passed,
        "integration_permitted": False,
        "promoted_candidates": [selected_policy.name] if standalone_passed else [],
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    summary = {
        **decision,
        "selection": proof,
        "coverage_gate": gate,
        "post_selection_gate_results": post_gate,
        "base_audit": base_row.to_dict(),
        "severe_audit": severe_row.to_dict(),
        "delay_audit": delay_row.to_dict(),
        "annual_returns": annual.to_dict(orient="records"),
        "diagnostics": audit_diagnostics,
        "limitations": limitations,
        "data_manifest_sha256": canonical_hash(records),
    }
    write_json(results / "FROZEN_DECISION.json", decision)
    write_json(results / "summary.json", summary)
    (results / "REPORT_RU.md").write_text(
        "# Active V269–V276 — crypto lottery quality\n\n"
        f"Status: `{decision['status']}`.\n\n"
        f"Selected policy: `{selected_policy.name}`.\n\n"
        f"Validation return: {float(base_row.validation_total_return):+.2%}; "
        f"holdout return: {float(base_row.holdout_total_return):+.2%}; "
        f"final return: {float(base_row.final_total_return):+.2%}.\n"
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
