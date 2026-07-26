#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from config import AUDITS, INTEGRATION_WEIGHTS, PERIODS, STANDALONE_GATES, VXM_AVAILABLE_FROM
from data import load_or_collect
from dates import self_test as dates_self_test
from engine import annual_returns, combine_separate_accounts, metrics, period, simulate
from features import build_market, policy_target, self_test as features_self_test
from selection import (
    best_year_share,
    ensemble_target,
    evaluate_prefinal,
    post_selection_checks,
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_atlas(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    frame.index = pd.to_datetime(frame.index).tz_localize(None)
    frame = frame.sort_index()
    if "equity" not in frame:
        raise ValueError(f"missing equity in {path}")
    result = pd.DataFrame(index=frame.index)
    result["equity"] = pd.to_numeric(frame["equity"], errors="coerce")
    result = result.dropna()
    result["daily_return"] = result["equity"].pct_change().fillna(
        result["equity"].iloc[0] / 10_000.0 - 1.0
    )
    return result


def atlas_annual(frame: pd.DataFrame) -> pd.DataFrame:
    return annual_returns(frame, "V75_original")


def vxm_feasibility(target: pd.DataFrame, account: pd.DataFrame, market) -> dict[str, float]:
    start = pd.Timestamp(VXM_AVAILABLE_FROM)
    dates = target.index[target.index >= start]
    active = target.loc[dates].abs().sum(axis=1) > 1e-12
    dates = dates[active]
    if len(dates) == 0:
        return {
            "active_days": 0,
            "fraction_days_one_contract_feasible": 0.0,
            "median_fractional_contracts": 0.0,
            "minimum_one_contract_weight": 0.0,
        }
    values = []
    weights = []
    for day in dates:
        price = float(market.features.at[day, "front_settle"])
        equity = float(account.at[day, "equity"])
        gross = float(target.loc[day].abs().sum())
        one_contract_notional = price * 100.0
        values.append(gross * equity / max(one_contract_notional, 1e-12))
        weights.append(one_contract_notional / max(equity, 1e-12))
    series = pd.Series(values)
    return {
        "active_days": int(len(series)),
        "fraction_days_one_contract_feasible": float((series >= 1.0).mean()),
        "median_fractional_contracts": float(series.median()),
        "minimum_one_contract_weight": float(min(weights)),
        "median_one_contract_weight": float(np.median(weights)),
    }


def block_bootstrap(returns: pd.Series, output: Path, seed: int = 147154) -> pd.DataFrame:
    values = returns.dropna().to_numpy(float)
    rng = np.random.default_rng(seed)
    rows = []
    if len(values) < 252:
        frame = pd.DataFrame(rows)
        frame.to_csv(output, index=False)
        return frame
    for block in (10, 21, 63):
        for horizon in (252, 504):
            totals, drawdowns = [], []
            for _ in range(3000):
                path: list[float] = []
                while len(path) < horizon:
                    start = int(rng.integers(0, max(1, len(values) - block + 1)))
                    path.extend(values[start : start + block])
                sample = np.asarray(path[:horizon])
                equity = np.cumprod(1.0 + sample)
                totals.append(float(equity[-1] - 1.0))
                drawdowns.append(float(np.min(equity / np.maximum.accumulate(equity) - 1.0)))
            rows.append(
                {
                    "block_days": block,
                    "horizon_days": horizon,
                    "median_return": float(np.median(totals)),
                    "p05_return": float(np.quantile(totals, 0.05)),
                    "probability_positive": float(np.mean(np.asarray(totals) > 0)),
                    "median_max_drawdown": float(np.median(drawdowns)),
                    "p05_max_drawdown": float(np.quantile(drawdowns, 0.05)),
                }
            )
    frame = pd.DataFrame(rows)
    frame.to_csv(output, index=False)
    return frame


def data_quality(contracts: pd.DataFrame, spot: pd.DataFrame, output: Path) -> dict:
    duplicate_count = int(contracts.duplicated(["Trade Date", "Expiry"]).sum())
    quality = {
        "contract_rows": int(len(contracts)),
        "unique_expiries": int(contracts["Expiry"].nunique()),
        "trade_date_min": contracts["Trade Date"].min().date().isoformat(),
        "trade_date_max": contracts["Trade Date"].max().date().isoformat(),
        "duplicate_trade_date_expiry": duplicate_count,
        "nonpositive_settle": int((contracts["Settle"] <= 0).sum()),
        "spot_rows": int(len(spot)),
        "spot_date_min": spot["DATE"].min().date().isoformat(),
        "spot_date_max": spot["DATE"].max().date().isoformat(),
    }
    pd.DataFrame([quality]).to_csv(output, index=False)
    return quality


def choose_integration(atlas: pd.DataFrame, sleeve: pd.DataFrame, output: Path) -> tuple[float | None, pd.DataFrame]:
    rows = []
    for weight in INTEGRATION_WEIGHTS:
        combined = combine_separate_accounts(atlas, sleeve, weight)
        selection = period(combined, "2021-01-01", "2024-01-01")
        rows.append(
            {
                "sleeve_weight": weight,
                **selection,
                "score": selection["sharpe"] + selection["max_drawdown"] + 0.5 * selection["annualized_return"],
            }
        )
    frame = pd.DataFrame(rows).sort_values("score", ascending=False)
    frame.to_csv(output, index=False)
    return (float(frame.iloc[0]["sleeve_weight"]) if not frame.empty else None), frame


def build_report(summary: dict, annual: pd.DataFrame) -> str:
    original = summary["original_v75"]
    selected = summary["selected_sleeve"]
    lines = [
        "# Active V147–V154 — dated VIX futures",
        "",
        f"Status: `{summary['status']}`",
        "",
        "V147 зафиксировал блокировку официального CME источника из публичного runner. V148 подтвердил доступ к официальным датированным Cboe VX-файлам. V149–V154 используют отдельные месячные контракты, явный roll, next-open execution, расходы и margin audit.",
        "",
        "## Основные метрики",
        "",
        "| Candidate | CAGR | Total return | Max DD | Sharpe | Turnover |",
        "|---|---:|---:|---:|---:|---:|",
        f"| V75 original | {original['annualized_return']:.2%} | {original['total_return']:.2%} | {original['max_drawdown']:.2%} | {original['sharpe']:.3f} | — |",
        f"| V154 dated VX sleeve | {selected['full']['annualized_return']:.2%} | {selected['full']['total_return']:.2%} | {selected['full']['max_drawdown']:.2%} | {selected['full']['sharpe']:.3f} | {selected['full']['annual_turnover']:.2f}x |",
        "",
        "## Решение",
        "",
        f"- eligible policies before 2021: `{summary['selection']['eligible_policy_count']}`;",
        f"- standalone passed: `{summary['standalone_selection_passed']}`;",
        f"- integration permitted: `{summary['integration_permitted']}`;",
        f"- promoted candidates: `{summary['promoted_candidates']}`;",
        "- `live_ready=false`;",
        "- `real_leverage_authorized=false`.",
        "",
        "## Годовая доходность",
        "",
        "| Год | V75 original | V154 dated VX sleeve |",
        "|---:|---:|---:|",
    ]
    for _, row in annual.iterrows():
        year = int(row["year"])
        left = "—" if pd.isna(row.get("V75_original")) else f"{row['V75_original']:+.2%}"
        right = "—" if pd.isna(row.get("V154_dated_VX")) else f"{row['V154_dated_VX']:+.2%}"
        lines.append(f"| {year} | {left} | {right} |")
    lines += [
        "",
        "## Evidence limits",
        "",
        "- Official Cboe contract files are settlement/history data, not a broker fill feed.",
        "- Bid/ask is modeled through explicit cost scenarios; historical order-book depth is unavailable.",
        "- Pre-2020 small-account execution uses normalized VX economics; integer VXM feasibility is audited only after VXM launch.",
        "- Program-level pristine holdout is absent. No live trading or real leverage is authorized.",
    ]
    return "\n".join(lines) + "\n"


def self_test() -> None:
    dates_self_test()
    features_self_test()
    print("V147-V154 self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=HERE.parent)
    parser.add_argument("--cache", type=Path, default=Path(".cache/v147_v154"))
    parser.add_argument("--atlas", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.atlas is None or not args.atlas.exists():
        raise SystemExit("--atlas V75 equity CSV is required")

    root = args.root.resolve()
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
    contracts, spot = load_or_collect(args.cache, root)
    quality = data_quality(contracts, spot, results / "data_quality.csv")
    market = build_market(contracts, spot)
    market.features.to_csv(root / "inputs" / "processed" / "vx_term_structure_daily.csv")

    selected, ranking = evaluate_prefinal(market, results)
    target = ensemble_target(selected, market)
    target.to_csv(results / "selected_target_weights.csv")

    audit_metrics = []
    accounts = {}
    for audit in AUDITS:
        account = simulate(market, target, audit)
        accounts[audit.name] = account
        account.to_csv(results / f"v154_equity_{audit.name}.csv")
        for period_name, bounds in PERIODS.items():
            audit_metrics.append(
                {"audit": audit.name, "period": period_name, **period(account, *bounds)}
            )
        audit_metrics.append({"audit": audit.name, "period": "full", **metrics(account)})
    pd.DataFrame(audit_metrics).to_csv(results / "audit_metrics.csv", index=False)

    base = accounts["base"]
    stress = accounts["stress"]
    eligible_count = int(ranking["eligible_before_2021"].sum())
    post = post_selection_checks(base)
    stress_post = post_selection_checks(stress)
    standalone_pass = bool(eligible_count > 0 and all(post.values()) and all(stress_post.values()))

    atlas = load_atlas(args.atlas)
    atlas_full = metrics(atlas)
    atlas_checkpoint = root.parents[1] / "docs" / "checkpoints" / "v138" / "CHECKPOINT_V138.json"
    if atlas_checkpoint.exists():
        atlas_canonical = json.loads(atlas_checkpoint.read_text()).get("original_v75", {})
        atlas_full.update(
            {
                "total_return": atlas_canonical.get("total_return", atlas_full["total_return"]),
                "annualized_return": atlas_canonical.get("cagr", atlas_full["annualized_return"]),
                "max_drawdown": atlas_canonical.get("max_drawdown", atlas_full["max_drawdown"]),
                "sharpe": atlas_canonical.get("sharpe", atlas_full["sharpe"]),
            }
        )

    integration_permitted = standalone_pass
    selected_weight = None
    integration = None
    integration_checks = {}
    if integration_permitted:
        selected_weight, _ = choose_integration(
            atlas, base, results / "integration_weight_selection_2021_2023.csv"
        )
        if selected_weight is not None:
            integration = combine_separate_accounts(atlas, base, selected_weight)
            integration.to_csv(results / "v153_integrated_equity.csv")
            holdout = period(integration, "2024-01-01", "2026-01-01")
            final = period(integration, "2026-01-01", "2026-07-01")
            aligned_atlas = atlas.reindex(integration.index).dropna()
            integration_checks = {
                "holdout_positive": holdout["total_return"] > 0,
                "final_positive": final["total_return"] > 0,
                "full_drawdown_not_worse": metrics(integration)["max_drawdown"] >= metrics(aligned_atlas)["max_drawdown"] - 0.01,
            }
    integration_pass = bool(integration_checks and all(integration_checks.values()))

    annual = atlas_annual(atlas).merge(
        annual_returns(base, "V154_dated_VX"), on="year", how="outer"
    ).sort_values("year")
    if integration is not None:
        annual = annual.merge(
            annual_returns(integration, "V153_integrated"), on="year", how="outer"
        )
    annual.to_csv(results / "ANNUAL_RETURNS.csv", index=False)
    block_bootstrap(base["daily_return"], results / "block_bootstrap.csv")
    feasibility = vxm_feasibility(target, base, market)

    selected_full = metrics(base)
    summary = {
        "candidate": "ACTIVE_V147_V154_DATED_VIX_FUTURES",
        "status": (
            "historical_candidate_needs_execution_forward"
            if standalone_pass
            else "rejected_or_needs_iteration"
        ),
        "live_ready": False,
        "real_leverage_authorized": False,
        "original_v75": atlas_full,
        "selection": {
            "selection_cutoff": "2020-12-31",
            "selection_uses_2021_or_later": False,
            "policy_count": len(ranking),
            "eligible_policy_count": eligible_count,
            "selected": [asdict(policy) for policy in selected],
            "selection_proof_sha256": sha256_file(results / "selection_proof_before_2021.json"),
        },
        "selected_sleeve": {
            "full": selected_full,
            "prefinal_2006_2020": period(base, "2006-01-01", "2021-01-01"),
            "holdout_2021_2023": period(base, "2021-01-01", "2024-01-01"),
            "holdout_2024_2025": period(base, "2024-01-01", "2026-01-01"),
            "final_2026h1": period(base, "2026-01-01", "2026-07-01"),
            "best_year_positive_log_share": best_year_share(base),
            "post_selection_checks": post,
            "stress_post_selection_checks": stress_post,
            "vxm_integer_feasibility": feasibility,
        },
        "standalone_selection_passed": standalone_pass,
        "integration_permitted": integration_permitted,
        "integration_weight": selected_weight,
        "integration_checks": integration_checks,
        "integration_passed": integration_pass,
        "promoted_candidates": ["V154_dated_VX"] if standalone_pass else [],
        "data_quality": quality,
        "evidence_limits": {
            "official_dated_contract_files": True,
            "historical_bid_ask_available": False,
            "historical_broker_margin_available": False,
            "program_level_holdout_pristine": False,
        },
    }
    (results / "summary.json").write_text(json.dumps(summary, indent=2, default=float) + "\n")
    (results / "FROZEN_DECISION.json").write_text(json.dumps(summary, indent=2, default=float) + "\n")
    report = build_report(summary, annual)
    (results / "REPORT_RU.md").write_text(report)
    (root / "ANNUAL_RETURNS.csv").write_text((results / "ANNUAL_RETURNS.csv").read_text())
    print(json.dumps(summary, indent=2, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
