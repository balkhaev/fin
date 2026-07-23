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

from config import COSTS, Costs, ResearchConfig, RotationParams, ShockParams, rotation_grid, shock_grid
from data import load_all
from metrics import equity_metrics
from rotation import (
    RotationCache,
    ensemble_weights,
    evaluate_rotation_grid,
    neighbor_count as rotation_neighbor_count,
    simulate_weights,
    target_weights,
)
from shock import (
    ShockCache,
    compact_trade_metrics,
    evaluate_shock_grid,
    neighbor_count as shock_neighbor_count,
    portfolio_backtest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Active crypto research v2")
    parser.add_argument("--output", type=Path, default=Path("artifacts/active_v2"))
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


def instantiate_rotation(row: pd.Series) -> RotationParams:
    return RotationParams(
        fast_days=int(row.fast_days), slow_days=int(row.slow_days), ema_days=int(row.ema_days),
        vol_days=int(row.vol_days), target_vol=float(row.target_vol),
        rebalance_hours=int(row.rebalance_hours), hysteresis=float(row.hysteresis),
    )


def instantiate_shock(row: pd.Series) -> ShockParams:
    return ShockParams(
        shock_bars=int(row.shock_bars), z_threshold=float(row.z_threshold),
        trend_ema_days=int(row.trend_ema_days), bar_location=float(row.bar_location),
        taker_ratio=float(row.taker_ratio), volume_ratio=float(row.volume_ratio),
        stop_atr=float(row.stop_atr), target_r=float(row.target_r),
        max_hold_bars=int(row.max_hold_bars),
    )


def diverse_top(results: pd.DataFrame, columns: list[str], limit: int = 3) -> pd.DataFrame:
    viable = results[(results["robust_score"] > -1e8) & (results["neighbor_count"] >= 2)]
    if viable.empty:
        viable = results[results["robust_score"] > -1e8]
    if viable.empty:
        viable = results.head(limit)
    selected: list[int] = []
    for index, row in viable.iterrows():
        if not selected:
            selected.append(index)
        elif all(sum(row[c] != viable.loc[other, c] for c in columns) >= 2 for other in selected):
            selected.append(index)
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        for index in viable.index:
            if index not in selected:
                selected.append(index)
            if len(selected) >= limit:
                break
    return viable.loc[selected].copy().reset_index(drop=True)


def add_rotation_metrics(
    rows: list[dict[str, object]], cache: RotationCache, weights: pd.DataFrame,
    family: str, key: str, cost: Costs, config: ResearchConfig,
    period_name: str, start: str, end: str,
) -> pd.DataFrame:
    equity = simulate_weights(cache, weights, cost, config, start, end)
    values = equity_metrics(equity["equity"])
    rows.append({
        "family": family, "key": key, "scenario": cost.name, "period": period_name,
        "start": start, "end": end, **values, "trades": np.nan,
        "profit_factor": np.nan, "win_rate": np.nan,
        "turnover": float(equity["turnover"].sum()),
        "average_exposure": float(equity["exposure"].mean()),
        "final_equity": float(equity["equity"].iloc[-1]),
    })
    return equity


def add_shock_metrics(
    rows: list[dict[str, object]], cache: ShockCache, params: ShockParams,
    family: str, cost: Costs, config: ResearchConfig,
    period_name: str, start: str, end: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    equity, trades = portfolio_backtest(cache, params, cost, config, start, end)
    values = equity_metrics(equity["equity"])
    compact = compact_trade_metrics([]) if trades.empty else {
        "trades": int(len(trades)),
        "profit_factor": float(
            trades.loc[trades.net_pnl > 0, "net_pnl"].sum()
            / abs(trades.loc[trades.net_pnl < 0, "net_pnl"].sum())
        ) if (trades.net_pnl < 0).any() else np.inf,
        "win_rate": float((trades.net_pnl > 0).mean()),
        "average_r": float(trades.r_multiple.mean()),
    }
    rows.append({
        "family": family, "key": params.key, "scenario": cost.name, "period": period_name,
        "start": start, "end": end, **values, **compact, "turnover": np.nan,
        "average_exposure": float(equity["exposure"].mean()),
        "final_equity": float(equity["equity"].iloc[-1]),
    })
    return equity, trades


def save_equity_plot(rotation: pd.DataFrame, benchmark: pd.Series, output: Path, title: str) -> None:
    benchmark = benchmark.reindex(rotation.index).ffill().dropna()
    benchmark = benchmark / benchmark.iloc[0] * rotation["equity"].iloc[0]
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(rotation.index, rotation["equity"], label="rotation ensemble")
    ax.plot(benchmark.index, benchmark, label="50/50 BTC-ETH buy & hold")
    ax.set_title(title)
    ax.set_ylabel("USDT")
    ax.grid(True, alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(output, dpi=160)
    plt.close(fig)


def self_test() -> None:
    rng = np.random.default_rng(7)
    index = pd.date_range("2020-01-01", periods=96 * 500, freq="15min", tz="UTC")
    raw: dict[str, pd.DataFrame] = {}
    for number, symbol in enumerate(("BTCUSDT", "ETHUSDT"), start=1):
        returns = rng.normal(0.00001 * number, 0.002, len(index))
        close = 10_000 * np.exp(np.cumsum(returns))
        open_ = np.r_[close[0], close[:-1]]
        spread = np.maximum(close * rng.uniform(0.0002, 0.003, len(index)), 0.1)
        high = np.maximum(open_, close) + spread
        low = np.minimum(open_, close) - spread
        quote = rng.lognormal(14, 0.5, len(index))
        raw[symbol] = pd.DataFrame({
            "open": open_, "high": high, "low": low, "close": close,
            "volume": quote / close, "quote_volume": quote,
            "trades": rng.integers(100, 5000, len(index)),
            "taker_buy_quote": quote * rng.uniform(0.35, 0.65, len(index)),
        }, index=index)
    config = ResearchConfig(
        end_exclusive="2021-05-15", development_end="2020-09-01", validation_end="2021-02-01"
    )
    rotation_cache = RotationCache(raw)
    params = RotationParams(3, 21, 10, 14, 0.30, 12, 0.0)
    weights = target_weights(rotation_cache, params)
    equity = simulate_weights(rotation_cache, weights, COSTS[1], config, config.start, config.end_exclusive)
    assert len(equity) > 100 and np.isfinite(equity.equity).all()
    shock_cache = ShockCache(raw)
    shock = ShockParams(2, -2.5, 20, 0.55, 0.50, 1.25, 1.0, 1.0, 8)
    shock_equity, _ = portfolio_backtest(shock_cache, shock, COSTS[1], config, config.start, config.end_exclusive)
    assert len(shock_equity) > 1_000 and np.isfinite(shock_equity.equity).all()
    print("self-test passed")


def write_report(
    output: Path, config: ResearchConfig, selected_rotation: pd.DataFrame,
    selected_shock: pd.DataFrame, metrics: pd.DataFrame,
) -> dict[str, object]:
    base_rotation = metrics[
        (metrics.family == "rotation_ensemble") & (metrics.scenario == "base")
        & (metrics.period == "research_holdout")
    ]
    stress_rotation = metrics[
        (metrics.family == "rotation_ensemble") & (metrics.scenario == "stress")
        & (metrics.period == "research_holdout")
    ]
    validation_rotation = metrics[
        (metrics.family == "rotation_ensemble") & (metrics.scenario == "base")
        & (metrics.period == "validation")
    ]
    if base_rotation.empty or stress_rotation.empty or validation_rotation.empty:
        status = "incomplete"
    else:
        b, s, v = base_rotation.iloc[0], stress_rotation.iloc[0], validation_rotation.iloc[0]
        status = "promising_research_candidate" if (
            b.total_return > 0 and b.max_drawdown > -0.25
            and s.total_return > 0 and v.total_return > 0
        ) else "rejected_or_needs_iteration"
    primary_shock = metrics[
        (metrics.family == "shock_primary") & (metrics.scenario == "base")
        & (metrics.period == "research_holdout")
    ]
    shock_status = "not_selected"
    if not primary_shock.empty:
        row = primary_shock.iloc[0]
        shock_status = "promising" if row.total_return > 0 and row.profit_factor > 1 else "research_only"
    key_metrics = metrics[
        metrics.family.isin(["rotation_ensemble", "shock_primary"])
        & metrics.period.isin(["validation", "research_holdout"])
    ][[
        "family", "scenario", "period", "total_return", "annualized_return",
        "max_drawdown", "sharpe", "calmar", "trades", "profit_factor", "turnover",
    ]]
    lines = [
        "# Active research v2 — результат", "",
        f"Статус основного процесса: **{status}**.",
        f"Статус 15-минутного shock-reversal: **{shock_status}**.", "",
        "## Методология", "",
        f"- Данные: Binance Spot 15m, {', '.join(config.symbols)}, {config.start} — {config.end_exclusive}.",
        f"- Development: {config.start} — {config.development_end}.",
        f"- Validation: {config.development_end} — {config.validation_end}.",
        f"- Research holdout: {config.validation_end} — {config.end_exclusive}.",
        "- Holdout не называется полностью нетронутым: рынок 2025–2026 уже использовался в AIMR v1 на уровне общей диагностики.",
        "- Параметры выбирались только по development и validation; holdout не участвовал в ранжировании.",
        "- Базовые расходы: 10 б.п. на сторону; стресс: 20 б.п. на сторону.", "",
        "## Выбранные rotation-конфигурации", "", selected_rotation.to_markdown(index=False), "",
        "## Выбранные shock-конфигурации", "", selected_shock.to_markdown(index=False), "",
        "## Ключевые метрики", "", key_metrics.to_markdown(index=False), "",
        "## Ограничения", "",
        "- Rotation — активная системная торговля на 4h, а не скальпинг; это сознательное снижение оборота после провала AIMR v1.",
        "- Shock-модуль использует свечной taker-buy proxy, но не знает bid/ask, очередь и L2 adverse selection.",
        "- Исторически положительный holdout остаётся исследовательским результатом, а не разрешением на реальный капитал.",
        "- Следующий обязательный этап для принятого кандидата — paper-forward без изменения параметров.",
    ]
    (output / "REPORT_RU.md").write_text("\n".join(lines), encoding="utf-8")
    summary = {"status": status, "shock_status": shock_status}
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    output: Path = args.output
    output.mkdir(parents=True, exist_ok=True)
    config = ResearchConfig()
    base_cost = next(cost for cost in COSTS if cost.name == "base")
    raw, manifest, quality = load_all(config, args.cache, args.refresh)
    pd.DataFrame(manifest).to_csv(output / "data_manifest.csv", index=False)
    pd.DataFrame(quality).to_csv(output / "data_quality.csv", index=False)
    (output / "config.json").write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")

    print("building rotation cache")
    rotation_cache = RotationCache(raw)
    rotation_results = evaluate_rotation_grid(rotation_cache, rotation_grid(), base_cost, config)
    rotation_results["neighbor_count"] = rotation_neighbor_count(rotation_results)
    rotation_results.to_csv(output / "rotation_search.csv", index=False)
    rotation_columns = [
        "fast_days", "slow_days", "ema_days", "vol_days", "target_vol", "rebalance_hours", "hysteresis"
    ]
    selected_rotation = diverse_top(rotation_results, rotation_columns, limit=3)
    selected_rotation.to_csv(output / "rotation_selected.csv", index=False)
    rotation_params = [instantiate_rotation(row) for _, row in selected_rotation.iterrows()]
    rotation_weight_frames = [target_weights(rotation_cache, params) for params in rotation_params]
    rotation_ensemble = ensemble_weights(rotation_weight_frames)

    print("building shock cache")
    shock_cache = ShockCache(raw)
    shock_results = evaluate_shock_grid(shock_cache, shock_grid(), base_cost, config)
    shock_results["neighbor_count"] = shock_neighbor_count(shock_results)
    shock_results.to_csv(output / "shock_search.csv", index=False)
    shock_columns = [
        "shock_bars", "z_threshold", "trend_ema_days", "bar_location", "taker_ratio",
        "volume_ratio", "stop_atr", "target_r", "max_hold_bars",
    ]
    selected_shock = diverse_top(shock_results, shock_columns, limit=3)
    selected_shock.to_csv(output / "shock_selected.csv", index=False)
    shock_params = [instantiate_shock(row) for _, row in selected_shock.iterrows()]

    metric_rows: list[dict[str, object]] = []
    saved_rotation_holdout: pd.DataFrame | None = None
    saved_shock_holdout: tuple[pd.DataFrame, pd.DataFrame] | None = None
    for cost in COSTS:
        for period_name, (start, end) in periods(config).items():
            equity = add_rotation_metrics(
                metric_rows, rotation_cache, rotation_ensemble, "rotation_ensemble",
                "+".join(params.key for params in rotation_params),
                cost, config, period_name, start, end,
            )
            if cost.name == "base" and period_name == "research_holdout":
                saved_rotation_holdout = equity
            if shock_params:
                shock_equity, shock_trades = add_shock_metrics(
                    metric_rows, shock_cache, shock_params[0], "shock_primary",
                    cost, config, period_name, start, end,
                )
                if cost.name == "base" and period_name == "research_holdout":
                    saved_shock_holdout = shock_equity, shock_trades
    for params, weights in zip(rotation_params, rotation_weight_frames):
        for period_name, (start, end) in periods(config).items():
            add_rotation_metrics(
                metric_rows, rotation_cache, weights, "rotation_component", params.key,
                base_cost, config, period_name, start, end,
            )
    metrics = pd.DataFrame(metric_rows)
    metrics.to_csv(output / "metrics.csv", index=False)
    (output / "selected_params.json").write_text(json.dumps({
        "rotation": [asdict(params) for params in rotation_params],
        "shock": [asdict(params) for params in shock_params],
    }, indent=2), encoding="utf-8")
    if saved_rotation_holdout is not None:
        saved_rotation_holdout.to_csv(output / "rotation_holdout_equity.csv")
        benchmark = rotation_cache.close.div(rotation_cache.close.iloc[0]).mean(axis=1)
        save_equity_plot(
            saved_rotation_holdout, benchmark, output / "rotation_holdout_equity.png",
            "Active v2 rotation — research holdout",
        )
        fig, ax = plt.subplots(figsize=(12, 4))
        ax.fill_between(saved_rotation_holdout.index, saved_rotation_holdout.drawdown, 0.0, alpha=0.4)
        ax.set_title("Rotation ensemble drawdown — research holdout")
        ax.grid(True, alpha=0.25)
        fig.tight_layout()
        fig.savefig(output / "rotation_holdout_drawdown.png", dpi=160)
        plt.close(fig)
    if saved_shock_holdout is not None:
        shock_equity, shock_trades = saved_shock_holdout
        shock_equity.to_csv(output / "shock_holdout_equity.csv")
        shock_trades.to_csv(output / "shock_holdout_trades.csv", index=False)
    summary = write_report(output, config, selected_rotation, selected_shock, metrics)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
