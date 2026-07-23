#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from config import COSTS, ProcessSpec, ResearchConfig, process_grid
from data import load_all
from metrics import annual_rows, equity_metrics, rolling_diagnostics
from strategy import (
    MarketData,
    ProcessFactory,
    annual_turnover,
    build_family_experts,
    mean_frames,
    evaluate_processes,
    select_diverse,
    simulate,
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Active V4 walk-forward crypto ensemble research")
    parser.add_argument("--output", type=Path, default=Path("artifacts/active_v4"))
    parser.add_argument("--cache", type=Path, default=Path(".cache/binance_vision_1d"))
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def periods(config: ResearchConfig) -> dict[str, tuple[str, str]]:
    return {
        "development": (config.evaluation_start, config.development_end),
        "validation": (config.development_end, config.validation_end),
        "research_holdout": (config.validation_end, config.end_exclusive),
    }


def instantiate_process(row: pd.Series) -> ProcessSpec:
    subset_value = row.get("subset", ())
    if isinstance(subset_value, str):
        subset = tuple(part for part in subset_value.strip("()[]").replace("'", "").split(", ") if part)
    elif isinstance(subset_value, (tuple, list)):
        subset = tuple(str(part) for part in subset_value)
    else:
        subset = ()
    return ProcessSpec(
        kind=str(row.kind),
        train_days=int(row.train_days),
        selection_days=int(row.selection_days),
        top_k=int(row.top_k),
        score_mode=str(row.score_mode),
        overlay=str(row.overlay),
        subset=subset,
    )


def self_test() -> None:
    rng = np.random.default_rng(41)
    index = pd.date_range("2018-01-01", periods=2_250, freq="1D", tz="UTC")
    daily: dict[str, pd.DataFrame] = {}
    for number, symbol in enumerate(("BTCUSDT", "ETHUSDT"), start=1):
        cycle = 0.0015 * np.sin(np.arange(len(index)) / (45 + number * 8))
        returns = cycle + rng.normal(0.0002, 0.018 + number * 0.003, len(index))
        previous_close = 10_000 * np.exp(np.cumsum(returns))
        overnight = rng.normal(0.0, 0.004, len(index))
        open_ = np.r_[previous_close[0], previous_close[:-1] * (1 + overnight[1:])]
        close = previous_close
        high = np.maximum(open_, close) * (1 + rng.uniform(0.0, 0.01, len(index)))
        low = np.minimum(open_, close) * (1 - rng.uniform(0.0, 0.01, len(index)))
        daily[symbol] = pd.DataFrame({
            "open": open_, "high": high, "low": low, "close": close,
            "volume": 1.0, "quote_volume": 1.0, "trades": 1.0, "taker_buy_quote": 0.5,
        }, index=index)
    data = MarketData(daily)
    weights = pd.DataFrame(0.0, index=data.index, columns=data.symbols)
    weights.loc[data.index[400]:, data.symbols[0]] = 0.35
    config = ResearchConfig(
        data_start="2018-01-01", evaluation_start="2019-01-01",
        development_end="2021-01-01", validation_end="2023-01-01",
        end_exclusive=str((index[-1] + pd.Timedelta(days=1)).date()),
    )
    equity = simulate(data, weights, COSTS[1], config, data.index[300], data.index[-1] + pd.Timedelta(days=1))
    assert len(equity) > 1_800
    assert np.isfinite(equity.equity).all()
    assert float(weights.min().min()) >= 0.0
    assert float(weights.sum(axis=1).max()) <= 1.0
    assert float(equity.turnover.sum()) > 0.0
    print("self-test passed")


def save_equity_plot(equity: pd.DataFrame, benchmark: pd.Series, title: str, output: Path) -> None:
    benchmark = benchmark.reindex(equity.index).ffill().dropna()
    benchmark = benchmark / benchmark.iloc[0] * equity.equity.iloc[0]
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(equity.index, equity.equity, label="V4 ensemble")
    ax.plot(benchmark.index, benchmark, label="50/50 BTC-ETH buy & hold")
    ax.set_title(title)
    ax.set_ylabel("USDT")
    ax.grid(True, alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(output, dpi=160)
    plt.close(fig)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parsed = arguments()
    if parsed.self_test:
        self_test()
        return 0

    output = parsed.output
    output.mkdir(parents=True, exist_ok=True)
    config = ResearchConfig()
    cost_by_name = {cost.name: cost for cost in COSTS}
    base, stress = cost_by_name["base"], cost_by_name["stress"]

    daily, manifest, quality = load_all(config, parsed.cache, parsed.refresh)
    pd.DataFrame(manifest).to_csv(output / "data_manifest.csv", index=False)
    pd.DataFrame(quality).to_csv(output / "data_quality.csv", index=False)
    (output / "config.json").write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")

    data = MarketData(daily)
    families, variant_counts = build_family_experts(data)
    pd.DataFrame([
        {"family": family, "variant_count": variant_counts[family], "mean_exposure": float(frame.sum(axis=1).mean())}
        for family, frame in families.items()
    ]).to_csv(output / "family_library.csv", index=False)

    factory = ProcessFactory(data, families, base, stress, config)
    search, process_frames, selection_log = evaluate_processes(
        data, factory, process_grid(), base, stress, config,
    )
    search.to_csv(output / "process_search.csv", index=False)
    pd.DataFrame(selection_log).to_csv(output / "selection_history.csv", index=False)
    selected = select_diverse(search, 3)
    selected.to_csv(output / "selected_processes.csv", index=False)
    selected_specs = [instantiate_process(row) for _, row in selected.iterrows()]
    selected_frames = [process_frames[spec.key] for spec in selected_specs]
    ensemble = mean_frames(selected_frames, next(iter(families.values())))

    metrics_rows: list[dict[str, object]] = []
    yearly_rows: list[dict[str, object]] = []
    holdout_by_cost: dict[str, pd.DataFrame] = {}

    def record(label: str, key: str, weights: pd.DataFrame, costs_to_use=tuple(COSTS)) -> None:
        for cost in costs_to_use:
            for period, (start, end) in periods(config).items():
                equity = simulate(data, weights, cost, config, start, end)
                values = equity_metrics(equity.equity)
                rolling = rolling_diagnostics(equity.equity)
                metrics_rows.append({
                    "label": label,
                    "key": key,
                    "scenario": cost.name,
                    "period": period,
                    **values,
                    "annual_turnover": annual_turnover(equity),
                    "average_exposure": float(equity.exposure.mean()),
                    "total_costs": float(equity.costs.sum()),
                    "final_equity": float(equity.equity.iloc[-1]),
                    **rolling,
                })
                if label == "v4_selected_ensemble" and period == "research_holdout":
                    holdout_by_cost[cost.name] = equity
            full = simulate(data, weights, cost, config, config.evaluation_start, config.end_exclusive)
            yearly_rows.extend(annual_rows(full.equity, label, cost.name))

    ensemble_key = "+".join(spec.key for spec in selected_specs)
    record("v4_selected_ensemble", ensemble_key, ensemble)
    for spec, frame in zip(selected_specs, selected_frames):
        record("selected_process_component", spec.key, frame, (base, stress))
    for family, frame in families.items():
        record("family_expert", family, frame, (base, stress))

    metrics = pd.DataFrame(metrics_rows)
    metrics.to_csv(output / "metrics.csv", index=False)
    yearly = pd.DataFrame(yearly_rows)
    yearly.to_csv(output / "yearly.csv", index=False)
    (output / "selected_specs.json").write_text(
        json.dumps([asdict(spec) for spec in selected_specs], indent=2), encoding="utf-8",
    )

    base_holdout = metrics[(metrics.label == "v4_selected_ensemble") & (metrics.scenario == "base") & (metrics.period == "research_holdout")].iloc[0]
    stress_holdout = metrics[(metrics.label == "v4_selected_ensemble") & (metrics.scenario == "stress") & (metrics.period == "research_holdout")].iloc[0]
    severe_holdout = metrics[(metrics.label == "v4_selected_ensemble") & (metrics.scenario == "severe") & (metrics.period == "research_holdout")].iloc[0]
    validation_stress = metrics[(metrics.label == "v4_selected_ensemble") & (metrics.scenario == "stress") & (metrics.period == "validation")].iloc[0]
    components = metrics[(metrics.label == "selected_process_component") & (metrics.scenario == "stress") & (metrics.period == "research_holdout")]
    families_holdout = metrics[(metrics.label == "family_expert") & (metrics.scenario == "stress") & (metrics.period == "research_holdout")]

    status = "paper_forward_candidate" if (
        validation_stress.total_return > 0
        and base_holdout.total_return > 0
        and stress_holdout.total_return > 0
        and severe_holdout.total_return > -0.03
        and stress_holdout.max_drawdown > -0.25
        and stress_holdout.annual_turnover < 20
        and stress_holdout.rolling_365_positive_share >= 0.55
        and int((components.total_return > 0).sum()) >= 2
        and int((families_holdout.total_return > 0).sum()) >= 2
    ) else "rejected_or_needs_iteration"

    summary = {
        "status": status,
        "selection_uses_only_development_and_validation": True,
        "research_holdout_is_fully_untouched": False,
        "selected_processes": [spec.key for spec in selected_specs],
        "base_holdout_return": float(base_holdout.total_return),
        "stress_holdout_return": float(stress_holdout.total_return),
        "severe_holdout_return": float(severe_holdout.total_return),
        "stress_holdout_max_drawdown": float(stress_holdout.max_drawdown),
        "stress_holdout_annual_turnover": float(stress_holdout.annual_turnover),
        "stress_holdout_rolling_positive_share": float(stress_holdout.rolling_365_positive_share),
        "positive_stress_components": int((components.total_return > 0).sum()),
        "positive_stress_families": int((families_holdout.total_return > 0).sum()),
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    for name, equity in holdout_by_cost.items():
        equity.to_csv(output / f"holdout_{name}_equity.csv")
    holdout_start = pd.Timestamp(config.validation_end, tz="UTC")
    benchmark = data.close.loc[data.close.index >= holdout_start]
    benchmark = benchmark.div(benchmark.iloc[0]).mean(axis=1) * config.starting_equity
    save_equity_plot(holdout_by_cost["base"], benchmark, "Active V4 — research holdout", output / "holdout_equity.png")
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.fill_between(holdout_by_cost["stress"].index, holdout_by_cost["stress"].drawdown, 0.0, alpha=0.4)
    ax.set_title("V4 stress drawdown — research holdout")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(output / "holdout_stress_drawdown.png", dpi=160)
    plt.close(fig)

    key_metrics = metrics[
        (metrics.label == "v4_selected_ensemble")
        & metrics.period.isin(["validation", "research_holdout"])
    ][[
        "scenario", "period", "total_return", "annualized_return", "max_drawdown",
        "sharpe", "calmar", "annual_turnover", "average_exposure",
        "rolling_365_positive_share", "rolling_365_worst",
    ]]
    component_metrics = components[["key", "total_return", "max_drawdown", "sharpe", "annual_turnover"]]
    family_metrics = families_holdout[["key", "total_return", "max_drawdown", "sharpe", "annual_turnover"]]
    report = [
        "# Active V4 — walk-forward family ensemble",
        "",
        f"Статус: **{status}**.",
        "",
        "## Что изменилось после V3",
        "",
        "- Вместо одной найденной конфигурации используются четыре независимых семейства простых правил.",
        "- Параметры внутри семейства усредняются заранее; отдельная лучшая настройка не выбирается.",
        "- Walk-forward варианты ранжируют только семейства и только по уже завершённому прошлому окну.",
        "- Исполнение происходит на следующем дневном open с полным учётом overnight-движения.",
        "- Отбор процесса использует development и validation при 10 и 20 б.п. на сторону; 2025–2026 не участвует в ранжировании.",
        "",
        "## Выбранные процессы",
        "",
        selected.to_markdown(index=False),
        "",
        "## Ключевые метрики ансамбля",
        "",
        key_metrics.to_markdown(index=False),
        "",
        "## Компоненты на research holdout, stress costs",
        "",
        component_metrics.to_markdown(index=False),
        "",
        "## Семейства на research holdout, stress costs",
        "",
        family_metrics.to_markdown(index=False),
        "",
        "## Ограничения",
        "",
        "- Research holdout уже затрагивался предыдущими итерациями и больше не считается полностью нетронутым.",
        "- Положительный результат допускает только фиксированный paper-forward тест.",
        "- Плечо, short, perpetual futures и перенос защищённого капитала в стратегию отсутствуют.",
    ]
    (output / "REPORT_RU.md").write_text("\n".join(report), encoding="utf-8")

    provenance = {
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": file_sha256(path)}
            for path in sorted(output.iterdir()) if path.is_file() and path.name != "provenance.json"
        }
    }
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
