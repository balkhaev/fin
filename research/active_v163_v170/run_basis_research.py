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

from basis_config import (
    ASSETS,
    AUDITS,
    END,
    FINAL_START,
    POLICIES,
    POST_SELECTION_GATES,
    PREFINAL_END,
    PREFINAL_GATES,
    START,
    Policy,
)
from basis_data import load_all
from basis_engine import (
    ensemble,
    metrics,
    policy_dict,
    prepare,
    simulate,
    slice_account,
    yearly_returns,
)


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


def v75_yearly(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    frame.index = pd.to_datetime(frame.index, utc=True)
    frame = frame.sort_index()
    rows: list[dict[str, Any]] = []
    previous = 10_000.0
    for year, part in frame.groupby(frame.index.year):
        end_value = float(part.equity.iloc[-1])
        rows.append({"year": int(year), "V75_original": end_value / previous - 1.0})
        previous = end_value
    return pd.DataFrame(rows)


def choose_components(ranking: pd.DataFrame) -> tuple[list[str], bool]:
    eligible = ranking[ranking.eligible_before_final]
    pool = eligible if not eligible.empty else ranking
    selected: list[str] = []
    lookbacks: set[int] = set()
    holds: set[int] = set()
    for _, row in pool.iterrows():
        if not selected or int(row.lookback_hours) not in lookbacks or int(row.max_hold_hours) not in holds:
            selected.append(str(row.policy))
            lookbacks.add(int(row.lookback_hours))
            holds.add(int(row.max_hold_hours))
        if len(selected) == 3:
            break
    for name in pool.policy.astype(str):
        if len(selected) == 3:
            break
        if name not in selected:
            selected.append(name)
    return selected, not eligible.empty


def policy_by_name(name: str) -> Policy:
    return next(policy for policy in POLICIES if policy.name == name)


def self_test() -> None:
    index = pd.date_range("2022-01-01", periods=900, freq="h", tz="UTC")
    rng = np.random.default_rng(7)
    markets: dict[str, pd.DataFrame] = {}
    base = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.002, len(index))))
    for number, asset in enumerate(ASSETS):
        deviation = 0.001 * np.sin(np.arange(len(index)) / (24 + number))
        binance = base * (1 + number * 0.01)
        okx = binance * np.exp(deviation)
        markets[asset] = pd.DataFrame(
            {
                "timestamp": index,
                "open_binance": binance,
                "close_binance": binance * 1.0001,
                "open_okx": okx,
                "close_okx": okx * 1.0001,
            }
        )
    prepared = prepare(markets)
    policy = POLICIES[0]
    account, _ = simulate(prepared, policy, AUDITS[0])
    assert len(account) == len(prepared.index)
    assert account.equity.gt(0).all()
    assert account.gross.max() <= 0.5000001

    changed = {asset: frame.copy() for asset, frame in markets.items()}
    changed[ASSETS[0]].loc[changed[ASSETS[0]].index[-1], "close_okx"] *= 10.0
    prepared_changed = prepare(changed)
    before = prepared.zscores[(ASSETS[0], policy.lookback_hours)][:-1]
    after = prepared_changed.zscores[(ASSETS[0], policy.lookback_hours)][:-1]
    np.testing.assert_allclose(before, after, equal_nan=True)
    print("V165 self-test passed")


def run(args: argparse.Namespace) -> int:
    root = args.root
    results = root / "results"
    processed = root / "inputs" / "processed"
    cache = args.cache
    results.mkdir(parents=True, exist_ok=True)
    processed.mkdir(parents=True, exist_ok=True)

    markets, provenance = load_all(cache, processed)
    prepared = prepare(markets)
    base_audit = next(item for item in AUDITS if item.name == "base")

    ranking_rows: list[dict[str, Any]] = []
    base_accounts: dict[str, pd.DataFrame] = {}
    base_trades: dict[str, pd.DataFrame] = {}
    for number, policy in enumerate(POLICIES, start=1):
        account, trades = simulate(prepared, policy, base_audit)
        base_accounts[policy.name] = account
        base_trades[policy.name] = trades
        prefinal = slice_account(account, START, PREFINAL_END)
        m = metrics(prefinal)
        yearly = yearly_returns(prefinal, "return")
        prefinal_years = yearly[yearly.year <= 2025]
        all_positive = bool(not prefinal_years.empty and (prefinal_years["return"] > 0).all())
        eligible = bool(
            m["cagr"] >= PREFINAL_GATES["cagr_min"]
            and m["sharpe"] >= PREFINAL_GATES["sharpe_min"]
            and m["max_drawdown"] >= PREFINAL_GATES["max_drawdown_min"]
            and all_positive
            and m["annual_turnover"] <= PREFINAL_GATES["annual_turnover_max"]
        )
        score = (
            float(m["cagr"])
            + 0.08 * float(m["sharpe"])
            + 0.10 * float(m["max_drawdown"])
            - 0.0005 * float(m["annual_turnover"])
        )
        ranking_rows.append(
            {
                "policy": policy.name,
                **asdict(policy),
                "eligible_before_final": eligible,
                "all_prefinal_years_positive": all_positive,
                "score": score,
                **{f"prefinal_{key}": value for key, value in m.items()},
            }
        )
        if number % 20 == 0:
            print(f"processed {number}/{len(POLICIES)} policies", flush=True)

    ranking = pd.DataFrame(ranking_rows).sort_values(
        ["eligible_before_final", "score"], ascending=[False, False]
    )
    ranking.to_csv(results / "selection_ranking_before_final.csv", index=False)
    selected_names, any_eligible = choose_components(ranking)
    selected_policies = [policy_by_name(name) for name in selected_names]

    proof = {
        "candidate": "V165_CROSS_VENUE_BASIS_CONVERGENCE",
        "selection_cutoff": "2025-12-31T23:59:59Z",
        "selection_uses_2026": False,
        "program_level_final_pristine": False,
        "policy_count": len(POLICIES),
        "eligible_policy_count": int(ranking.eligible_before_final.sum()),
        "selected": [policy_dict(policy) for policy in selected_policies],
        "gates": PREFINAL_GATES,
        "ranking_sha256": hashlib.sha256(
            ranking.to_csv(index=False).encode("utf-8")
        ).hexdigest(),
        "design_sha256": hashlib.sha256(
            (root / "V165_BASIS_DESIGN.json").read_bytes()
        ).hexdigest(),
    }
    proof["selection_proof_sha256"] = canonical_hash(proof)
    (results / "selection_proof_before_final.json").write_text(
        json.dumps(proof, indent=2) + "\n"
    )

    audit_rows: list[dict[str, Any]] = []
    audit_accounts: dict[str, pd.DataFrame] = {}
    selected_trade_frames: list[pd.DataFrame] = []
    for audit in AUDITS:
        accounts: list[pd.DataFrame] = []
        for policy in selected_policies:
            if audit.name == "base":
                account = base_accounts[policy.name]
                trades = base_trades[policy.name]
            else:
                account, trades = simulate(prepared, policy, audit)
            accounts.append(account)
            if audit.name == "base" and not trades.empty:
                selected_trade_frames.append(trades.assign(component=policy.name))
        combined = ensemble(accounts)
        audit_accounts[audit.name] = combined
        combined.to_csv(results / f"{audit.name}_equity.csv")
        full = metrics(combined)
        prefinal = metrics(slice_account(combined, START, PREFINAL_END))
        final = metrics(slice_account(combined, FINAL_START, END))
        audit_rows.append(
            {
                "audit": audit.name,
                **asdict(audit),
                **full,
                "prefinal_cagr": prefinal["cagr"],
                "prefinal_sharpe": prefinal["sharpe"],
                "prefinal_max_drawdown": prefinal["max_drawdown"],
                "final_return": final["total_return"],
            }
        )
    audit_frame = pd.DataFrame(audit_rows)
    audit_frame.to_csv(results / "audit_metrics.csv", index=False)
    if selected_trade_frames:
        pd.concat(selected_trade_frames, ignore_index=True).to_csv(
            results / "selected_trades.csv", index=False
        )
    else:
        pd.DataFrame().to_csv(results / "selected_trades.csv", index=False)

    candidate = audit_accounts["base"]
    candidate_full = metrics(candidate)
    candidate_prefinal = metrics(slice_account(candidate, START, PREFINAL_END))
    candidate_final = metrics(slice_account(candidate, FINAL_START, END))
    candidate_yearly = yearly_returns(candidate, "V165_basis")
    worst_year = float(candidate_yearly.V165_basis.min()) if not candidate_yearly.empty else 0.0
    severe_full = metrics(audit_accounts["severe"])
    checks = {
        "eligible_before_final": any_eligible,
        "prefinal_cagr": candidate_prefinal["cagr"] >= PREFINAL_GATES["cagr_min"],
        "prefinal_sharpe": candidate_prefinal["sharpe"] >= PREFINAL_GATES["sharpe_min"],
        "prefinal_max_drawdown": candidate_prefinal["max_drawdown"] >= PREFINAL_GATES["max_drawdown_min"],
        "all_prefinal_years_positive": bool(
            (candidate_yearly[candidate_yearly.year <= 2025].V165_basis > 0).all()
        ),
        "severe_full_cagr_positive": severe_full["cagr"] > 0,
        "worst_year": worst_year >= POST_SELECTION_GATES["worst_year_min"],
        "final_return_positive": candidate_final["total_return"] > 0,
        "zero_liquidations": bool((audit_frame.liquidations == 0).all()),
        "positive_margin_buffer": float(audit_frame.min_margin_buffer.min()) > 0,
    }
    standalone_passed = all(checks.values())

    integration: dict[str, Any] = {
        "permitted": standalone_passed,
        "tested": False,
        "reason": None if standalone_passed else "standalone gates failed",
    }
    if standalone_passed:
        atlas = pd.read_csv(args.atlas, index_col=0, parse_dates=True)
        atlas.index = pd.to_datetime(atlas.index, utc=True)
        atlas_daily = atlas.equity.resample("1D").last().ffill()
        sleeve_daily = candidate.equity.resample("1D").last().ffill()
        joined = pd.concat([atlas_daily, sleeve_daily], axis=1, join="inner")
        joined.columns = ["atlas", "sleeve"]
        ar = joined.atlas.pct_change().fillna(joined.atlas.iloc[0] / 10_000.0 - 1.0)
        sr = joined.sleeve.pct_change().fillna(joined.sleeve.iloc[0] / 10_000.0 - 1.0)
        rows = []
        for weight in (0.05, 0.10, 0.15):
            equity = 10_000.0 * (1 + (1 - weight) * ar + weight * sr).cumprod()
            account = pd.DataFrame(
                {
                    "equity": equity,
                    "gross": 0.0,
                    "turnover": 0.0,
                    "costs": 0.0,
                    "funding_buffer_cost": 0.0,
                    "forced_costs": 0.0,
                    "trade_events": 0.0,
                    "liquidated_notional": 0.0,
                    "min_margin_buffer": 1.0,
                }
            )
            rows.append({"weight": weight, **metrics(account)})
        integration = {"permitted": True, "tested": True, "weights": rows}

    annual = v75_yearly(args.atlas).merge(candidate_yearly, on="year", how="outer")
    annual = annual.sort_values("year")
    annual.to_csv(results / "ANNUAL_RETURNS.csv", index=False)

    quality_rows = []
    for asset, value in provenance["assets"].items():
        quality_rows.append(
            {
                "asset": asset,
                "binance_rows": value["binance"]["rows"],
                "okx_rows": value["okx"]["rows"],
                "aligned_rows": value["aligned_rows"],
                "timestamp_min": value["timestamp_min"],
                "timestamp_max": value["timestamp_max"],
            }
        )
    pd.DataFrame(quality_rows).to_csv(results / "data_quality.csv", index=False)

    status = (
        "frozen_historical_candidate_needs_forward"
        if standalone_passed
        else "rejected_or_needs_iteration"
    )
    summary = {
        "candidate": "ACTIVE_V165_CROSS_VENUE_BASIS_CONVERGENCE",
        "status": status,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
        "selection": proof,
        "checks": checks,
        "standalone_selection_passed": standalone_passed,
        "integration": integration,
        "candidate_full": candidate_full,
        "candidate_prefinal": candidate_prefinal,
        "candidate_final_2026h1": candidate_final,
        "worst_year": worst_year,
        "audit_metrics": audit_frame.to_dict(orient="records"),
        "data_quality": quality_rows,
        "limitations": [
            "OKX and Binance candles are public historical observations, not synchronized executable quotes.",
            "Funding is charged through a conservative fixed buffer because full OKX historical funding is unavailable.",
            "Program-level 2026 final is not pristine because the broader program has already inspected it.",
            "Cross-venue transfer, outage, collateral and liquidation-engine risks are not fully modeled.",
        ],
    }
    (results / "summary.json").write_text(json.dumps(summary, indent=2, default=float) + "\n")
    (results / "FROZEN_DECISION.json").write_text(
        json.dumps(
            {
                "candidate": summary["candidate"],
                "status": status,
                "standalone_selection_passed": standalone_passed,
                "integration_permitted": integration["permitted"],
                "promoted_candidates": [summary["candidate"]] if standalone_passed else [],
                "live_ready": False,
                "real_leverage_authorized": False,
            },
            indent=2,
        )
        + "\n"
    )

    report = [
        "# Active V165 — cross-venue basis convergence",
        "",
        f"Status: `{status}`",
        "",
        f"Policies: {len(POLICIES)}; eligible before final: {proof['eligible_policy_count']}.",
        "",
        "| Metric | Full | Prefinal | 2026 H1 |",
        "|---|---:|---:|---:|",
        f"| CAGR | {candidate_full['cagr']:.2%} | {candidate_prefinal['cagr']:.2%} | {candidate_final['cagr']:.2%} |",
        f"| Total return | {candidate_full['total_return']:.2%} | {candidate_prefinal['total_return']:.2%} | {candidate_final['total_return']:.2%} |",
        f"| Max DD | {candidate_full['max_drawdown']:.2%} | {candidate_prefinal['max_drawdown']:.2%} | {candidate_final['max_drawdown']:.2%} |",
        f"| Sharpe | {candidate_full['sharpe']:.3f} | {candidate_prefinal['sharpe']:.3f} | {candidate_final['sharpe']:.3f} |",
        "",
        "V75 remains the mandatory control and is the first column in `ANNUAL_RETURNS.csv`.",
        "No live trading or real leverage is authorized.",
    ]
    (results / "REPORT_RU.md").write_text("\n".join(report) + "\n")
    print(json.dumps(summary, indent=2, default=float))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--cache", type=Path, default=Path(".cache/v165_basis"))
    parser.add_argument("--atlas", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.atlas is None:
        raise SystemExit("--atlas is required")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
