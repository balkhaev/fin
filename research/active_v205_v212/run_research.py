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

from flow_config import (
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
from flow_data import load_all
from flow_engine import (
    calendar_returns,
    concentration_metrics,
    metrics,
    period_metrics,
    policy_dict,
    policy_signal,
    simulate,
    trade_bootstrap,
)

CANDIDATE = "ACTIVE_V205_FLOW_DEPTH_INTERACTION"


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
    rows: list[dict[str, Any]] = []
    previous = INITIAL_EQUITY
    for year, part in frame.sort_index().groupby(frame.index.year):
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
        ):
            continue
        rel = str(path.relative_to(root))
        files[rel] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    (root / "MANIFEST.json").write_text(
        json.dumps({"candidate": CANDIDATE, "files": files}, indent=2) + "\n"
    )


def development_panels(panels: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    start = pd.Timestamp(START, tz="UTC")
    end = pd.Timestamp(DEVELOPMENT_END, tz="UTC")
    return {
        symbol: panel[(panel.index >= start) & (panel.index <= end)].copy()
        for symbol, panel in panels.items()
    }


def self_test() -> None:
    index = pd.date_range("2024-01-01", periods=3_000, freq="1min", tz="UTC")
    base_price = 100.0 * np.exp(np.cumsum(np.full(len(index), 0.00001)))
    panels: dict[str, pd.DataFrame] = {}
    for number, symbol in enumerate(SYMBOLS):
        panel = pd.DataFrame(index=index)
        panel["open"] = base_price * (1.0 + number * 0.01)
        panel["close"] = panel.open * 1.00001
        panel["flow_z"] = 0.0
        panel["pressure_z"] = 0.0
        panel["depth_z"] = -2.0
        panel["volume_z"] = 1.5
        panel["impact_bps"] = 1.0
        panel["quality"] = True
        panel.loc[index[800::25], "flow_z"] = 2.5
        panel.loc[index[800::25], "pressure_z"] = 2.0
        panels[symbol] = panel
    policy = next(
        item
        for item in POLICIES
        if item.family == "agreement_continuation"
        and item.flow_threshold == 2.0
        and item.pressure_threshold == 1.5
        and item.persistence == 1
        and item.hold_minutes == 3
    )
    audit = next(item for item in AUDITS if item.name == "base")
    account, trades = simulate(panels, policy, audit)
    assert not trades.empty
    assert metrics(account, trades)["trade_count"] > 10
    changed = panels[SYMBOLS[0]].copy()
    before = policy_signal(panels[SYMBOLS[0]], policy).direction.iloc[:-1]
    changed.iloc[-1, changed.columns.get_loc("flow_z")] = -100.0
    after = policy_signal(changed, policy).direction.iloc[:-1]
    pd.testing.assert_series_equal(before, after)
    print("V205-V212 self-test passed")


def rank_policies(panels: dict[str, pd.DataFrame]) -> pd.DataFrame:
    dev = development_panels(panels)
    audit = next(item for item in AUDITS if item.name == "base")
    rows: list[dict[str, Any]] = []
    for number, policy in enumerate(POLICIES, start=1):
        account, trades = simulate(dev, policy, audit)
        value = metrics(account, trades)
        preliminary = bool(
            value["cagr"] >= DEVELOPMENT_GATES["cagr_min"]
            and value["sharpe"] >= DEVELOPMENT_GATES["sharpe_min"]
            and value["max_drawdown"] >= DEVELOPMENT_GATES["max_drawdown_min"]
            and value["trade_count"] >= DEVELOPMENT_GATES["closed_trades_min"]
            and value["annual_turnover"] <= DEVELOPMENT_GATES["annual_turnover_max"]
        )
        btc_return = np.nan
        eth_return = np.nan
        if preliminary and policy.family != "reversed_agreement_control":
            btc_account, btc_trades = simulate({SYMBOLS[0]: dev[SYMBOLS[0]]}, policy, audit)
            eth_account, eth_trades = simulate({SYMBOLS[1]: dev[SYMBOLS[1]]}, policy, audit)
            btc_return = float(metrics(btc_account, btc_trades)["total_return"])
            eth_return = float(metrics(eth_account, eth_trades)["total_return"])
        eligible = bool(
            preliminary
            and policy.family != "reversed_agreement_control"
            and np.isfinite(btc_return)
            and np.isfinite(eth_return)
            and btc_return > 0.0
            and eth_return > 0.0
            and value["unexplained_events"] == 0
        )
        score = (
            float(value["cagr"])
            + 0.05 * float(value["sharpe"])
            + 0.10 * float(value["max_drawdown"])
            - 0.0001 * float(value["annual_turnover"])
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
                **{f"development_{key}": item for key, item in value.items()},
            }
        )
        if number % 10 == 0:
            print(f"ranked {number}/{len(POLICIES)} policies", flush=True)
    return pd.DataFrame(rows).sort_values(
        ["eligible_development", "score"], ascending=[False, False]
    )


def common_files(
    results: Path,
    gate: dict[str, Any],
    provenance: dict[str, Any],
    quality: pd.DataFrame,
) -> None:
    quality.to_csv(results / "data_quality.csv", index=False)
    (results / "coverage_gate.json").write_text(json.dumps(gate, indent=2) + "\n")
    (results / "provenance.json").write_text(
        json.dumps(provenance, indent=2, default=float) + "\n"
    )


def fail_without_selection(
    root: Path,
    results: Path,
    atlas: Path,
    gate: dict[str, Any],
    provenance: dict[str, Any],
    quality: pd.DataFrame,
) -> None:
    common_files(results, gate, provenance, quality)
    pd.DataFrame().to_csv(results / "selection_ranking_development.csv", index=False)
    pd.DataFrame().to_csv(results / "selected_trades.csv", index=False)
    pd.DataFrame().to_csv(results / "audit_metrics.csv", index=False)
    v75_yearly(atlas).to_csv(results / "ANNUAL_RETURNS.csv", index=False)
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
    }
    (results / "FROZEN_DECISION.json").write_text(json.dumps(decision, indent=2) + "\n")
    (results / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (results / "REPORT_RU.md").write_text(
        "# V205–V212 flow-depth research\n\nStatus: `data_access_insufficient`.\n\n"
        "No policy P&L was calculated.\n"
    )
    write_manifest(root)


def reject_before_oos(
    root: Path,
    results: Path,
    atlas: Path,
    gate: dict[str, Any],
    provenance: dict[str, Any],
    quality: pd.DataFrame,
    ranking: pd.DataFrame,
    proof: dict[str, Any],
) -> None:
    common_files(results, gate, provenance, quality)
    ranking.to_csv(results / "selection_ranking_development.csv", index=False)
    pd.DataFrame().to_csv(results / "selected_trades.csv", index=False)
    pd.DataFrame().to_csv(results / "audit_metrics.csv", index=False)
    v75_yearly(atlas).to_csv(results / "ANNUAL_RETURNS.csv", index=False)
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
        "development_diagnostics": ranking.head(10).to_dict(orient="records"),
        "provenance_sha256": canonical_hash(provenance),
        "limitations": [
            "Validation and later periods were not opened because no development policy passed.",
            "Execution is a next-minute mark-open proxy plus frozen cost floors, not BBO proof.",
        ],
    }
    (results / "FROZEN_DECISION.json").write_text(json.dumps(decision, indent=2) + "\n")
    (results / "summary.json").write_text(
        json.dumps(summary, indent=2, default=float) + "\n"
    )
    (results / "REPORT_RU.md").write_text(
        f"# V205–V212 flow-depth research\n\nStatus: `rejected_before_validation`.\n\n"
        f"Frozen policies: {len(POLICIES)}; eligible in development: 0.\n\n"
        "Validation, 2025 holdout and 2026 H1 final were not opened.\n"
    )
    write_manifest(root)


def integration_metrics(atlas_path: Path, account: pd.DataFrame) -> list[dict[str, Any]]:
    atlas = pd.read_csv(atlas_path, index_col=0, parse_dates=True)
    atlas.index = pd.to_datetime(atlas.index, utc=True)
    joined = pd.concat(
        [
            atlas.equity.resample("1D").last().ffill(),
            account.equity.resample("1D").last().ffill(),
        ],
        axis=1,
        join="inner",
    )
    joined.columns = ["atlas", "sleeve"]
    ar = joined.atlas.pct_change().fillna(0.0)
    sr = joined.sleeve.pct_change().fillna(0.0)
    rows: list[dict[str, Any]] = []
    for weight in (0.05, 0.10, 0.15):
        equity = INITIAL_EQUITY * (1.0 + (1.0 - weight) * ar + weight * sr).cumprod()
        dummy = pd.DataFrame(
            {
                "equity": equity,
                "turnover": 0.0,
                "costs": 0.0,
                "trade_events": 0,
                "unexplained_events": 0,
                "gross": 0.0,
            }
        )
        rows.append({"weight": weight, **metrics(dummy)})
    return rows


def run(args: argparse.Namespace) -> int:
    root = args.root
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
    panels, provenance, quality, gate = load_all(args.cache)
    if not gate["passed"]:
        fail_without_selection(root, results, args.atlas, gate, provenance, quality)
        print(json.dumps({"status": "data_access_insufficient", "gate": gate}, indent=2))
        return 0

    ranking = rank_policies(panels)
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
        "ranking_sha256": hashlib.sha256(ranking.to_csv(index=False).encode("utf-8")).hexdigest(),
        "design_sha256": sha256_file(root / "V205_V212_DESIGN.json"),
        "coverage_gate": gate,
    }
    proof["selection_proof_sha256"] = canonical_hash(proof)
    (results / "selection_proof_before_validation.json").write_text(
        json.dumps(proof, indent=2, default=float) + "\n"
    )
    if selected_name is None:
        reject_before_oos(
            root,
            results,
            args.atlas,
            gate,
            provenance,
            quality,
            ranking,
            proof,
        )
        print(json.dumps({"status": "rejected_before_validation", "proof": proof}, indent=2))
        return 0

    selected = policy_by_name(selected_name)
    audit_rows: list[dict[str, Any]] = []
    accounts: dict[str, pd.DataFrame] = {}
    trades_by_audit: dict[str, pd.DataFrame] = {}
    for audit in AUDITS:
        account, trades = simulate(panels, selected, audit)
        accounts[audit.name] = account
        trades_by_audit[audit.name] = trades
        full = metrics(account, trades)
        dev = period_metrics(account, trades, START, DEVELOPMENT_END)
        validation = period_metrics(account, trades, VALIDATION_START, VALIDATION_END)
        holdout = period_metrics(account, trades, HOLDOUT_START, HOLDOUT_END)
        final = period_metrics(account, trades, FINAL_START, END)
        audit_rows.append(
            {
                "audit": audit.name,
                **asdict(audit),
                **full,
                "development_return": dev["total_return"],
                "development_cagr": dev["cagr"],
                "development_sharpe": dev["sharpe"],
                "development_max_drawdown": dev["max_drawdown"],
                "validation_return": validation["total_return"],
                "holdout_return": holdout["total_return"],
                "final_return": final["total_return"],
            }
        )
    audit_frame = pd.DataFrame(audit_rows)
    base_account = accounts["base"]
    base_trades = trades_by_audit["base"]
    full = metrics(base_account, base_trades)
    dev = period_metrics(base_account, base_trades, START, DEVELOPMENT_END)
    validation = period_metrics(base_account, base_trades, VALIDATION_START, VALIDATION_END)
    holdout = period_metrics(base_account, base_trades, HOLDOUT_START, HOLDOUT_END)
    final = period_metrics(base_account, base_trades, FINAL_START, END)
    concentration = concentration_metrics(base_account)
    severe = metrics(accounts["severe"], trades_by_audit["severe"])
    latency = metrics(accounts["delay_1m"], trades_by_audit["delay_1m"])
    asset_metrics: dict[str, Any] = {}
    base_audit = next(item for item in AUDITS if item.name == "base")
    for symbol in SYMBOLS:
        account, trades = simulate({symbol: panels[symbol]}, selected, base_audit)
        asset_metrics[symbol] = {
            "development": period_metrics(account, trades, START, DEVELOPMENT_END),
            "full": metrics(account, trades),
        }
    checks = {
        "eligible_development": True,
        "btc_development_positive": asset_metrics[SYMBOLS[0]]["development"]["total_return"] > 0,
        "eth_development_positive": asset_metrics[SYMBOLS[1]]["development"]["total_return"] > 0,
        "validation_return_positive": validation["total_return"] > 0,
        "holdout_return_positive": holdout["total_return"] > 0,
        "final_return_positive": final["total_return"] > 0,
        "severe_full_cagr_positive": severe["cagr"] > 0,
        "latency_full_cagr_positive": latency["cagr"] > 0,
        "worst_calendar_quarter": concentration["worst_calendar_quarter"] >= POST_SELECTION_GATES["worst_calendar_quarter_min"],
        "top_month_positive_pnl_share": concentration["top_month_positive_pnl_share"] <= POST_SELECTION_GATES["top_month_positive_pnl_share_max"],
        "zero_unexplained_events": bool((audit_frame.unexplained_events == 0).all()),
        "data_coverage": gate["passed"],
    }
    standalone_passed = all(checks.values())
    integration = {
        "permitted": standalone_passed,
        "tested": standalone_passed,
        "weights": integration_metrics(args.atlas, base_account) if standalone_passed else [],
        "reason": None if standalone_passed else "standalone gates failed",
    }
    yearly = calendar_returns(base_account, "year").rename(columns={"return": "V205_flow_depth"})
    v75_yearly(args.atlas).merge(yearly, on="year", how="outer").sort_values("year").to_csv(
        results / "ANNUAL_RETURNS.csv", index=False
    )
    common_files(results, gate, provenance, quality)
    ranking.to_csv(results / "selection_ranking_development.csv", index=False)
    audit_frame.to_csv(results / "audit_metrics.csv", index=False)
    base_trades.to_csv(results / "selected_trades.csv", index=False)
    calendar_returns(base_account, "month").to_csv(results / "monthly_returns.csv", index=False)
    status = "frozen_historical_candidate_needs_forward" if standalone_passed else "rejected_or_needs_iteration"
    decision = {
        "candidate": CANDIDATE,
        "selected_policy": selected.name,
        "status": status,
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
        "candidate_development": dev,
        "candidate_validation_2024h2": validation,
        "candidate_holdout_2025": holdout,
        "candidate_final_2026h1": final,
        "asset_metrics": asset_metrics,
        "audit_metrics": audit_frame.to_dict(orient="records"),
        "concentration": concentration,
        "bootstrap": trade_bootstrap(base_trades),
        "provenance_sha256": canonical_hash(provenance),
        "limitations": [
            "Execution uses next-minute mark open plus frozen cost floors, not historical BBO fills.",
            "BookDepth is aggregated at percentage buckets rather than full L2.",
            "Program-level holdout is not pristine.",
        ],
    }
    (results / "FROZEN_DECISION.json").write_text(json.dumps(decision, indent=2) + "\n")
    (results / "summary.json").write_text(json.dumps(summary, indent=2, default=float) + "\n")
    report = [
        "# V205–V212 actual flow-depth research",
        "",
        f"Status: `{status}`.",
        "",
        f"Selected policy: `{selected.name}`.",
        "",
        "| Metric | Full | Development | Validation | Holdout 2025 | Final 2026 H1 |",
        "|---|---:|---:|---:|---:|---:|",
        f"| Total return | {pct(full['total_return'])} | {pct(dev['total_return'])} | {pct(validation['total_return'])} | {pct(holdout['total_return'])} | {pct(final['total_return'])} |",
        f"| Max DD | {pct(full['max_drawdown'])} | {pct(dev['max_drawdown'])} | {pct(validation['max_drawdown'])} | {pct(holdout['max_drawdown'])} | {pct(final['max_drawdown'])} |",
        f"| Sharpe | {full['sharpe']:.3f} | {dev['sharpe']:.3f} | {validation['sharpe']:.3f} | {holdout['sharpe']:.3f} | {final['sharpe']:.3f} |",
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
    parser.add_argument("--cache", type=Path, default=Path(".cache/v205_flow_depth"))
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
