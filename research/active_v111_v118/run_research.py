from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

from config import (
    DYNAMIC_CORR_THRESHOLD,
    DYNAMIC_LEVERAGE_CAP,
    DYNAMIC_TARGET_VOL,
    PERIODS,
    PROMOTION,
    REBALANCE_DAYS,
    STATIC_ATLAS,
    STATIC_EXTERNAL,
)
from engine import align, cut, load_account, simulate
from metrics import audit, metrics, yearly


def safe_metrics(account):
    if len(account) >= 20:
        return metrics(account)
    return {
        key: 0.0
        for key in (
            "total_return",
            "annualized_return",
            "sharpe",
            "max_drawdown",
            "calmar",
            "annual_turnover",
            "average_gross",
            "max_gross",
            "final_equity",
        )
    }


def self_test() -> None:
    index = pd.date_range("2020-01-01", periods=900, freq="B", tz="UTC")
    rng = np.random.default_rng(111)
    accounts = []
    for mean, stdev in ((0.0005, 0.015), (0.0002, 0.007), (0.0003, 0.009)):
        returns = rng.normal(mean, stdev, len(index))
        equity = 10000.0 * np.cumprod(1.0 + returns)
        accounts.append(pd.DataFrame({"equity": equity, "gross": 0.5, "turnover": 0.01}, index=index))
    first = simulate(accounts, (0.8, 0.1, 0.1), "dynamic", delay=0)
    delayed = simulate(accounts, (0.8, 0.1, 0.1), "dynamic", delay=2)
    assert first.gross.max() <= 1.2500001
    assert delayed.gross.max() <= 1.2500001
    changed = [account.copy() for account in accounts]
    changed[0].iloc[-1, changed[0].columns.get_loc("equity")] *= 5.0
    second = simulate(changed, (0.8, 0.1, 0.1), "dynamic")
    pd.testing.assert_frame_equal(
        first.iloc[:-1], second.iloc[:-1], check_exact=False, rtol=1e-12, atol=1e-12
    )
    print("V111-V118 self-test passed")


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
    atlas = accounts[0]
    atlas_metrics = {period: safe_metrics(cut(atlas, period, PERIODS)) for period in PERIODS}

    static_rows, static_accounts = [], {}
    for atlas_weight in STATIC_ATLAS:
        for crisis_weight in STATIC_EXTERNAL:
            rotation_weight = 1.0 - atlas_weight - crisis_weight
            if rotation_weight < 0.05 or rotation_weight > 0.30:
                continue
            for rebalance in REBALANCE_DAYS:
                name = (
                    f"a{int(atlas_weight * 100)}_c{int(crisis_weight * 100)}"
                    f"_r{int(rotation_weight * 100)}_d{rebalance}"
                )
                account = simulate(
                    accounts,
                    (atlas_weight, crisis_weight, rotation_weight),
                    "static",
                    rebalance=rebalance,
                )
                static_accounts[name] = account
                bridge = safe_metrics(cut(account, "bridge", PERIODS))
                static_rows.append(
                    {
                        "candidate": name,
                        "atlas": atlas_weight,
                        "crisis": crisis_weight,
                        "rotation": rotation_weight,
                        "rebalance": rebalance,
                        **bridge,
                        "score": bridge["sharpe"]
                        + 0.50 * bridge["annualized_return"]
                        - 0.25 * abs(bridge["max_drawdown"]),
                    }
                )
    static_ranking = pd.DataFrame(static_rows).sort_values("score", ascending=False)
    static_ranking.to_csv(output / "v111_static_bridge_ranking.csv", index=False)
    static_selected = str(static_ranking.iloc[0].candidate)
    static_account = static_accounts[static_selected]
    static_account.to_csv(output / "v111_static_equity.csv")
    base_weights = tuple(
        float(value) for value in static_ranking.iloc[0][["atlas", "crisis", "rotation"]]
    )

    dynamic_rows, dynamic_accounts = [], {}
    for target_vol, corr_threshold, leverage_cap, rebalance in itertools.product(
        DYNAMIC_TARGET_VOL,
        DYNAMIC_CORR_THRESHOLD,
        DYNAMIC_LEVERAGE_CAP,
        REBALANCE_DAYS,
    ):
        name = (
            f"tv{int(target_vol * 100)}_corr{int(corr_threshold * 100)}"
            f"_lev{int(leverage_cap * 100)}_d{rebalance}"
        )
        account = simulate(
            accounts,
            base_weights,
            "dynamic",
            target_vol=target_vol,
            corr_threshold=corr_threshold,
            leverage_cap=leverage_cap,
            rebalance=rebalance,
        )
        dynamic_accounts[name] = account
        bridge = safe_metrics(cut(account, "bridge", PERIODS))
        dynamic_rows.append(
            {
                "candidate": name,
                "target_vol": target_vol,
                "corr_threshold": corr_threshold,
                "leverage_cap": leverage_cap,
                "rebalance": rebalance,
                **bridge,
                "score": bridge["sharpe"]
                + 0.55 * bridge["annualized_return"]
                - 0.30 * abs(bridge["max_drawdown"]),
            }
        )
    dynamic_ranking = pd.DataFrame(dynamic_rows).sort_values("score", ascending=False)
    dynamic_ranking.to_csv(output / "v112_dynamic_bridge_ranking.csv", index=False)
    selected_dynamic = list(dynamic_ranking.head(3).candidate)

    returns = [dynamic_accounts[name].equity.pct_change().fillna(0.0) for name in selected_dynamic]
    index = dynamic_accounts[selected_dynamic[0]].index
    ensemble_return = sum(returns) / len(returns)
    ensemble = pd.DataFrame(
        {
            "equity": 10000.0 * (1.0 + ensemble_return).cumprod(),
            "gross": sum(dynamic_accounts[name].gross for name in selected_dynamic) / len(selected_dynamic),
            "turnover": sum(dynamic_accounts[name].turnover for name in selected_dynamic) / len(selected_dynamic),
            "leverage_scale": sum(dynamic_accounts[name].leverage_scale for name in selected_dynamic) / len(selected_dynamic),
        },
        index=index,
    )
    ensemble.to_csv(output / "v113_dynamic_ensemble_equity.csv")

    proof = {
        "selection_window": "2021-01-01/2023-12-31",
        "holdout_2024_2025_used": False,
        "final_2026h1_used": False,
        "static_selected": static_selected,
        "dynamic_selected": selected_dynamic,
        "static_ranking": static_ranking.head(30).to_dict(orient="records"),
        "dynamic_ranking": dynamic_ranking.head(30).to_dict(orient="records"),
    }
    proof_bytes = json.dumps(proof, indent=2, sort_keys=True).encode()
    proof_hash = hashlib.sha256(proof_bytes).hexdigest()
    (output / "selection_proof_before_holdout.json").write_bytes(proof_bytes)

    first = dynamic_ranking.iloc[0]
    variants = {
        "static": static_account,
        "dynamic": ensemble,
        "no_leverage": simulate(
            accounts,
            base_weights,
            "dynamic",
            target_vol=float(first.target_vol),
            corr_threshold=float(first.corr_threshold),
            leverage_cap=1.0,
            rebalance=int(first.rebalance),
        ),
        "severe_transfer_cost": simulate(
            accounts,
            base_weights,
            "dynamic",
            target_vol=float(first.target_vol),
            corr_threshold=float(first.corr_threshold),
            leverage_cap=float(first.leverage_cap),
            rebalance=int(first.rebalance),
            extra_cost_bps=40.0,
        ),
    }
    for delay in (1, 2, 5):
        variants[f"delay_{delay}"] = simulate(
            accounts,
            base_weights,
            "dynamic",
            target_vol=float(first.target_vol),
            corr_threshold=float(first.corr_threshold),
            leverage_cap=float(first.leverage_cap),
            rebalance=int(first.rebalance),
            delay=delay,
        )

    audit_rows = []
    for name, account in variants.items():
        account.to_csv(output / f"{name}_equity.csv")
        for period in PERIODS:
            audit_rows.append(
                {"variant": name, "period": period, **safe_metrics(cut(account, period, PERIODS))}
            )
    audit_table = pd.DataFrame(audit_rows)
    audit_table.to_csv(output / "v114_v116_audit_metrics.csv", index=False)

    def get(variant: str, period: str):
        return audit_table[(audit_table.variant == variant) & (audit_table.period == period)].iloc[0]

    prefinal = get("dynamic", "prefinal")
    holdout = get("dynamic", "holdout")
    final = get("dynamic", "final_2026h1")
    atlas_prefinal = atlas_metrics["prefinal"]
    checks = {
        "holdout_positive": float(holdout.total_return) > 0.0,
        "final_positive": float(final.total_return) > 0.0,
        "cagr_not_destroyed": float(prefinal.annualized_return)
        >= atlas_prefinal["annualized_return"] - PROMOTION["cagr_loss_max"],
        "dd_or_sharpe_improved": (
            float(prefinal.max_drawdown)
            >= atlas_prefinal["max_drawdown"] + PROMOTION["dd_improvement_min"]
        )
        or (
            float(prefinal.sharpe)
            >= atlas_prefinal["sharpe"] + PROMOTION["sharpe_improvement_min"]
        ),
        "max_gross": float(prefinal.max_gross) <= PROMOTION["max_gross"],
        "delay5_positive": float(get("delay_5", "full").annualized_return) > 0.0,
        "severe_transfer_positive": float(
            get("severe_transfer_cost", "full").annualized_return
        )
        > 0.0,
    }
    passed = all(checks.values())
    yearly(ensemble).to_csv(output / "v117_yearly.csv", index=False)
    summary = {
        "research": "ACTIVE_V111_V118_TRIDENT",
        "status": "frozen_composite_candidate" if passed else "rejected_or_needs_iteration",
        "selection_proof_sha256": proof_hash,
        "static_selected": static_selected,
        "dynamic_selected": selected_dynamic,
        "checks": checks,
        "atlas_prefinal": atlas_prefinal,
        "trident_prefinal": {
            key: float(prefinal[key])
            for key in (
                "annualized_return",
                "total_return",
                "max_drawdown",
                "sharpe",
                "annual_turnover",
                "average_gross",
                "max_gross",
            )
        },
        "trident_holdout": {
            key: float(holdout[key])
            for key in ("annualized_return", "total_return", "max_drawdown", "sharpe")
        },
        "trident_final_2026h1": {
            key: float(final[key])
            for key in ("annualized_return", "total_return", "max_drawdown", "sharpe")
        },
        "audit": audit(ensemble),
        "latency_costs": {
            name: {
                "cagr": float(get(name, "full").annualized_return),
                "dd": float(get(name, "full").max_drawdown),
                "sharpe": float(get(name, "full").sharpe),
            }
            for name in variants
        },
        "live_ready": False,
        "real_leverage_authorized": False,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    print(json.dumps(summary, indent=2, default=float))


if __name__ == "__main__":
    main()
