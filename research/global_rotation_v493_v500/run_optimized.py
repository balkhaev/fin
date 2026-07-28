#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "research" / "active_v103_v110"
sys.path.insert(0, str(SOURCE_ROOT))

import config  # noqa: E402
import data  # noqa: E402
import engine  # noqa: E402
import metrics as legacy_metrics  # noqa: E402
import signals  # noqa: E402


def cut(account: pd.DataFrame, period: str) -> pd.DataFrame:
    start, end = config.PERIODS[period]
    return account.loc[
        (account.index >= pd.Timestamp(start, tz="UTC"))
        & (account.index < pd.Timestamp(end, tz="UTC"))
    ]


def safe_metrics(account: pd.DataFrame) -> dict[str, float]:
    if len(account) >= 20:
        return legacy_metrics.metrics(account)
    return {
        key: 0.0
        for key in (
            "total_return", "annualized_return", "sharpe", "max_drawdown",
            "calmar", "annual_turnover", "average_gross", "max_gross",
            "final_equity",
        )
    }


def risk_scale_cached(
    scheduled: pd.DataFrame,
    returns: pd.DataFrame,
    target_vol: float,
    gross_cap: float,
    realised_vol: pd.Series | None = None,
) -> pd.DataFrame:
    if realised_vol is None:
        raw_return = (scheduled.shift(1) * returns).sum(axis=1).fillna(0.0)
        realised_vol = raw_return.rolling(63, min_periods=30).std() * np.sqrt(252.0)
    scale = (
        (target_vol / realised_vol.replace(0.0, np.nan))
        .shift(1)
        .clip(0.0, 2.0)
        .fillna(0.0)
    )
    output = scheduled.mul(scale, axis=0)
    gross = output.abs().sum(axis=1)
    cap_scale = (
        (gross_cap / gross.replace(0.0, np.nan)).clip(upper=1.0).fillna(1.0)
    )
    return output.mul(cap_scale, axis=0)


def simulate_cached(
    returns: pd.DataFrame,
    target: pd.DataFrame,
    start: str,
    end: str,
    cost_bps: float,
    annual_short_borrow: float = 0.02,
    annual_financing: float = 0.045,
) -> pd.DataFrame:
    index = returns.index[
        (returns.index >= pd.Timestamp(start, tz="UTC"))
        & (returns.index < pd.Timestamp(end, tz="UTC"))
    ]
    selected_returns = returns.reindex(index).fillna(0.0)
    weights = target.reindex(index).fillna(0.0).shift(1).fillna(0.0)
    turnover = weights.diff().abs().sum(axis=1).fillna(weights.abs().sum(axis=1))
    gross = weights.abs().sum(axis=1)
    short_gross = weights.clip(upper=0.0).abs().sum(axis=1)
    net_return = (
        (weights * selected_returns).sum(axis=1)
        - turnover * cost_bps / 10000.0
        - short_gross * annual_short_borrow / 252.0
        - (gross - 1.0).clip(lower=0.0) * annual_financing / 252.0
    )
    equity = 10000.0 * (1.0 + net_return).cumprod()
    return pd.DataFrame(
        {
            "equity": equity,
            "gross": gross,
            "turnover": turnover,
            "short_gross": short_gross,
        },
        index=index,
    )


def self_test() -> None:
    index = pd.date_range("2010-01-01", periods=360, freq="B", tz="UTC")
    rng = np.random.default_rng(493)
    prices = pd.DataFrame(
        {
            ticker: 100.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, len(index))))
            for ticker in config.UNIVERSE[:8]
        },
        index=index,
    )
    returns = prices.pct_change(fill_method=None)
    raw = pd.DataFrame(
        rng.normal(0.0, 0.1, size=prices.shape),
        index=index,
        columns=prices.columns,
    )
    scheduled = engine.schedule(raw, 10)
    raw_return = (scheduled.shift(1) * returns).sum(axis=1).fillna(0.0)
    realised = raw_return.rolling(63, min_periods=30).std() * np.sqrt(252.0)
    for target_vol, gross_cap in ((0.12, 1.0), (0.15, 1.15), (0.18, 1.25)):
        expected = engine.risk_scale(scheduled, returns, target_vol, gross_cap)
        actual = risk_scale_cached(
            scheduled, returns, target_vol, gross_cap, realised_vol=realised
        )
        pd.testing.assert_frame_equal(
            expected, actual, check_exact=False, rtol=1e-13, atol=1e-13
        )
        expected_account = engine.simulate(
            prices, expected, "2010-01-01", "2012-01-01", 10.0
        )
        actual_account = simulate_cached(
            returns, actual, "2010-01-01", "2012-01-01", 10.0
        )
        pd.testing.assert_frame_equal(
            expected_account, actual_account, check_exact=False, rtol=1e-13, atol=1e-13
        )
    print("V493 cached transport equality self-test passed")


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
    prices, manifest = data.load(config.UNIVERSE, args.cache)
    atlas = pd.read_csv(args.atlas, index_col=0, parse_dates=True)
    atlas.index = pd.to_datetime(atlas.index, utc=True)
    raw_processes = signals.process_targets(prices, config.GROUPS)
    returns = prices.pct_change(fill_method=None)

    rows: list[dict] = []
    targets: dict[str, pd.DataFrame] = {}
    candidate_ids: list[str] = []
    for process_number, (name, raw_target) in enumerate(raw_processes.items(), 1):
        for rebalance in config.REBALANCE:
            scheduled = engine.schedule(raw_target, rebalance)
            raw_return = (scheduled.shift(1) * returns).sum(axis=1).fillna(0.0)
            realised_vol = raw_return.rolling(63, min_periods=30).std() * np.sqrt(252.0)
            for target_vol, gross_cap in itertools.product(
                config.TARGET_VOL, config.GROSS_CAP
            ):
                candidate = (
                    f"{name}__r{rebalance}__v{int(target_vol * 100)}"
                    f"__g{int(gross_cap * 100)}"
                )
                weights = risk_scale_cached(
                    scheduled,
                    returns,
                    target_vol,
                    gross_cap,
                    realised_vol=realised_vol,
                )
                targets[candidate] = weights
                candidate_ids.append(candidate)
                for scenario in ("stress", "severe"):
                    account = simulate_cached(
                        returns,
                        weights,
                        *config.PERIODS["selection"],
                        config.COSTS[scenario],
                    )
                    for period in (*config.SELECTION_PERIODS, "selection"):
                        sliced = cut(account, period) if period != "selection" else account
                        rows.append(
                            {
                                "candidate": candidate,
                                "scenario": scenario,
                                "period": period,
                                **safe_metrics(sliced),
                            }
                        )
        print(
            f"process {process_number}/{len(raw_processes)} candidates={len(candidate_ids)}",
            flush=True,
        )

    table = pd.DataFrame(rows)
    ranking_rows: list[dict] = []
    for candidate in candidate_ids:
        stress = table[(table.candidate == candidate) & (table.scenario == "stress")]
        severe = table[(table.candidate == candidate) & (table.scenario == "severe")]
        selection = stress[stress.period == "selection"].iloc[0]
        period_rows = stress[stress.period.isin(config.SELECTION_PERIODS)]
        checks = {
            "periods_positive": bool(
                (period_rows.total_return > config.SELECTION_GATES["worst_period"]).all()
            ),
            "cagr": float(selection.annualized_return)
            >= config.SELECTION_GATES["cagr"],
            "sharpe": float(selection.sharpe)
            >= config.SELECTION_GATES["sharpe"],
            "dd": float(selection.max_drawdown) >= config.SELECTION_GATES["dd"],
            "turnover": float(selection.annual_turnover)
            <= config.SELECTION_GATES["turnover"],
            "severe": float(
                severe[severe.period.isin(config.SELECTION_PERIODS)].total_return.min()
            )
            >= config.SELECTION_GATES["severe_worst"],
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

    ranking = pd.DataFrame(ranking_rows).sort_values(
        ["eligible", "score"], ascending=False
    )
    ranking.to_csv(output / "selection_ranking.csv", index=False)
    pool = ranking[ranking.eligible] if ranking.eligible.any() else ranking.head(20)
    selected: list[str] = []
    used_families: set[str] = set()
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
        "transport": "cached_exact_equal_to_legacy",
    }
    proof_bytes = json.dumps(proof, indent=2, sort_keys=True).encode()
    proof_hash = hashlib.sha256(proof_bytes).hexdigest()
    (output / "selection_proof_before_post2020.json").write_bytes(proof_bytes)

    evaluation_rows: list[dict] = []
    accounts: dict[str, pd.DataFrame] = {}
    for scenario, cost_bps in config.COSTS.items():
        account = simulate_cached(
            returns, ensemble, *config.PERIODS["full"], cost_bps
        )
        accounts[scenario] = account
        account.to_csv(output / f"v103_{scenario}_equity.csv")
        for period in config.PERIODS:
            evaluation_rows.append(
                {
                    "scenario": scenario,
                    "period": period,
                    **safe_metrics(cut(account, period)),
                }
            )
    evaluation = pd.DataFrame(evaluation_rows)
    evaluation.to_csv(output / "metrics.csv", index=False)

    def get(scenario: str, period: str) -> pd.Series:
        return evaluation[
            (evaluation.scenario == scenario) & (evaluation.period == period)
        ].iloc[0]

    diagnostic = legacy_metrics.diagnostics(cut(accounts["stress"], "prefinal"))
    checks = {
        "bridge_positive": float(get("stress", "bridge").total_return) > 0.0,
        "holdout_positive": float(get("stress", "holdout").total_return) > 0.0,
        "cagr": float(get("stress", "prefinal").annualized_return)
        >= config.PROMOTION_GATES["cagr"],
        "sharpe": float(get("stress", "prefinal").sharpe)
        >= config.PROMOTION_GATES["sharpe"],
        "dd": float(get("stress", "prefinal").max_drawdown)
        >= config.PROMOTION_GATES["dd"],
        "post2020": diagnostic["post2020_cagr"]
        >= config.PROMOTION_GATES["post2020"],
        "concentration": diagnostic["best_positive_year_log_share"]
        <= config.PROMOTION_GATES["best_year_share"],
        "rolling": diagnostic["worst_rolling_252"]
        >= config.PROMOTION_GATES["rolling252"],
        "extreme": float(get("extreme", "full").annualized_return) > 0.0,
    }
    passed = all(checks.values())
    blend_weight = None
    if passed:
        scores: list[tuple[float, float]] = []
        blend_rows: list[dict] = []
        for weight in config.BLEND_WEIGHTS:
            combined = engine.combine(atlas, accounts["stress"], weight)
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
                    {
                        "weight": weight,
                        "period": period,
                        **safe_metrics(cut(combined, period)),
                    }
                )
        blend_weight = max(scores, key=lambda item: item[1])[0]
        pd.DataFrame(blend_rows).to_csv(output / "v109_blend_metrics.csv", index=False)

    legacy_metrics.yearly(accounts["stress"]).to_csv(output / "yearly.csv", index=False)
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
                "annualized_return", "total_return", "max_drawdown", "sharpe",
                "annual_turnover", "average_gross", "max_gross",
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
            for scenario in config.COSTS
        },
        "atlas_correlation": float(aligned.corr().iloc[0, 1]),
        "blend_selected_weight": blend_weight,
        "transport": "cached_exact_equal_to_legacy",
        "live_ready": False,
        "real_leverage_authorized": False,
        "data_manifest": manifest,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, default=float) + "\n"
    )
    print(json.dumps(summary, indent=2, default=float))


if __name__ == "__main__":
    main()
