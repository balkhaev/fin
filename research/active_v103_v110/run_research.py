from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

from config import (
    BLEND_WEIGHTS,
    COSTS,
    GROSS_CAP,
    GROUPS,
    PERIODS,
    PROMOTION_GATES,
    REBALANCE,
    SELECTION_GATES,
    SELECTION_PERIODS,
    TARGET_VOL,
    UNIVERSE,
)
from data import load
from engine import combine, risk_scale, schedule, simulate
from metrics import diagnostics, metrics, yearly
from signals import process_targets


def cut(account: pd.DataFrame, period: str) -> pd.DataFrame:
    start, end = PERIODS[period]
    return account.loc[
        (account.index >= pd.Timestamp(start, tz="UTC"))
        & (account.index < pd.Timestamp(end, tz="UTC"))
    ]


def safe_metrics(account: pd.DataFrame) -> dict[str, float]:
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
    index = pd.date_range("2010-01-01", periods=900, freq="B", tz="UTC")
    rng = np.random.default_rng(103)
    prices = pd.DataFrame(
        {
            ticker: 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, len(index))))
            for ticker in UNIVERSE
        },
        index=index,
    )
    processes = process_targets(prices, GROUPS)
    first = next(iter(processes.values()))
    weights = risk_scale(schedule(first, 10), prices.pct_change(fill_method=None), 0.15, 1.15)
    assert weights.abs().sum(axis=1).max() <= 1.1500001
    changed = prices.copy()
    changed.iloc[-1] *= 5.0
    second = next(iter(process_targets(changed, GROUPS).values()))
    weights_2 = risk_scale(schedule(second, 10), changed.pct_change(fill_method=None), 0.15, 1.15)
    pd.testing.assert_frame_equal(
        weights.iloc[:-1], weights_2.iloc[:-1], check_exact=False, rtol=1e-12, atol=1e-12
    )
    print("V103-V110 self-test passed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--atlas", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if None in (args.cache, args.atlas, args.output):
        raise SystemExit("--cache, --atlas and --output are required")

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    prices, manifest = load(UNIVERSE, args.cache)
    atlas = pd.read_csv(args.atlas, index_col=0, parse_dates=True)
    atlas.index = pd.to_datetime(atlas.index, utc=True)
    raw_processes = process_targets(prices, GROUPS)
    returns = prices.pct_change(fill_method=None)

    rows, targets, candidate_ids = [], {}, []
    for name, raw_target in raw_processes.items():
        for rebalance, target_vol, gross_cap in itertools.product(REBALANCE, TARGET_VOL, GROSS_CAP):
            candidate = f"{name}__r{rebalance}__v{int(target_vol * 100)}__g{int(gross_cap * 100)}"
            weights = risk_scale(schedule(raw_target, rebalance), returns, target_vol, gross_cap)
            targets[candidate] = weights
            candidate_ids.append(candidate)
            for scenario in ("stress", "severe"):
                account = simulate(prices, weights, *PERIODS["selection"], COSTS[scenario])
                for period in (*SELECTION_PERIODS, "selection"):
                    sliced = cut(account, period) if period != "selection" else account
                    rows.append(
                        {
                            "candidate": candidate,
                            "scenario": scenario,
                            "period": period,
                            **safe_metrics(sliced),
                        }
                    )

    table = pd.DataFrame(rows)
    ranking_rows = []
    for candidate in candidate_ids:
        stress = table[(table.candidate == candidate) & (table.scenario == "stress")]
        severe = table[(table.candidate == candidate) & (table.scenario == "severe")]
        selection = stress[stress.period == "selection"].iloc[0]
        period_rows = stress[stress.period.isin(SELECTION_PERIODS)]
        checks = {
            "periods_positive": bool((period_rows.total_return > SELECTION_GATES["worst_period"]).all()),
            "cagr": float(selection.annualized_return) >= SELECTION_GATES["cagr"],
            "sharpe": float(selection.sharpe) >= SELECTION_GATES["sharpe"],
            "dd": float(selection.max_drawdown) >= SELECTION_GATES["dd"],
            "turnover": float(selection.annual_turnover) <= SELECTION_GATES["turnover"],
            "severe": float(severe[severe.period.isin(SELECTION_PERIODS)].total_return.min())
            >= SELECTION_GATES["severe_worst"],
        }
        score = float(
            selection.annualized_return
            + 0.10 * selection.sharpe
            - 0.15 * abs(selection.max_drawdown)
            - 0.001 * selection.annual_turnover
        )
        ranking_rows.append(
            {
                "candidate": candidate,
                "eligible": all(checks.values()),
                "score": score,
                **checks,
                "selection_cagr": selection.annualized_return,
                "selection_sharpe": selection.sharpe,
                "selection_dd": selection.max_drawdown,
                "turnover": selection.annual_turnover,
            }
        )

    ranking = pd.DataFrame(ranking_rows).sort_values(["eligible", "score"], ascending=False)
    ranking.to_csv(output / "selection_ranking.csv", index=False)
    pool = ranking[ranking.eligible] if ranking.eligible.any() else ranking.head(20)
    selected, used_families = [], set()
    for candidate in pool.candidate:
        family = str(candidate).split("__")[0].split("_l")[0]
        if family not in used_families or not selected:
            selected.append(str(candidate))
            used_families.add(family)
        if len(selected) == 3:
            break

    ensemble = sum(targets[name] for name in selected) / len(selected)
    ensemble.to_csv(output / "v103_target_weights.csv")
    proof = {
        "selection_end": "2020-12-31",
        "candidate_count": len(candidate_ids),
        "selected": selected,
        "ranking_top": ranking.head(50).to_dict(orient="records"),
    }
    proof_bytes = json.dumps(proof, indent=2, sort_keys=True).encode()
    proof_hash = hashlib.sha256(proof_bytes).hexdigest()
    (output / "selection_proof_before_post2020.json").write_bytes(proof_bytes)

    evaluation_rows, accounts = [], {}
    for scenario, cost_bps in COSTS.items():
        account = simulate(prices, ensemble, *PERIODS["full"], cost_bps)
        accounts[scenario] = account
        account.to_csv(output / f"v103_{scenario}_equity.csv")
        for period in PERIODS:
            evaluation_rows.append(
                {"scenario": scenario, "period": period, **safe_metrics(cut(account, period))}
            )
    evaluation = pd.DataFrame(evaluation_rows)
    evaluation.to_csv(output / "metrics.csv", index=False)

    def get(scenario: str, period: str):
        return evaluation[(evaluation.scenario == scenario) & (evaluation.period == period)].iloc[0]

    diagnostic = diagnostics(cut(accounts["stress"], "prefinal"))
    checks = {
        "bridge_positive": float(get("stress", "bridge").total_return) > 0.0,
        "holdout_positive": float(get("stress", "holdout").total_return) > 0.0,
        "cagr": float(get("stress", "prefinal").annualized_return) >= PROMOTION_GATES["cagr"],
        "sharpe": float(get("stress", "prefinal").sharpe) >= PROMOTION_GATES["sharpe"],
        "dd": float(get("stress", "prefinal").max_drawdown) >= PROMOTION_GATES["dd"],
        "post2020": diagnostic["post2020_cagr"] >= PROMOTION_GATES["post2020"],
        "concentration": diagnostic["best_positive_year_log_share"] <= PROMOTION_GATES["best_year_share"],
        "rolling": diagnostic["worst_rolling_252"] >= PROMOTION_GATES["rolling252"],
        "extreme": float(get("extreme", "full").annualized_return) > 0.0,
    }
    passed = all(checks.values())
    blend_weight = None
    if passed:
        scores, blend_rows = [], []
        for weight in BLEND_WEIGHTS:
            combined = combine(atlas, accounts["stress"], weight)
            bridge_metrics = safe_metrics(cut(combined, "bridge"))
            scores.append(
                (
                    weight,
                    bridge_metrics["sharpe"]
                    + 0.40 * bridge_metrics["annualized_return"]
                    - 0.20 * abs(bridge_metrics["max_drawdown"]),
                )
            )
            for period in ("bridge", "holdout", "final_2026h1", "prefinal", "full"):
                blend_rows.append(
                    {"weight": weight, "period": period, **safe_metrics(cut(combined, period))}
                )
        blend_weight = max(scores, key=lambda item: item[1])[0]
        pd.DataFrame(blend_rows).to_csv(output / "v109_blend_metrics.csv", index=False)

    yearly(accounts["stress"]).to_csv(output / "yearly.csv", index=False)
    aligned = pd.concat(
        [atlas.equity.pct_change(), accounts["stress"].equity.pct_change()], axis=1
    ).dropna()
    summary = {
        "research": "ACTIVE_V103_V110_GLOBAL_ROTATION",
        "status": "frozen_candidate" if passed else "rejected_or_needs_iteration",
        "selection_proof_sha256": proof_hash,
        "selected": selected,
        "checks": checks,
        "diagnostics": diagnostic,
        "stress_prefinal": {
            key: float(get("stress", "prefinal")[key])
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
        "final_2026h1": {
            key: float(get("stress", "final_2026h1")[key])
            for key in ("annualized_return", "total_return", "max_drawdown", "sharpe")
        },
        "strict_costs": {
            scenario: {
                "cagr": float(get(scenario, "full").annualized_return),
                "dd": float(get(scenario, "full").max_drawdown),
            }
            for scenario in COSTS
        },
        "atlas_correlation": float(aligned.corr().iloc[0, 1]),
        "blend_selected_weight": blend_weight,
        "live_ready": False,
        "real_leverage_authorized": False,
        "data_manifest": manifest,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    print(json.dumps(summary, indent=2, default=float))


if __name__ == "__main__":
    main()
