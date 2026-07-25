from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from allocators import (
    AllocationSpec,
    build_allocation,
    candidate_specs,
    spec_id,
)
from config import PERIODS, PROMOTION_GATES, SELECTION_GATES
from engine import align_accounts, load_account, simulate
from metrics import diagnostics, metrics, yearly


def utc(value):
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


def cut(account: pd.DataFrame, period: str) -> pd.DataFrame:
    start, end = PERIODS[period]
    return account.loc[(account.index >= utc(start)) & (account.index < utc(end))]


def safe_metrics(account: pd.DataFrame) -> dict[str, float]:
    if len(account) >= 20:
        return metrics(account)
    return {
        key: 0.0
        for key in (
            "total_return",
            "annualized_return",
            "annualized_volatility",
            "sharpe",
            "max_drawdown",
            "calmar",
            "annual_turnover",
            "average_gross",
            "max_gross",
            "average_scale",
            "max_scale",
            "final_equity",
        )
    }


def ensemble_accounts(accounts: list[pd.DataFrame]) -> pd.DataFrame:
    index = accounts[0].index
    for account in accounts[1:]:
        index = index.intersection(account.index)
    returns = [account.loc[index].equity.pct_change().fillna(0.0) for account in accounts]
    average_return = sum(returns) / len(returns)
    equity = 10000.0 * (1.0 + average_return).cumprod()
    return pd.DataFrame(
        {
            "equity": equity,
            "gross": sum(account.loc[index].gross for account in accounts) / len(accounts),
            "turnover": sum(account.loc[index].turnover for account in accounts) / len(accounts),
            "scale": sum(account.loc[index].scale for account in accounts) / len(accounts),
        },
        index=index,
    )


def paired_bootstrap(
    baseline_return: pd.Series,
    candidate_return: pd.Series,
    block: int,
    horizon: int,
    paths: int = 3000,
    seed: int = 119,
) -> dict[str, float]:
    joined = pd.concat([baseline_return, candidate_return], axis=1).dropna().to_numpy(float)
    rng = np.random.default_rng(seed + block + horizon)
    candidate_wins = 0
    candidate_lower_dd = 0
    candidate_positive = 0
    returns = []
    for _ in range(paths):
        pieces = []
        remaining = horizon
        while remaining > 0:
            length = min(block, remaining)
            start = int(rng.integers(0, max(1, len(joined) - length + 1)))
            pieces.append(joined[start : start + length])
            remaining -= length
        sample = np.vstack(pieces)[:horizon]
        baseline_equity = np.cumprod(1.0 + sample[:, 0])
        candidate_equity = np.cumprod(1.0 + sample[:, 1])
        baseline_total = baseline_equity[-1] - 1.0
        candidate_total = candidate_equity[-1] - 1.0
        baseline_dd = np.min(baseline_equity / np.maximum.accumulate(baseline_equity) - 1.0)
        candidate_dd = np.min(candidate_equity / np.maximum.accumulate(candidate_equity) - 1.0)
        candidate_wins += candidate_total > baseline_total
        candidate_lower_dd += candidate_dd > baseline_dd
        candidate_positive += candidate_total > 0.0
        returns.append(candidate_total)
    return {
        "block": block,
        "horizon": horizon,
        "paths": paths,
        "p_candidate_beats_atlas": candidate_wins / paths,
        "p_candidate_lower_drawdown": candidate_lower_dd / paths,
        "p_candidate_positive": candidate_positive / paths,
        "median_candidate_return": float(np.median(returns)),
        "p05_candidate_return": float(np.quantile(returns, 0.05)),
    }


def self_test() -> None:
    index = pd.date_range("2020-01-01", periods=900, freq="B", tz="UTC")
    rng = np.random.default_rng(119)
    accounts = []
    for mean, volatility, gross in ((0.0005, 0.014, 0.65), (0.0002, 0.007, 0.45), (0.0003, 0.009, 0.55)):
        returns = rng.normal(mean, volatility, len(index))
        accounts.append(
            pd.DataFrame(
                {
                    "equity": 10000.0 * np.cumprod(1.0 + returns),
                    "gross": gross,
                    "turnover": 0.01,
                },
                index=index,
            )
        )
    _, returns, _ = align_accounts(accounts)
    spec = AllocationSpec("lowcorr_leverage", 63, 20, 10.0, parameter_a=0.45, leverage_cap=1.10, target_vol=0.18)
    allocation, scale = build_allocation(returns, accounts[0].equity, spec)
    first = simulate(accounts, allocation, scale, spec)
    assert first.gross.max() <= 1.2000001
    changed = [account.copy() for account in accounts]
    changed[0].iloc[-1, changed[0].columns.get_loc("equity")] *= 5.0
    _, changed_returns, _ = align_accounts(changed)
    allocation_2, scale_2 = build_allocation(changed_returns, changed[0].equity, spec)
    second = simulate(changed, allocation_2, scale_2, spec)
    pd.testing.assert_frame_equal(first.iloc[:-1], second.iloc[:-1], check_exact=False, rtol=1e-11, atol=1e-11)
    print("V119-V126 self-test passed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--atlas", type=Path)
    parser.add_argument("--crisis", type=Path)
    parser.add_argument("--rotation", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if None in (args.atlas, args.crisis, args.rotation, args.output):
        raise SystemExit("all account and output paths are required")

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    accounts = [load_account(args.atlas), load_account(args.crisis), load_account(args.rotation)]
    _, sleeve_returns, _ = align_accounts(accounts)
    atlas_account = accounts[0]
    atlas_selection = safe_metrics(cut(atlas_account, "selection"))
    atlas_prefinal = safe_metrics(cut(atlas_account, "prefinal"))

    specs = candidate_specs()
    spec_map = {spec_id(spec): spec for spec in specs}
    account_map: dict[str, pd.DataFrame] = {}
    ranking_rows = []

    for number, spec in enumerate(specs, 1):
        candidate = spec_id(spec)
        allocation, scale = build_allocation(sleeve_returns, atlas_account.equity, spec)
        account = simulate(accounts, allocation, scale, spec)
        account_map[candidate] = account
        selection = safe_metrics(cut(account, "selection"))
        checks = {
            "cagr_floor": selection["annualized_return"]
            >= atlas_selection["annualized_return"] + SELECTION_GATES["cagr_floor_vs_atlas"],
            "drawdown_floor": selection["max_drawdown"] >= SELECTION_GATES["max_drawdown_floor"],
            "sharpe_floor": selection["sharpe"] >= SELECTION_GATES["sharpe_floor"],
            "max_gross": selection["max_gross"] <= SELECTION_GATES["max_gross"],
            "positive": selection["total_return"] > 0.0,
        }
        score = (
            selection["sharpe"]
            + 0.60 * selection["annualized_return"]
            - 0.35 * abs(selection["max_drawdown"])
            - 0.001 * selection["annual_turnover"]
        )
        ranking_rows.append(
            {
                "candidate": candidate,
                "family": spec.family,
                "eligible": all(checks.values()),
                "score": score,
                **checks,
                **{f"selection_{key}": value for key, value in selection.items()},
            }
        )
        if number % 50 == 0:
            print("candidate", number, "/", len(specs), flush=True)

    ranking = pd.DataFrame(ranking_rows).sort_values(["eligible", "score"], ascending=False)
    ranking.to_csv(output / "selection_ranking.csv", index=False)
    pool = ranking[ranking.eligible] if ranking.eligible.any() else ranking.head(25)
    selected = []
    used_families = set()
    for row in pool.itertuples():
        if row.family not in used_families or not selected:
            selected.append(str(row.candidate))
            used_families.add(str(row.family))
        if len(selected) == 3:
            break
    if not selected:
        selected = [str(ranking.iloc[0].candidate)]

    proof = {
        "selection_window": "2021-01-01/2023-12-31",
        "holdout_2024_2025_used": False,
        "final_2026h1_used": False,
        "program_level_holdout_pristine": False,
        "candidate_count": len(specs),
        "selected": selected,
        "selected_specs": [asdict(spec_map[name]) for name in selected],
        "ranking_top": ranking.head(50).to_dict(orient="records"),
    }
    proof_bytes = json.dumps(proof, indent=2, sort_keys=True).encode()
    proof_sha = hashlib.sha256(proof_bytes).hexdigest()
    (output / "selection_proof_before_holdout.json").write_bytes(proof_bytes)

    baseline_components = [account_map[name] for name in selected]
    robust = ensemble_accounts(baseline_components)
    robust.to_csv(output / "v125_robust_ensemble_equity.csv")

    variants = {"robust": robust}
    for delay in (1, 2, 5):
        components = []
        for name in selected:
            spec = spec_map[name]
            allocation, scale = build_allocation(sleeve_returns, atlas_account.equity, spec)
            components.append(simulate(accounts, allocation, scale, spec, delay=delay))
        variants[f"delay_{delay}"] = ensemble_accounts(components)
    for label, extra, no_leverage in (("severe_transfer", 40.0, False), ("no_leverage", 0.0, True)):
        components = []
        for name in selected:
            spec = spec_map[name]
            allocation, scale = build_allocation(sleeve_returns, atlas_account.equity, spec)
            components.append(
                simulate(
                    accounts,
                    allocation,
                    scale,
                    spec,
                    extra_transfer_cost_bps=extra,
                    force_no_leverage=no_leverage,
                )
            )
        variants[label] = ensemble_accounts(components)

    audit_rows = []
    for name, account in variants.items():
        account.to_csv(output / f"{name}_equity.csv")
        for period in PERIODS:
            audit_rows.append(
                {"variant": name, "period": period, **safe_metrics(cut(account, period))}
            )
    audit_table = pd.DataFrame(audit_rows)
    audit_table.to_csv(output / "audit_metrics.csv", index=False)

    def get(variant: str, period: str):
        return audit_table[(audit_table.variant == variant) & (audit_table.period == period)].iloc[0]

    prefinal = get("robust", "prefinal")
    holdout = get("robust", "holdout")
    final = get("robust", "final_2026h1")
    checks = {
        "holdout_positive": float(holdout.total_return) > 0.0,
        "final_positive": float(final.total_return) > 0.0,
        "cagr_not_destroyed": float(prefinal.annualized_return)
        >= atlas_prefinal["annualized_return"] - PROMOTION_GATES["cagr_loss_vs_atlas_max"],
        "dd_or_sharpe_improved": (
            float(prefinal.max_drawdown)
            >= atlas_prefinal["max_drawdown"] + PROMOTION_GATES["dd_improvement_min"]
        )
        or (
            float(prefinal.sharpe)
            >= atlas_prefinal["sharpe"] + PROMOTION_GATES["sharpe_improvement_min"]
        ),
        "max_gross": float(prefinal.max_gross) <= 1.20,
        "delay5_positive": float(get("delay_5", "full").annualized_return) > 0.0,
        "severe_transfer_positive": float(get("severe_transfer", "full").annualized_return) > 0.0,
        "selection_has_eligible": bool(ranking.eligible.any()),
    }
    passed = all(checks.values())

    yearly(robust).to_csv(output / "v126_yearly.csv", index=False)
    baseline_return = atlas_account.equity.pct_change().fillna(0.0)
    candidate_return = robust.equity.pct_change().fillna(0.0)
    bootstrap = [
        paired_bootstrap(baseline_return, candidate_return, block, horizon)
        for block in (20, 60)
        for horizon in (252, 504)
    ]
    pd.DataFrame(bootstrap).to_csv(output / "paired_bootstrap.csv", index=False)

    summary = {
        "research": "ACTIVE_V119_V126_ROBUST_TRIDENT",
        "status": "frozen_robust_composite_candidate" if passed else "rejected_or_needs_iteration",
        "selection_proof_sha256": proof_sha,
        "selected": selected,
        "selected_families": [spec_map[name].family for name in selected],
        "checks": checks,
        "atlas_selection": atlas_selection,
        "atlas_prefinal": atlas_prefinal,
        "robust_selection": safe_metrics(cut(robust, "selection")),
        "robust_prefinal": {key: float(prefinal[key]) for key in ("annualized_return", "total_return", "max_drawdown", "sharpe", "annual_turnover", "average_gross", "max_gross", "average_scale", "max_scale")},
        "robust_holdout": {key: float(holdout[key]) for key in ("annualized_return", "total_return", "max_drawdown", "sharpe")},
        "robust_final_2026h1": {key: float(final[key]) for key in ("annualized_return", "total_return", "max_drawdown", "sharpe")},
        "diagnostics": diagnostics(robust),
        "variants": {
            name: {
                "cagr": float(get(name, "full").annualized_return),
                "dd": float(get(name, "full").max_drawdown),
                "sharpe": float(get(name, "full").sharpe),
                "max_gross": float(get(name, "full").max_gross),
            }
            for name in variants
        },
        "bootstrap": bootstrap,
        "live_ready": False,
        "real_leverage_authorized": False,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    print(json.dumps(summary, indent=2, default=float))


if __name__ == "__main__":
    main()
