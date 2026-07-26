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
    START,
    Policy,
)
from data import _funding_blocks, load_all
from engine import (
    bootstrap_trade_mean,
    circular_block_bootstrap_total_return,
    ensemble,
    metrics,
    policy_dict,
    prepare,
    simulate,
    slice_account,
    yearly_returns,
)

CANDIDATE = "ACTIVE_V171_BINANCE_HYPERLIQUID_FUNDING"
MIN_FULL_ASSETS = 3
MIN_USABLE_ROWS = 2_800
MIN_PRICE_COVERAGE = 0.95
MIN_FUNDING_COVERAGE = 0.90
LATEST_ACCEPTABLE_START = pd.Timestamp("2023-08-01", tz="UTC")
EARLIEST_ACCEPTABLE_END = pd.Timestamp("2026-06-20", tz="UTC")


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


def pct(value: Any) -> str:
    return f"{100.0 * float(value):+.2f}%"


def policy_by_name(name: str) -> Policy:
    return next(policy for policy in POLICIES if policy.name == name)


def choose_components(ranking: pd.DataFrame) -> tuple[list[str], bool]:
    eligible = ranking[ranking.eligible_development]
    traded = ranking[ranking.development_trade_count > 0]
    pool = eligible if not eligible.empty else (traded if not traded.empty else ranking)
    selected: list[str] = []
    lookbacks: set[int] = set()
    holds: set[int] = set()
    for _, row in pool.iterrows():
        lookback = int(row.lookback_blocks)
        hold = int(row.hold_blocks)
        if not selected or lookback not in lookbacks or hold not in holds:
            selected.append(str(row.policy))
            lookbacks.add(lookback)
            holds.add(hold)
        if len(selected) == 3:
            break
    for name in pool.policy.astype(str):
        if len(selected) == 3:
            break
        if name not in selected:
            selected.append(name)
    return selected, not eligible.empty


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


def coverage_gate(provenance: dict[str, Any]) -> dict[str, Any]:
    assets: list[dict[str, Any]] = []
    full_assets: list[str] = []
    for asset in ASSETS:
        value = provenance.get("assets", {}).get(asset, {})
        start_raw = value.get("usable_timestamp_min")
        end_raw = value.get("usable_timestamp_max")
        start = pd.Timestamp(start_raw) if start_raw else None
        end = pd.Timestamp(end_raw) if end_raw else None
        if start is not None and start.tzinfo is None:
            start = start.tz_localize("UTC")
        if end is not None and end.tzinfo is None:
            end = end.tz_localize("UTC")
        row = {
            "asset": asset,
            "rows": int(value.get("rows", 0)),
            "usable_rows": int(value.get("usable_rows", 0)),
            "price_coverage": float(value.get("price_coverage", 0.0)),
            "funding_coverage": float(value.get("funding_coverage", 0.0)),
            "usable_timestamp_min": start_raw,
            "usable_timestamp_max": end_raw,
            "starts_on_time": bool(start is not None and start <= LATEST_ACCEPTABLE_START),
            "ends_on_time": bool(end is not None and end >= EARLIEST_ACCEPTABLE_END),
        }
        row["enough_rows"] = row["usable_rows"] >= MIN_USABLE_ROWS
        row["price_coverage_ok"] = row["price_coverage"] >= MIN_PRICE_COVERAGE
        row["funding_coverage_ok"] = row["funding_coverage"] >= MIN_FUNDING_COVERAGE
        row["full_coverage"] = bool(
            row["starts_on_time"]
            and row["ends_on_time"]
            and row["enough_rows"]
            and row["price_coverage_ok"]
            and row["funding_coverage_ok"]
        )
        if row["full_coverage"]:
            full_assets.append(asset)
        assets.append(row)
    return {
        "candidate": "V171_DATA_COVERAGE",
        "minimum_full_assets": MIN_FULL_ASSETS,
        "minimum_usable_rows": MIN_USABLE_ROWS,
        "minimum_price_coverage": MIN_PRICE_COVERAGE,
        "minimum_funding_coverage": MIN_FUNDING_COVERAGE,
        "latest_acceptable_start": LATEST_ACCEPTABLE_START.isoformat(),
        "earliest_acceptable_end": EARLIEST_ACCEPTABLE_END.isoformat(),
        "full_assets": full_assets,
        "passed": len(full_assets) >= MIN_FULL_ASSETS,
        "assets": assets,
    }


def self_test() -> None:
    # Funding stamped at 08:00 belongs to the interval that ended at 08:00,
    # not to a position opened just after 08:00.
    payments = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2024-01-01 01:00", "2024-01-01 08:00"], utc=True
            ),
            "rate": [0.001, 0.002],
        }
    )
    blocks = _funding_blocks(payments, "test")
    assert blocks.loc[0, "timestamp"] == pd.Timestamp("2024-01-01 00:00", tz="UTC")
    assert abs(float(blocks.loc[0, "funding_test"]) - 0.003) < 1e-12

    index = pd.date_range("2023-05-01", periods=420, freq="8h", tz="UTC")
    rng = np.random.default_rng(171)
    base = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.002, len(index))))
    markets: dict[str, pd.DataFrame] = {}
    for number, asset in enumerate(ASSETS):
        basis = 0.0002 * np.sin(np.arange(len(index)) / (15 + number))
        funding_binance = np.full(len(index), 0.0008)
        funding_hyperliquid = np.full(len(index), -0.0004)
        markets[asset] = pd.DataFrame(
            {
                "timestamp": index,
                "open_binance": base * (1.0 + number * 0.01),
                "open_hyperliquid": base
                * (1.0 + number * 0.01)
                * np.exp(basis),
                "funding_binance": funding_binance,
                "funding_hyperliquid": funding_hyperliquid,
                "funding_spread": funding_binance - funding_hyperliquid,
                "funding_complete": True,
                "basis_bps": basis * 10_000.0,
            }
        )
    prepared = prepare(markets)
    policy = Policy(lookback_blocks=3, min_predicted_edge_bps=16.0, hold_blocks=3, max_abs_basis_bps=40.0)
    base_audit = next(audit for audit in AUDITS if audit.name == "base")
    account, trades = simulate(prepared, policy, base_audit)
    assert metrics(account)["trade_count"] > 10
    assert float(account.gross.max()) <= 1.01
    assert not trades.empty
    fixed = trades[trades.exit_reason == "fixed_horizon"]
    assert not fixed.empty and (fixed.holding_blocks == 3).all()
    assert (trades.net_return > -0.05).all()

    changed = {asset: frame.copy() for asset, frame in markets.items()}
    changed[ASSETS[0]].loc[200, "funding_spread"] *= -100.0
    before = prepared.frames[ASSETS[0]]["forecast_3"].iloc[:201]
    after = prepare(changed).frames[ASSETS[0]]["forecast_3"].iloc[:201]
    np.testing.assert_allclose(before, after, equal_nan=True)
    print("V171-V178 self-test passed")


def write_manifest(root: Path) -> None:
    manifest_files: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        rel = str(path.relative_to(root))
        if rel in {"MANIFEST.json", "run.log"}:
            continue
        manifest_files[rel] = {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    (root / "MANIFEST.json").write_text(
        json.dumps({"candidate": CANDIDATE, "files": manifest_files}, indent=2) + "\n"
    )


def data_failure_outputs(
    root: Path,
    results: Path,
    gate: dict[str, Any],
    provenance: dict[str, Any],
    atlas: Path,
) -> None:
    results.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(gate["assets"]).to_csv(results / "data_quality.csv", index=False)
    pd.DataFrame().to_csv(results / "selection_ranking_before_holdout.csv", index=False)
    pd.DataFrame().to_csv(results / "selected_trades.csv", index=False)
    pd.DataFrame().to_csv(results / "audit_metrics.csv", index=False)
    v75_yearly(atlas).to_csv(results / "ANNUAL_RETURNS.csv", index=False)
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
        "checks": {},
        "limitations": [
            "At least three fixed assets must have synchronized Binance and Hyperliquid price/funding coverage.",
            "No unavailable asset or venue is replaced after observing results.",
            "Program-level holdout is not pristine.",
        ],
        "provenance_sha256": canonical_hash(provenance),
    }
    (results / "FROZEN_DECISION.json").write_text(json.dumps(decision, indent=2) + "\n")
    (results / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (results / "selection_proof_before_holdout.json").write_text(
        json.dumps(
            {
                "candidate": CANDIDATE,
                "selection_not_run": True,
                "reason": "data coverage gate failed",
                "coverage_gate": gate,
            },
            indent=2,
        )
        + "\n"
    )
    report = [
        "# Active V171–V178 — Binance/Hyperliquid funding sleeve",
        "",
        "Status: `data_access_insufficient`.",
        "",
        f"Full assets: `{', '.join(gate['full_assets']) or 'none'}`; required: {gate['minimum_full_assets']}.",
        "",
        "The strategy grid was not selected or evaluated. V75 remains the control; live trading and real leverage remain disabled.",
    ]
    (results / "REPORT_RU.md").write_text("\n".join(report) + "\n")
    write_manifest(root)


def run(args: argparse.Namespace) -> int:
    root = args.root
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
    markets, provenance = load_all(root, args.raw_cache, args.v165_processed)
    gate = coverage_gate(provenance)
    (results / "coverage_gate.json").write_text(json.dumps(gate, indent=2) + "\n")
    pd.DataFrame(gate["assets"]).to_csv(results / "data_quality.csv", index=False)
    if not gate["passed"]:
        data_failure_outputs(root, results, gate, provenance, args.atlas)
        print(json.dumps({"status": "data_access_insufficient", "coverage": gate}, indent=2))
        return 0

    prepared = prepare(markets)
    base_audit = next(audit for audit in AUDITS if audit.name == "base")
    ranking_rows: list[dict[str, Any]] = []
    base_accounts: dict[str, pd.DataFrame] = {}
    base_trades: dict[str, pd.DataFrame] = {}
    for number, policy in enumerate(POLICIES, start=1):
        account, trades = simulate(prepared, policy, base_audit)
        base_accounts[policy.name] = account
        base_trades[policy.name] = trades
        development = slice_account(account, START, DEVELOPMENT_END)
        values = metrics(development)
        yearly = yearly_returns(development, "return")
        all_positive = bool(
            not yearly.empty and (pd.to_numeric(yearly["return"]) > 0.0).all()
        )
        eligible = bool(
            values["cagr"] >= DEVELOPMENT_GATES["cagr_min"]
            and values["sharpe"] >= DEVELOPMENT_GATES["sharpe_min"]
            and values["max_drawdown"] >= DEVELOPMENT_GATES["max_drawdown_min"]
            and all_positive
            and values["trade_count"] >= DEVELOPMENT_GATES["trade_count_min"]
            and values["annual_turnover"] <= DEVELOPMENT_GATES["annual_turnover_max"]
        )
        score = (
            float(values["cagr"])
            + 0.08 * float(values["sharpe"])
            + 0.12 * float(values["max_drawdown"])
            - 0.0003 * float(values["annual_turnover"])
        )
        ranking_rows.append(
            {
                "policy": policy.name,
                **asdict(policy),
                "eligible_development": eligible,
                "all_development_years_positive": all_positive,
                "score": score,
                **{f"development_{key}": value for key, value in values.items()},
            }
        )
        if number % 10 == 0:
            print(f"processed {number}/{len(POLICIES)} policies", flush=True)

    ranking = pd.DataFrame(ranking_rows).sort_values(
        ["eligible_development", "score"], ascending=[False, False]
    )
    ranking.to_csv(results / "selection_ranking_before_holdout.csv", index=False)
    selected_names, any_eligible = choose_components(ranking)
    selected_policies = [policy_by_name(name) for name in selected_names]
    proof = {
        "candidate": CANDIDATE,
        "selection_cutoff": "2024-12-31T23:59:59Z",
        "selection_uses_2025": False,
        "selection_uses_2026": False,
        "program_level_holdout_pristine": False,
        "policy_count": len(POLICIES),
        "eligible_policy_count": int(ranking.eligible_development.sum()),
        "selected": [policy_dict(policy) for policy in selected_policies],
        "development_gates": DEVELOPMENT_GATES,
        "post_selection_gates": POST_SELECTION_GATES,
        "ranking_sha256": hashlib.sha256(
            ranking.to_csv(index=False).encode("utf-8")
        ).hexdigest(),
        "design_sha256": sha256_file(root / "V171_V178_DESIGN.json"),
        "coverage_gate": gate,
    }
    proof["selection_proof_sha256"] = canonical_hash(proof)
    (results / "selection_proof_before_holdout.json").write_text(
        json.dumps(proof, indent=2, default=float) + "\n"
    )

    audit_rows: list[dict[str, Any]] = []
    audit_accounts: dict[str, pd.DataFrame] = {}
    selected_trade_frames: list[pd.DataFrame] = []
    selected_forced_exits: dict[str, int] = {}
    for audit in AUDITS:
        accounts: list[pd.DataFrame] = []
        forced = 0
        for policy in selected_policies:
            if audit.name == "base":
                account = base_accounts[policy.name]
                trades = base_trades[policy.name]
            else:
                account, trades = simulate(prepared, policy, audit)
            accounts.append(account)
            forced += int(account.forced_exits.sum())
            if audit.name == "base" and not trades.empty:
                selected_trade_frames.append(
                    trades.assign(component=policy.name, component_weight=1.0 / len(selected_policies))
                )
        combined = ensemble(accounts)
        audit_accounts[audit.name] = combined
        selected_forced_exits[audit.name] = forced
        combined.to_csv(results / f"{audit.name}_equity.csv")
        full = metrics(combined)
        development = metrics(slice_account(combined, START, DEVELOPMENT_END))
        holdout = metrics(slice_account(combined, HOLDOUT_START, HOLDOUT_END))
        final = metrics(slice_account(combined, FINAL_START, END))
        audit_rows.append(
            {
                "audit": audit.name,
                **asdict(audit),
                **full,
                "development_cagr": development["cagr"],
                "development_sharpe": development["sharpe"],
                "development_max_drawdown": development["max_drawdown"],
                "holdout_return": holdout["total_return"],
                "final_return": final["total_return"],
                "component_forced_exits": forced,
            }
        )
    audit_frame = pd.DataFrame(audit_rows)
    audit_frame.to_csv(results / "audit_metrics.csv", index=False)
    selected_trades = (
        pd.concat(selected_trade_frames, ignore_index=True)
        if selected_trade_frames
        else pd.DataFrame()
    )
    selected_trades.to_csv(results / "selected_trades.csv", index=False)

    candidate = audit_accounts["base"]
    candidate_full = metrics(candidate)
    candidate_development = metrics(slice_account(candidate, START, DEVELOPMENT_END))
    candidate_holdout = metrics(slice_account(candidate, HOLDOUT_START, HOLDOUT_END))
    candidate_final = metrics(slice_account(candidate, FINAL_START, END))
    candidate_yearly = yearly_returns(candidate, "V171_funding")
    worst_year = (
        float(pd.to_numeric(candidate_yearly.V171_funding).min())
        if not candidate_yearly.empty
        else 0.0
    )
    severe_full = metrics(audit_accounts["severe"])
    delay_full = metrics(audit_accounts["delay_8h"])
    oos = slice_account(candidate, HOLDOUT_START, END)
    bootstrap = {
        "trade_mean": bootstrap_trade_mean(selected_trades),
        "oos_circular_block": circular_block_bootstrap_total_return(oos),
    }
    (results / "bootstrap.json").write_text(json.dumps(bootstrap, indent=2) + "\n")

    checks = {
        "eligible_development": any_eligible,
        "development_cagr": candidate_development["cagr"] >= DEVELOPMENT_GATES["cagr_min"],
        "development_sharpe": candidate_development["sharpe"] >= DEVELOPMENT_GATES["sharpe_min"],
        "development_max_drawdown": candidate_development["max_drawdown"] >= DEVELOPMENT_GATES["max_drawdown_min"],
        "all_development_years_positive": bool(
            not candidate_yearly[candidate_yearly.year <= 2024].empty
            and (candidate_yearly[candidate_yearly.year <= 2024].V171_funding > 0.0).all()
        ),
        "development_trade_count": candidate_development["trade_count"] >= DEVELOPMENT_GATES["trade_count_min"],
        "annual_turnover": candidate_development["annual_turnover"] <= DEVELOPMENT_GATES["annual_turnover_max"],
        "holdout_return_positive": candidate_holdout["total_return"] > 0.0,
        "final_return_positive": candidate_final["total_return"] > 0.0,
        "severe_full_cagr_positive": severe_full["cagr"] > 0.0,
        "delay_full_cagr_positive": delay_full["cagr"] > 0.0,
        "worst_year": worst_year >= POST_SELECTION_GATES["worst_year_min"],
        "full_trade_count": candidate_full["trade_count"] >= POST_SELECTION_GATES["trade_count_min"],
        "zero_forced_exits": all(value == 0 for value in selected_forced_exits.values()),
        "data_coverage": gate["passed"],
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
        atlas_returns = joined.atlas.pct_change().fillna(0.0)
        sleeve_returns = joined.sleeve.pct_change().fillna(0.0)
        rows = []
        for weight in (0.05, 0.10, 0.15):
            equity = 10_000.0 * (
                1.0 + (1.0 - weight) * atlas_returns + weight * sleeve_returns
            ).cumprod()
            account = pd.DataFrame(
                {
                    "equity": equity,
                    "gross": 0.0,
                    "turnover": 0.0,
                    "costs": 0.0,
                    "funding_pnl": 0.0,
                    "price_pnl": 0.0,
                    "missing_penalties": 0.0,
                    "trade_events": 0.0,
                    "forced_exits": 0.0,
                }
            )
            rows.append({"weight": weight, **metrics(account)})
        integration = {"permitted": True, "tested": True, "weights": rows}

    annual = v75_yearly(args.atlas).merge(candidate_yearly, on="year", how="outer")
    annual.sort_values("year").to_csv(results / "ANNUAL_RETURNS.csv", index=False)

    status = (
        "frozen_historical_candidate_needs_forward"
        if standalone_passed
        else "rejected_or_needs_iteration"
    )
    summary = {
        "candidate": CANDIDATE,
        "status": status,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
        "selection": proof,
        "coverage_gate": gate,
        "checks": checks,
        "standalone_selection_passed": standalone_passed,
        "integration": integration,
        "candidate_full": candidate_full,
        "candidate_development": candidate_development,
        "candidate_holdout_2025": candidate_holdout,
        "candidate_final_2026h1": candidate_final,
        "worst_year": worst_year,
        "audit_metrics": audit_frame.to_dict(orient="records"),
        "bootstrap": bootstrap,
        "limitations": [
            "Binance observations are checksum-verified public files; Hyperliquid observations are public API snapshots persisted in the repository.",
            "Hyperliquid hourly funding inside each 8h block is valued at the block-end boundary price, not at every hourly mark.",
            "Public candles are not synchronized executable bid/ask quotes and do not model venue outages or collateral transfer delays.",
            "The broader research program has already inspected 2025-2026, so program-level holdout is not pristine.",
            "Even a passed historical candidate remains paper-forward only; live trading and real leverage are disabled.",
        ],
        "provenance_sha256": canonical_hash(provenance),
    }
    (results / "summary.json").write_text(
        json.dumps(summary, indent=2, default=float) + "\n"
    )
    decision = {
        "candidate": CANDIDATE,
        "status": status,
        "standalone_selection_passed": standalone_passed,
        "integration_permitted": integration["permitted"],
        "promoted_candidates": [CANDIDATE] if standalone_passed else [],
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    (results / "FROZEN_DECISION.json").write_text(
        json.dumps(decision, indent=2) + "\n"
    )

    report = [
        "# Active V171–V178 — Binance/Hyperliquid funding sleeve",
        "",
        f"Status: `{status}`.",
        "",
        f"Policies: {len(POLICIES)}; eligible on development: {proof['eligible_policy_count']}.",
        f"Selected diagnostics/components: `{', '.join(selected_names)}`.",
        "",
        "| Metric | Full | Development 2023-2024 | Holdout 2025 | Final 2026 H1 |",
        "|---|---:|---:|---:|---:|",
        f"| CAGR | {pct(candidate_full['cagr'])} | {pct(candidate_development['cagr'])} | {pct(candidate_holdout['cagr'])} | {pct(candidate_final['cagr'])} |",
        f"| Total return | {pct(candidate_full['total_return'])} | {pct(candidate_development['total_return'])} | {pct(candidate_holdout['total_return'])} | {pct(candidate_final['total_return'])} |",
        f"| Max DD | {pct(candidate_full['max_drawdown'])} | {pct(candidate_development['max_drawdown'])} | {pct(candidate_holdout['max_drawdown'])} | {pct(candidate_final['max_drawdown'])} |",
        f"| Sharpe | {float(candidate_full['sharpe']):.3f} | {float(candidate_development['sharpe']):.3f} | {float(candidate_holdout['sharpe']):.3f} | {float(candidate_final['sharpe']):.3f} |",
        "",
        f"Severe full CAGR: {pct(severe_full['cagr'])}; 8h-delay full CAGR: {pct(delay_full['cagr'])}.",
        f"OOS block-bootstrap probability of positive total return: {bootstrap['oos_circular_block']['probability_positive']:.1%}.",
        "",
        "## Promotion checks",
        "",
    ]
    for name, value in checks.items():
        report.append(f"- [{'x' if value else ' '}] `{name}`")
    report += [
        "",
        "V75 remains the mandatory first control column in `ANNUAL_RETURNS.csv`.",
        "No live trading or real leverage is authorized.",
    ]
    (results / "REPORT_RU.md").write_text("\n".join(report) + "\n")
    write_manifest(root)
    print(json.dumps(summary, indent=2, default=float))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--raw-cache", type=Path)
    parser.add_argument("--v165-processed", type=Path)
    parser.add_argument("--atlas", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.raw_cache is None or args.v165_processed is None or args.atlas is None:
        raise SystemExit("--raw-cache, --v165-processed and --atlas are required")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
