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
    AUDITS,
    DEVELOPMENT_END,
    DEVELOPMENT_GATES,
    END,
    FINAL_START,
    HOLDOUT_END,
    HOLDOUT_START,
    INITIAL_EQUITY,
    POLICIES,
    POST_SELECTION_GATES,
    START,
    SYMBOLS,
    VALIDATION_END,
    VALIDATION_START,
    Policy,
)
from data import load_all
from engine import (
    calendar_returns,
    concentration_metrics,
    metrics,
    period_metrics,
    policy_dict,
    policy_signal,
    simulate,
    slice_account,
    slice_trades,
    trade_bootstrap,
)

CANDIDATE = "ACTIVE_V198_DEPTH_REPLENISHMENT"


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


def v75_yearly(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    frame.index = pd.to_datetime(frame.index, utc=True)
    frame = frame.sort_index()
    rows: list[dict[str, Any]] = []
    previous = INITIAL_EQUITY
    for year, part in frame.groupby(frame.index.year):
        end_value = float(part.equity.iloc[-1])
        rows.append({"year": int(year), "V75_original": end_value / previous - 1.0})
        previous = end_value
    return pd.DataFrame(rows)


def write_manifest(root: Path) -> None:
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if (
            not path.is_file()
            or path.name == "MANIFEST.json"
            or "__pycache__" in path.parts
            or path.suffix == ".pyc"
            or "raw_cache" in path.parts
        ):
            continue
        rel = str(path.relative_to(root))
        files[rel] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    (root / "MANIFEST.json").write_text(
        json.dumps({"candidate": CANDIDATE, "files": files}, indent=2) + "\n"
    )


def empty_annual(atlas: Path, results: Path) -> None:
    v75_yearly(atlas).to_csv(results / "ANNUAL_RETURNS.csv", index=False)


def failure_outputs(
    root: Path,
    results: Path,
    gate: dict[str, Any],
    provenance: dict[str, Any],
    quality: pd.DataFrame,
    atlas: Path,
) -> None:
    results.mkdir(parents=True, exist_ok=True)
    quality.to_csv(results / "data_quality.csv", index=False)
    (results / "coverage_gate.json").write_text(json.dumps(gate, indent=2) + "\n")
    (results / "provenance.json").write_text(
        json.dumps(provenance, indent=2, default=float) + "\n"
    )
    pd.DataFrame().to_csv(results / "selection_ranking_development.csv", index=False)
    pd.DataFrame().to_csv(results / "selected_trades.csv", index=False)
    pd.DataFrame().to_csv(results / "audit_metrics.csv", index=False)
    empty_annual(atlas, results)
    proof = {
        "candidate": CANDIDATE,
        "selection_not_run": True,
        "reason": "data coverage gate failed",
        "coverage_gate": gate,
    }
    (results / "selection_proof_before_validation.json").write_text(
        json.dumps(proof, indent=2) + "\n"
    )
    decision = {
        "candidate": CANDIDATE,
        "status": "data_access_insufficient",
        "data_coverage_passed": False,
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
        "provenance_sha256": canonical_hash(provenance),
        "limitations": [
            "A missing or incomplete depth month is not filled with zero liquidity.",
            "No policy return is calculated when the frozen coverage gate fails.",
            "Reference-price research cannot establish executable live performance.",
        ],
    }
    (results / "FROZEN_DECISION.json").write_text(
        json.dumps(decision, indent=2) + "\n"
    )
    (results / "summary.json").write_text(
        json.dumps(summary, indent=2, default=float) + "\n"
    )
    (results / "REPORT_RU.md").write_text(
        "# V198–V204 depth research\n\n"
        "Status: `data_access_insufficient`.\n\n"
        "Frozen data coverage gates failed; no P&L or threshold selection was run.\n\n"
        "`live_ready=false`; `real_leverage_authorized=false`; "
        "`profitability_proven=false`.\n"
    )
    write_manifest(root)


def self_test() -> None:
    index = pd.date_range("2024-01-01", periods=3_000, freq="1min", tz="UTC")
    base_price = 100.0 * np.exp(np.cumsum(np.full(len(index), 0.00001)))
    panels: dict[str, pd.DataFrame] = {}
    for number, symbol in enumerate(SYMBOLS):
        pressure = np.zeros(len(index))
        pressure[800::25] = 2.5 + 0.1 * number
        panel = pd.DataFrame(index=index)
        panel["open"] = base_price * (1.0 + number * 0.01)
        panel["close"] = panel.open * 1.00001
        panel["pressure_z"] = pressure
        panel["depth_z"] = -2.0
        panel["price_move"] = 0.0
        panel["bid_replenishment"] = 0.2
        panel["ask_replenishment"] = 0.2
        panel["quality"] = True
        panels[symbol] = panel
    policy = next(
        policy
        for policy in POLICIES
        if policy.family == "imbalance_continuation"
        and policy.threshold == 2.0
        and policy.persistence == 1
        and policy.hold_minutes == 3
    )
    base_audit = next(audit for audit in AUDITS if audit.name == "base")
    account, trades = simulate(panels, policy, base_audit)
    assert not trades.empty
    assert metrics(account, trades)["trade_count"] > 10
    assert float(account.equity.min()) > 0

    changed = {name: frame.copy() for name, frame in panels.items()}
    before = policy_signal(panels[SYMBOLS[0]], policy).direction.iloc[:-1]
    changed[SYMBOLS[0]].iloc[-1, changed[SYMBOLS[0]].columns.get_loc("pressure_z")] = -100.0
    after = policy_signal(changed[SYMBOLS[0]], policy).direction.iloc[:-1]
    pd.testing.assert_series_equal(before, after)
    print("V198-V204 self-test passed")


def development_panels(panels: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    start = pd.Timestamp(START, tz="UTC")
    end = pd.Timestamp(DEVELOPMENT_END, tz="UTC")
    return {symbol: panel[(panel.index >= start) & (panel.index <= end)].copy() for symbol, panel in panels.items()}


def rank_policies(
    panels: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    base_audit = next(audit for audit in AUDITS if audit.name == "base")
    dev = development_panels(panels)
    rows: list[dict[str, Any]] = []
    for number, policy in enumerate(POLICIES, start=1):
        account, trades = simulate(dev, policy, base_audit)
        values = metrics(account, trades)
        preliminary = bool(
            values["cagr"] >= DEVELOPMENT_GATES["cagr_min"]
            and values["sharpe"] >= DEVELOPMENT_GATES["sharpe_min"]
            and values["max_drawdown"] >= DEVELOPMENT_GATES["max_drawdown_min"]
            and values["trade_count"] >= DEVELOPMENT_GATES["closed_trades_min"]
            and values["annual_turnover"] <= DEVELOPMENT_GATES["annual_turnover_max"]
        )
        btc_return = np.nan
        eth_return = np.nan
        if preliminary and policy.family != "false_pressure_control":
            btc_account, btc_trades = simulate({SYMBOLS[0]: dev[SYMBOLS[0]]}, policy, base_audit)
            eth_account, eth_trades = simulate({SYMBOLS[1]: dev[SYMBOLS[1]]}, policy, base_audit)
            btc_return = float(metrics(btc_account, btc_trades)["total_return"])
            eth_return = float(metrics(eth_account, eth_trades)["total_return"])
        eligible = bool(
            preliminary
            and policy.family != "false_pressure_control"
            and np.isfinite(btc_return)
            and np.isfinite(eth_return)
            and btc_return > 0.0
            and eth_return > 0.0
            and values["unexplained_events"] == 0
        )
        score = (
            float(values["cagr"])
            + 0.05 * float(values["sharpe"])
            + 0.10 * float(values["max_drawdown"])
            - 0.0001 * float(values["annual_turnover"])
        )
        rows.append(
            {
                "policy": policy.name,
                **asdict(policy),
                "eligible_development": eligible,
                "preliminary_gate": preliminary,
                "btc_development_return": btc_return,
                "eth_development_return": eth_return,
                "score": score,
                **{f"development_{key}": value for key, value in values.items()},
            }
        )
        if number % 10 == 0:
            print(f"ranked {number}/{len(POLICIES)} policies", flush=True)
    return pd.DataFrame(rows).sort_values(
        ["eligible_development", "score"], ascending=[False, False]
    )


def no_candidate_outputs(
    root: Path,
    results: Path,
    gate: dict[str, Any],
    provenance: dict[str, Any],
    quality: pd.DataFrame,
    ranking: pd.DataFrame,
    proof: dict[str, Any],
    atlas: Path,
) -> None:
    quality.to_csv(results / "data_quality.csv", index=False)
    (results / "coverage_gate.json").write_text(json.dumps(gate, indent=2) + "\n")
    (results / "provenance.json").write_text(
        json.dumps(provenance, indent=2, default=float) + "\n"
    )
    ranking.to_csv(results / "selection_ranking_development.csv", index=False)
    pd.DataFrame().to_csv(results / "selected_trades.csv", index=False)
    pd.DataFrame().to_csv(results / "audit_metrics.csv", index=False)
    empty_annual(atlas, results)
    decision = {
        "candidate": CANDIDATE,
        "status": "rejected_before_validation",
        "data_coverage_passed": True,
        "eligible_policy_count": 0,
        "standalone_selection_passed": False,
        "integration_permitted": False,
        "promoted_candidates": [],
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    diagnostic = ranking.head(10).to_dict(orient="records")
    summary = {
        **decision,
        "selection": proof,
        "development_diagnostics": diagnostic,
        "coverage_gate": gate,
        "provenance_sha256": canonical_hash(provenance),
        "limitations": [
            "Proxy execution uses next-minute mark open plus frozen round-trip cost floors, not historical BBO fills.",
            "Validation, holdout and final P&L were not opened because no development policy passed.",
            "No live trading or real leverage is authorized.",
        ],
    }
    (results / "FROZEN_DECISION.json").write_text(
        json.dumps(decision, indent=2) + "\n"
    )
    (results / "summary.json").write_text(
        json.dumps(summary, indent=2, default=float) + "\n"
    )
    report = [
        "# V198–V204 actual depth research",
        "",
        "Status: `rejected_before_validation`.",
        "",
        f"Frozen policies: {len(POLICIES)}; eligible in development: 0.",
        "",
        "Validation, 2025 holdout and 2026 H1 final were not opened.",
        "",
        "`live_ready=false`; `real_leverage_authorized=false`; `profitability_proven=false`.",
    ]
    (results / "REPORT_RU.md").write_text("\n".join(report) + "\n")
    write_manifest(root)


def integration_metrics(
    atlas_path: Path,
    account: pd.DataFrame,
) -> list[dict[str, Any]]:
    atlas = pd.read_csv(atlas_path, index_col=0, parse_dates=True)
    atlas.index = pd.to_datetime(atlas.index, utc=True)
    atlas_daily = atlas.equity.resample("1D").last().ffill()
    sleeve_daily = account.equity.resample("1D").last().ffill()
    joined = pd.concat([atlas_daily, sleeve_daily], axis=1, join="inner")
    joined.columns = ["atlas", "sleeve"]
    atlas_return = joined.atlas.pct_change().fillna(0.0)
    sleeve_return = joined.sleeve.pct_change().fillna(0.0)
    rows: list[dict[str, Any]] = []
    for weight in (0.05, 0.10, 0.15):
        equity = INITIAL_EQUITY * (
            1.0 + (1.0 - weight) * atlas_return + weight * sleeve_return
        ).cumprod()
        output = pd.DataFrame(
            {
                "equity": equity,
                "turnover": 0.0,
                "costs": 0.0,
                "trade_events": 0,
                "unexplained_events": 0,
                "gross": 0.0,
            }
        )
        rows.append({"weight": weight, **metrics(output)})
    return rows


def run(args: argparse.Namespace) -> int:
    root = args.root
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
    panels, provenance, quality, gate = load_all(args.cache)
    quality.to_csv(results / "data_quality.csv", index=False)
    (results / "coverage_gate.json").write_text(json.dumps(gate, indent=2) + "\n")
    (results / "provenance.json").write_text(
        json.dumps(provenance, indent=2, default=float) + "\n"
    )
    if not gate["passed"]:
        failure_outputs(root, results, gate, provenance, quality, args.atlas)
        print(json.dumps({"status": "data_access_insufficient", "gate": gate}, indent=2))
        return 0

    ranking = rank_policies(panels)
    ranking.to_csv(results / "selection_ranking_development.csv", index=False)
    eligible = ranking[ranking.eligible_development]
    selected_name = str(eligible.iloc[0].policy) if not eligible.empty else None
    proof = {
        "candidate": CANDIDATE,
        "selection_cutoff": "2024-06-30T23:59:59Z",
        "selection_uses_validation": False,
        "selection_uses_2025": False,
        "selection_uses_2026": False,
        "program_level_holdout_pristine": False,
        "policy_count": len(POLICIES),
        "eligible_policy_count": int(ranking.eligible_development.sum()),
        "selected": policy_dict(policy_by_name(selected_name)) if selected_name else None,
        "development_gates": DEVELOPMENT_GATES,
        "post_selection_gates": POST_SELECTION_GATES,
        "ranking_sha256": hashlib.sha256(
            ranking.to_csv(index=False).encode("utf-8")
        ).hexdigest(),
        "design_sha256": sha256_file(root / "V197_V204_DESIGN.json"),
        "config_sha256": sha256_file(root / "V198_V204_CONFIG.json"),
        "coverage_gate": gate,
    }
    proof["selection_proof_sha256"] = canonical_hash(proof)
    (results / "selection_proof_before_validation.json").write_text(
        json.dumps(proof, indent=2, default=float) + "\n"
    )
    if selected_name is None:
        no_candidate_outputs(
            root,
            results,
            gate,
            provenance,
            quality,
            ranking,
            proof,
            args.atlas,
        )
        print(json.dumps({"status": "rejected_before_validation", "proof": proof}, indent=2))
        return 0

    selected = policy_by_name(selected_name)
    audit_rows: list[dict[str, Any]] = []
    audit_accounts: dict[str, pd.DataFrame] = {}
    audit_trades: dict[str, pd.DataFrame] = {}
    for audit in AUDITS:
        account, trades = simulate(panels, selected, audit)
        audit_accounts[audit.name] = account
        audit_trades[audit.name] = trades
        full = metrics(account, trades)
        development = period_metrics(account, trades, START, DEVELOPMENT_END)
        validation = period_metrics(account, trades, VALIDATION_START, VALIDATION_END)
        holdout = period_metrics(account, trades, HOLDOUT_START, HOLDOUT_END)
        final = period_metrics(account, trades, FINAL_START, END)
        audit_rows.append(
            {
                "audit": audit.name,
                **asdict(audit),
                **full,
                "development_return": development["total_return"],
                "development_cagr": development["cagr"],
                "development_sharpe": development["sharpe"],
                "development_max_drawdown": development["max_drawdown"],
                "validation_return": validation["total_return"],
                "holdout_return": holdout["total_return"],
                "final_return": final["total_return"],
            }
        )
    audit_frame = pd.DataFrame(audit_rows)
    audit_frame.to_csv(results / "audit_metrics.csv", index=False)

    base_account = audit_accounts["base"]
    base_trades = audit_trades["base"]
    base_trades.to_csv(results / "selected_trades.csv", index=False)
    base_account.equity.resample("1D").last().ffill().rename("equity").to_csv(
        results / "candidate_daily_equity.csv"
    )

    full = metrics(base_account, base_trades)
    development = period_metrics(base_account, base_trades, START, DEVELOPMENT_END)
    validation = period_metrics(base_account, base_trades, VALIDATION_START, VALIDATION_END)
    holdout = period_metrics(base_account, base_trades, HOLDOUT_START, HOLDOUT_END)
    final = period_metrics(base_account, base_trades, FINAL_START, END)
    severe = metrics(audit_accounts["severe"], audit_trades["severe"])
    extreme = metrics(audit_accounts["extreme"], audit_trades["extreme"])
    latency = metrics(audit_accounts["delay_1m"], audit_trades["delay_1m"])
    concentration = concentration_metrics(base_account)
    bootstrap = trade_bootstrap(base_trades)

    base_audit = next(audit for audit in AUDITS if audit.name == "base")
    asset_metrics: dict[str, Any] = {}
    for symbol in SYMBOLS:
        account, trades = simulate({symbol: panels[symbol]}, selected, base_audit)
        asset_metrics[symbol] = {
            "full": metrics(account, trades),
            "development": period_metrics(account, trades, START, DEVELOPMENT_END),
            "validation": period_metrics(account, trades, VALIDATION_START, VALIDATION_END),
            "holdout": period_metrics(account, trades, HOLDOUT_START, HOLDOUT_END),
            "final": period_metrics(account, trades, FINAL_START, END),
        }

    checks = {
        "eligible_development": True,
        "development_cagr": development["cagr"] >= DEVELOPMENT_GATES["cagr_min"],
        "development_sharpe": development["sharpe"] >= DEVELOPMENT_GATES["sharpe_min"],
        "development_max_drawdown": development["max_drawdown"]
        >= DEVELOPMENT_GATES["max_drawdown_min"],
        "development_closed_trades": development["trade_count"]
        >= DEVELOPMENT_GATES["closed_trades_min"],
        "btc_return_positive": asset_metrics[SYMBOLS[0]]["full"]["total_return"] > 0.0,
        "eth_return_positive": asset_metrics[SYMBOLS[1]]["full"]["total_return"] > 0.0,
        "validation_return_positive": validation["total_return"] > 0.0,
        "holdout_return_positive": holdout["total_return"] > 0.0,
        "final_return_positive": final["total_return"] > 0.0,
        "severe_full_cagr_positive": severe["cagr"] > 0.0,
        "latency_full_cagr_positive": latency["cagr"] > 0.0,
        "worst_calendar_quarter": concentration["worst_calendar_quarter"]
        >= POST_SELECTION_GATES["worst_calendar_quarter_min"],
        "top_month_positive_pnl_share": concentration["top_month_positive_pnl_share"]
        <= POST_SELECTION_GATES["top_month_positive_pnl_share_max"],
        "zero_unexplained_book_events": all(
            int(value) == 0 for value in audit_frame.unexplained_events
        ),
        "data_coverage": gate["passed"],
    }
    standalone_passed = all(checks.values())
    integration = {
        "permitted": standalone_passed,
        "tested": False,
        "reason": None if standalone_passed else "standalone gates failed",
    }
    if standalone_passed:
        integration = {
            "permitted": True,
            "tested": True,
            "weights": integration_metrics(args.atlas, base_account),
        }

    candidate_yearly = calendar_returns(base_account, "year").rename(
        columns={"return": "V198_depth"}
    )
    annual = v75_yearly(args.atlas).merge(candidate_yearly, on="year", how="outer")
    annual.sort_values("year").to_csv(results / "ANNUAL_RETURNS.csv", index=False)
    calendar_returns(base_account, "month").to_csv(
        results / "candidate_monthly_returns.csv", index=False
    )
    calendar_returns(base_account, "quarter").to_csv(
        results / "candidate_quarterly_returns.csv", index=False
    )

    status = (
        "frozen_historical_candidate_needs_forward"
        if standalone_passed
        else "rejected_or_needs_iteration"
    )
    decision = {
        "candidate": CANDIDATE,
        "selected_policy": selected.name,
        "status": status,
        "data_coverage_passed": True,
        "standalone_selection_passed": standalone_passed,
        "integration_permitted": integration["permitted"],
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
        "integration": integration,
        "candidate_full": full,
        "candidate_development": development,
        "candidate_validation_2024h2": validation,
        "candidate_holdout_2025": holdout,
        "candidate_final_2026h1": final,
        "asset_metrics": asset_metrics,
        "audit_metrics": audit_frame.to_dict(orient="records"),
        "extreme_full": extreme,
        "concentration": concentration,
        "bootstrap": bootstrap,
        "provenance_sha256": canonical_hash(provenance),
        "limitations": [
            "BookDepth observations are exchange depth aggregates at signed percentage buckets, not full L2 reconstruction.",
            "Proxy execution uses next-minute mark open plus frozen cost floors because continuous bookTicker history is unavailable.",
            "The result is historical research evidence only and cannot establish executable live performance.",
            "Program-level 2025-2026 holdout is not pristine.",
        ],
    }
    (results / "FROZEN_DECISION.json").write_text(
        json.dumps(decision, indent=2) + "\n"
    )
    (results / "summary.json").write_text(
        json.dumps(summary, indent=2, default=float) + "\n"
    )

    report = [
        "# V198–V204 actual depth research",
        "",
        f"Status: `{status}`.",
        "",
        f"Selected policy: `{selected.name}`.",
        "",
        "| Metric | Full | Development | Validation 2024 H2 | Holdout 2025 | Final 2026 H1 |",
        "|---|---:|---:|---:|---:|---:|",
        f"| Total return | {pct(full['total_return'])} | {pct(development['total_return'])} | {pct(validation['total_return'])} | {pct(holdout['total_return'])} | {pct(final['total_return'])} |",
        f"| CAGR | {pct(full['cagr'])} | {pct(development['cagr'])} | — | — | — |",
        f"| Max DD | {pct(full['max_drawdown'])} | {pct(development['max_drawdown'])} | {pct(validation['max_drawdown'])} | {pct(holdout['max_drawdown'])} | {pct(final['max_drawdown'])} |",
        f"| Sharpe | {full['sharpe']:.3f} | {development['sharpe']:.3f} | {validation['sharpe']:.3f} | {holdout['sharpe']:.3f} | {final['sharpe']:.3f} |",
        "",
        "Proxy execution is not live-execution proof. V75 remains the mandatory control.",
        "",
        "`live_ready=false`; `real_leverage_authorized=false`; `profitability_proven=false`.",
    ]
    (results / "REPORT_RU.md").write_text("\n".join(report) + "\n")
    write_manifest(root)
    print(json.dumps(summary, indent=2, default=float))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--cache", type=Path, default=Path(".cache/v198_depth"))
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
