#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from config import COSTS, ResearchConfig, TrendParams, trend_grid
from data import load_all
from metrics import equity_metrics
from strategy import DailyCache, ensemble_weights, evaluate_grid, neighbor_count, simulate, target_weights


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cost-aware low-turnover crypto trend research")
    parser.add_argument("--output", type=Path, default=Path("artifacts/active_v3"))
    parser.add_argument("--cache", type=Path, default=Path(".cache/binance_vision_15m"))
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def periods(config: ResearchConfig) -> dict[str, tuple[str, str]]:
    return {
        "development": (config.start, config.development_end),
        "validation": (config.development_end, config.validation_end),
        "research_holdout": (config.validation_end, config.end_exclusive),
    }


def instantiate(row: pd.Series) -> TrendParams:
    return TrendParams(
        fast_days=int(row.fast_days), slow_days=int(row.slow_days), ema_days=int(row.ema_days),
        vol_days=int(row.vol_days), target_vol=float(row.target_vol),
        rebalance_days=int(row.rebalance_days), min_hold_days=int(row.min_hold_days),
        switch_threshold=float(row.switch_threshold), weight_band=float(row.weight_band),
        min_slow_momentum=float(row.min_slow_momentum),
    )


def diverse_top(results: pd.DataFrame, limit: int = 3) -> pd.DataFrame:
    columns = [
        "fast_days", "slow_days", "ema_days", "vol_days", "target_vol",
        "rebalance_days", "min_hold_days", "switch_threshold", "weight_band",
        "min_slow_momentum",
    ]
    viable = results[(results.robust_score > -1e8) & (results.neighbor_count >= 3)]
    if viable.empty:
        viable = results[results.robust_score > -1e8]
    selected: list[int] = []
    for index, row in viable.iterrows():
        if not selected or all(sum(row[c] != viable.loc[other, c] for c in columns) >= 2 for other in selected):
            selected.append(index)
        if len(selected) == limit:
            break
    for index in viable.index:
        if len(selected) == limit:
            break
        if index not in selected:
            selected.append(index)
    return viable.loc[selected].reset_index(drop=True)


def self_test() -> None:
    rng = np.random.default_rng(17)
    index = pd.date_range("2020-01-01", periods=1_100, freq="1D", tz="UTC")
    daily: dict[str, pd.DataFrame] = {}
    for number, symbol in enumerate(("BTCUSDT", "ETHUSDT"), start=1):
        returns = rng.normal(0.0004 * number, 0.025, len(index))
        close = 10_000 * np.exp(np.cumsum(returns))
        open_ = np.r_[close[0], close[:-1]]
        high = np.maximum(open_, close) * (1 + rng.uniform(0.0, 0.02, len(index)))
        low = np.minimum(open_, close) * (1 - rng.uniform(0.0, 0.02, len(index)))
        daily[symbol] = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=index)
    config = ResearchConfig(end_exclusive="2023-01-05", development_end="2021-06-01", validation_end="2022-06-01")
    cache = DailyCache(daily)
    params = TrendParams(21, 90, 100, 30, 0.20, 7, 14, 0.25, 0.10, 0.0)
    weights = target_weights(cache, params)
    equity = simulate(cache, weights, COSTS[1], config, config.start, config.end_exclusive)
    assert len(equity) > 1_000
    assert np.isfinite(equity.equity).all()
    assert float(weights.max().max()) <= 1.0
    assert float(weights.min().min()) >= 0.0
    print("self-test passed")


def yearly_rows(equity: pd.DataFrame, family: str, scenario: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for year, segment in equity.groupby(equity.index.year):
        values = equity_metrics(segment.equity)
        rows.append({"family": family, "scenario": scenario, "year": int(year), **values})
    return rows


def rolling_diagnostics(equity: pd.DataFrame) -> dict[str, float]:
    series = equity.equity
    rolling_return = series / series.shift(365) - 1
    valid = rolling_return.dropna()
    return {
        "rolling_365_windows": int(len(valid)),
        "rolling_365_positive_share": float((valid > 0).mean()) if len(valid) else np.nan,
        "rolling_365_worst": float(valid.min()) if len(valid) else np.nan,
        "rolling_365_median": float(valid.median()) if len(valid) else np.nan,
    }


def save_plot(equity: pd.DataFrame, benchmark: pd.Series, output: Path) -> None:
    benchmark = benchmark.reindex(equity.index).ffill().dropna()
    benchmark = benchmark / benchmark.iloc[0] * equity.equity.iloc[0]
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(equity.index, equity.equity, label="V3 low-turnover ensemble")
    ax.plot(benchmark.index, benchmark, label="50/50 BTC-ETH buy & hold")
    ax.set_title("Active V3 — research holdout")
    ax.set_ylabel("USDT")
    ax.grid(True, alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(output, dpi=160)
    plt.close(fig)


def main() -> int:
    parsed = args()
    if parsed.self_test:
        self_test()
        return 0
    output = parsed.output
    output.mkdir(parents=True, exist_ok=True)
    config = ResearchConfig()
    base = next(cost for cost in COSTS if cost.name == "base")
    stress = next(cost for cost in COSTS if cost.name == "stress")
    daily, manifest, quality = load_all(config, parsed.cache, parsed.refresh)
    pd.DataFrame(manifest).to_csv(output / "data_manifest.csv", index=False)
    pd.DataFrame(quality).to_csv(output / "data_quality.csv", index=False)
    (output / "config.json").write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")

    cache = DailyCache(daily)
    search = evaluate_grid(cache, trend_grid(), base, stress, config)
    search["neighbor_count"] = neighbor_count(search)
    search.to_csv(output / "search.csv", index=False)
    selected = diverse_top(search, 3)
    selected.to_csv(output / "selected.csv", index=False)
    parameters = [instantiate(row) for _, row in selected.iterrows()]
    frames = [target_weights(cache, parameter) for parameter in parameters]
    ensemble = ensemble_weights(frames)

    metrics_rows: list[dict[str, object]] = []
    yearly: list[dict[str, object]] = []
    holdout_by_cost: dict[str, pd.DataFrame] = {}
    for cost in COSTS:
        for period, (start, end) in periods(config).items():
            equity = simulate(cache, ensemble, cost, config, start, end)
            values = equity_metrics(equity.equity)
            years = max((pd.Timestamp(end) - pd.Timestamp(start)).days / 365, 1 / 365)
            metrics_rows.append({
                "family": "daily_trend_ensemble", "key": "+".join(p.key for p in parameters),
                "scenario": cost.name, "period": period, **values,
                "annual_turnover": float(equity.turnover.sum() / years),
                "average_exposure": float(equity.exposure.mean()),
                "final_equity": float(equity.equity.iloc[-1]),
                **rolling_diagnostics(equity),
            })
            if period == "research_holdout":
                holdout_by_cost[cost.name] = equity
        full = simulate(cache, ensemble, cost, config, config.start, config.end_exclusive)
        yearly.extend(yearly_rows(full, "daily_trend_ensemble", cost.name))
    for parameter, weights in zip(parameters, frames):
        for cost in (base, stress):
            for period, (start, end) in periods(config).items():
                equity = simulate(cache, weights, cost, config, start, end)
                values = equity_metrics(equity.equity)
                years = max((pd.Timestamp(end) - pd.Timestamp(start)).days / 365, 1 / 365)
                metrics_rows.append({
                    "family": "daily_trend_component", "key": parameter.key,
                    "scenario": cost.name, "period": period, **values,
                    "annual_turnover": float(equity.turnover.sum() / years),
                    "average_exposure": float(equity.exposure.mean()),
                    "final_equity": float(equity.equity.iloc[-1]),
                    **rolling_diagnostics(equity),
                })
    metrics = pd.DataFrame(metrics_rows)
    metrics.to_csv(output / "metrics.csv", index=False)
    pd.DataFrame(yearly).to_csv(output / "yearly.csv", index=False)
    (output / "selected_params.json").write_text(
        json.dumps([asdict(parameter) for parameter in parameters], indent=2), encoding="utf-8"
    )

    base_holdout = metrics[(metrics.family == "daily_trend_ensemble") & (metrics.scenario == "base") & (metrics.period == "research_holdout")].iloc[0]
    stress_holdout = metrics[(metrics.family == "daily_trend_ensemble") & (metrics.scenario == "stress") & (metrics.period == "research_holdout")].iloc[0]
    validation_base = metrics[(metrics.family == "daily_trend_ensemble") & (metrics.scenario == "base") & (metrics.period == "validation")].iloc[0]
    validation_stress = metrics[(metrics.family == "daily_trend_ensemble") & (metrics.scenario == "stress") & (metrics.period == "validation")].iloc[0]
    component_holdout = metrics[(metrics.family == "daily_trend_component") & (metrics.scenario == "stress") & (metrics.period == "research_holdout")]
    status = "promising_research_candidate" if (
        validation_base.total_return > 0
        and validation_stress.total_return > 0
        and base_holdout.total_return > 0
        and stress_holdout.total_return > 0
        and stress_holdout.max_drawdown > -0.25
        and stress_holdout.annual_turnover < 30
        and (component_holdout.total_return > 0).sum() >= 2
        and stress_holdout.rolling_365_positive_share >= 0.55
    ) else "rejected_or_needs_iteration"
    summary = {
        "status": status,
        "selection_is_holdout_free": True,
        "holdout_is_fully_untouched": False,
        "base_holdout_return": float(base_holdout.total_return),
        "stress_holdout_return": float(stress_holdout.total_return),
        "stress_holdout_max_drawdown": float(stress_holdout.max_drawdown),
        "stress_holdout_annual_turnover": float(stress_holdout.annual_turnover),
        "stress_holdout_rolling_positive_share": float(stress_holdout.rolling_365_positive_share),
        "positive_stress_components": int((component_holdout.total_return > 0).sum()),
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    base_equity = holdout_by_cost["base"]
    stress_equity = holdout_by_cost["stress"]
    base_equity.to_csv(output / "holdout_base_equity.csv")
    stress_equity.to_csv(output / "holdout_stress_equity.csv")
    benchmark = cache.close.div(cache.close.iloc[0]).mean(axis=1)
    save_plot(base_equity, benchmark, output / "holdout_equity.png")
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.fill_between(stress_equity.index, stress_equity.drawdown, 0.0, alpha=0.4)
    ax.set_title("V3 stress drawdown — research holdout")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(output / "holdout_stress_drawdown.png", dpi=160)
    plt.close(fig)

    key = metrics[(metrics.family == "daily_trend_ensemble") & metrics.period.isin(["validation", "research_holdout"])][[
        "scenario", "period", "total_return", "annualized_return", "max_drawdown",
        "sharpe", "calmar", "annual_turnover", "average_exposure",
        "rolling_365_positive_share", "rolling_365_worst",
    ]]
    report = [
        "# Active V3 — cost-aware low-turnover trend", "",
        f"Статус: **{status}**.", "",
        "## Выбранные конфигурации", "", selected.to_markdown(index=False), "",
        "## Ключевые метрики", "", key.to_markdown(index=False), "",
        "## Интерпретация", "",
        "- Отбор выполнен только по development и validation одновременно при 10 и 20 б.п. на сторону.",
        "- Research holdout не участвовал в ранжировании, но не является полностью нетронутым после предыдущих итераций.",
        "- Положительный статус означает только допуск к неизменяемому paper-forward процессу, не к реальным деньгам.",
        "- Плечо отсутствует; остаток капитала находится в cash.",
    ]
    (output / "REPORT_RU.md").write_text("\n".join(report), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
