#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from config import Config, COSTS, PERIODS, process_grid
from data import load
from market import Market
from metrics import metrics, rolling
from strategy import family_library, process_frame, simulate


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Active V9 fixed-universe USD-M cross-sectional research")
    parser.add_argument("--cache", type=Path, default=Path(".cache/v9"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/v9"))
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def self_test() -> None:
    index = pd.date_range("2020-01-01", periods=900, freq="1D", tz="UTC")
    rng = np.random.default_rng(9)
    klines: dict[str, pd.DataFrame] = {}
    funding: dict[str, pd.Series] = {}
    for number, symbol in enumerate(Config().symbols[:7]):
        returns = rng.normal(0.0002 * (number - 3), 0.02, len(index))
        close = 100 * np.exp(np.cumsum(returns))
        open_ = np.r_[close[0], close[:-1] * (1 + rng.normal(0, 0.003, len(index) - 1))]
        klines[symbol] = pd.DataFrame(
            {
                "open": open_,
                "high": np.maximum(open_, close) * 1.01,
                "low": np.minimum(open_, close) * 0.99,
                "close": close,
                "volume": 1.0,
                "quote_volume": 1.0,
                "trades": 1.0,
                "taker_buy_base": 0.5,
                "taker_buy_quote": 0.5,
            },
            index=index,
        )
        funding[symbol] = pd.Series(rng.normal(0, 0.0001, len(index)), index=index)
    market = Market(klines, funding)
    families, counts = family_library(market)
    assert counts == {
        "xs_momentum": 144,
        "residual_momentum": 144,
        "anchor": 72,
        "funding_momentum": 216,
    }
    assert all(np.isfinite(frame.to_numpy()).all() for frame in families.values())
    weights = next(iter(families.values()))
    account = simulate(
        market,
        weights,
        "2021-01-01",
        "2022-01-01",
        0.004,
        Config(symbols=tuple(market.symbols), start="2020-01-01", end_exclusive="2022-01-01"),
    )
    assert np.isfinite(account.equity).all()
    assert float(account.gross.max()) <= 0.90
    changed = {symbol: frame.copy() for symbol, frame in klines.items()}
    first = next(iter(changed))
    changed[first].iloc[-1, changed[first].columns.get_loc("close")] *= 4
    changed_market = Market(changed, funding)
    original_score = market.close.pct_change(30, fill_method=None).div(market.vol.replace(0, np.nan))
    changed_score = changed_market.close.pct_change(30, fill_method=None).div(changed_market.vol.replace(0, np.nan))
    original_weights = select_test_weights(market, original_score)
    changed_weights = select_test_weights(changed_market, changed_score)
    pd.testing.assert_frame_equal(original_weights.iloc[:-1], changed_weights.iloc[:-1])
    print("self-test passed", counts)


def select_test_weights(market: Market, score: pd.DataFrame) -> pd.DataFrame:
    from strategy import select_unit_weights
    return select_unit_weights(market, score, 2, 2, "beta")


def robust_process_score(severe_rows: list[dict[str, float]]) -> float:
    returns = [row["total_return"] for row in severe_rows]
    drawdowns = [row["max_drawdown"] for row in severe_rows]
    turnovers = [row["annual_turnover"] for row in severe_rows]
    sharpes = [row["sharpe"] for row in severe_rows]
    if sum(value > 0 for value in returns) < 3:
        return -1e9
    if min(returns) <= -0.10 or min(drawdowns) <= -0.30 or max(turnovers) >= 35:
        return -1e9
    if not all(np.isfinite(sharpes)):
        return -1e9
    return float(min(returns) + np.median(returns) + 0.20 * min(sharpes) - 0.01 * max(turnovers))


def select_diverse(search: pd.DataFrame, limit: int = 3) -> list[str]:
    viable = search[search.score > -1e8]
    if viable.empty:
        return search.head(limit).key.tolist()
    fields = ("kind", "subset", "train_days", "selection_days", "top_k", "score_mode")
    selected_indices: list[int] = []
    for index, row in viable.iterrows():
        if not selected_indices:
            selected_indices.append(index)
        else:
            distances = [sum(row[field] != viable.loc[other, field] for field in fields) for other in selected_indices]
            if min(distances) >= 2:
                selected_indices.append(index)
        if len(selected_indices) == limit:
            break
    for index in viable.index:
        if len(selected_indices) == limit:
            break
        if index not in selected_indices:
            selected_indices.append(index)
    return viable.loc[selected_indices].key.tolist()


def block_bootstrap(equity: pd.Series, seed: int = 20260724, simulations: int = 5000, block_days: int = 30) -> dict[str, float]:
    returns = equity.pct_change().dropna().to_numpy(float)
    horizon = 365
    rng = np.random.default_rng(seed)
    totals = np.empty(simulations)
    drawdowns = np.empty(simulations)
    hit_20_before_minus_10 = np.zeros(simulations, dtype=bool)
    for simulation in range(simulations):
        path_parts: list[np.ndarray] = []
        while sum(len(part) for part in path_parts) < horizon:
            start = int(rng.integers(0, max(1, len(returns) - block_days + 1)))
            path_parts.append(returns[start : start + block_days])
        sampled = np.concatenate(path_parts)[:horizon]
        curve = np.cumprod(1 + sampled)
        totals[simulation] = curve[-1] - 1
        drawdowns[simulation] = np.min(curve / np.maximum.accumulate(curve) - 1)
        positive_hit = np.flatnonzero(curve >= 1.20)
        negative_hit = np.flatnonzero(curve <= 0.90)
        positive_time = positive_hit[0] if len(positive_hit) else horizon + 1
        negative_time = negative_hit[0] if len(negative_hit) else horizon + 1
        hit_20_before_minus_10[simulation] = positive_time < negative_time
    return {
        "simulations": simulations,
        "block_days": block_days,
        "positive_probability": float((totals > 0).mean()),
        "above_20_probability": float((totals > 0.20).mean()),
        "median_return": float(np.median(totals)),
        "p05_return": float(np.quantile(totals, 0.05)),
        "median_max_drawdown": float(np.median(drawdowns)),
        "p05_max_drawdown": float(np.quantile(drawdowns, 0.05)),
        "hit_20_before_minus_10_probability": float(hit_20_before_minus_10.mean()),
    }


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parsed = arguments()
    if parsed.self_test:
        self_test()
        return 0

    config = Config()
    output = parsed.output
    output.mkdir(parents=True, exist_ok=True)
    klines, funding, manifest, quality = load(config, parsed.cache, parsed.refresh)
    pd.DataFrame(manifest).to_csv(output / "data_manifest.csv", index=False)
    pd.DataFrame(quality).to_csv(output / "data_quality.csv", index=False)
    market = Market(klines, funding)
    families, counts = family_library(market)
    pd.DataFrame([
        {
            "family": name,
            "variants": counts[name],
            "average_gross": float(frame.abs().sum(axis=1).mean()),
            "max_target_gross": float(frame.abs().sum(axis=1).max()),
        }
        for name, frame in families.items()
    ]).to_csv(output / "family_library.csv", index=False)

    precomputed_accounts = {
        (name, scenario): simulate(market, frame, config.start, config.end_exclusive, COSTS[scenario], config)
        for name, frame in families.items()
        for scenario in ("stress", "severe")
    }

    search_rows: list[dict[str, object]] = []
    process_frames: dict[str, pd.DataFrame] = {}
    selection_logs: list[dict[str, object]] = []
    for process in process_grid():
        weights, logs = process_frame(market, families, process, config, COSTS, accounts=precomputed_accounts)
        process_frames[process.key] = weights
        selection_logs.extend(logs)
        row: dict[str, object] = {"key": process.key, **asdict(process)}
        severe_rows: list[dict[str, float]] = []
        for period in ("development", "validation_a", "validation_b", "bridge_2025"):
            for scenario in ("stress", "severe"):
                result = metrics(simulate(market, weights, *PERIODS[period], COSTS[scenario], config))
                if scenario == "severe": severe_rows.append(result)
                for key, value in result.items(): row[f"{period}_{scenario}_{key}"] = value
        row["score"] = robust_process_score(severe_rows)
        search_rows.append(row)
    search = pd.DataFrame(search_rows).sort_values("score", ascending=False).reset_index(drop=True)
    search.to_csv(output / "process_search.csv", index=False)
    pd.DataFrame(selection_logs).to_csv(output / "walkforward_selection_log.csv", index=False)

    selected = select_diverse(search, 3)
    selected_rows = search[search.key.isin(selected)].copy()
    selected_rows.to_csv(output / "selected_processes.csv", index=False)
    ensemble = sum(process_frames[key] for key in selected) / len(selected)
    ensemble.to_csv(output / "frozen_signal.csv")

    metric_rows: list[dict[str, object]] = []
    account_cache: dict[tuple[str, str], pd.DataFrame] = {}
    for scenario, rate in COSTS.items():
        for period, (start, end) in PERIODS.items():
            account = simulate(market, ensemble, start, end, rate, config)
            account_cache[(scenario, period)] = account
            metric_rows.append({"label": "v9_ensemble", "scenario": scenario, "period": period, **metrics(account), **rolling(account.equity)})
            if period in ("full", "final_2026h1"): account.to_csv(output / f"{scenario}_{period}_equity.csv")
    for key in selected:
        for scenario in ("stress", "severe"):
            for period in ("development", "validation_a", "validation_b", "bridge_2025", "final_2026h1"):
                account = simulate(market, process_frames[key], *PERIODS[period], COSTS[scenario], config)
                metric_rows.append({"label": "selected_component", "key": key, "scenario": scenario, "period": period, **metrics(account), **rolling(account.equity)})
    for name, frame in families.items():
        for scenario in ("stress", "severe"):
            for period in ("bridge_2025", "final_2026h1"):
                account = simulate(market, frame, *PERIODS[period], COSTS[scenario], config)
                metric_rows.append({"label": "family", "key": name, "scenario": scenario, "period": period, **metrics(account), **rolling(account.equity)})
    metrics_frame = pd.DataFrame(metric_rows)
    metrics_frame.to_csv(output / "metrics.csv", index=False)

    severe_prefinal = metrics_frame[(metrics_frame.label == "v9_ensemble") & (metrics_frame.scenario == "severe") & metrics_frame.period.isin(["development", "validation_a", "validation_b", "bridge_2025"])]
    stress_final = metrics_frame[(metrics_frame.label == "v9_ensemble") & (metrics_frame.scenario == "stress") & (metrics_frame.period == "final_2026h1")].iloc[0]
    severe_final = metrics_frame[(metrics_frame.label == "v9_ensemble") & (metrics_frame.scenario == "severe") & (metrics_frame.period == "final_2026h1")].iloc[0]
    severe_full = metrics_frame[(metrics_frame.label == "v9_ensemble") & (metrics_frame.scenario == "severe") & (metrics_frame.period == "full")].iloc[0]
    severe_components_final = metrics_frame[(metrics_frame.label == "selected_component") & (metrics_frame.scenario == "severe") & (metrics_frame.period == "final_2026h1")]

    prefinal_positive = int((severe_prefinal.total_return > 0).sum())
    status = "frozen_paper_forward_candidate" if (
        bool((search.score > -1e8).any()) and prefinal_positive >= 3
        and float(severe_prefinal.total_return.min()) > -0.10
        and float(severe_prefinal.max_drawdown.min()) > -0.30
        and float(stress_final.total_return) > 0
        and float(severe_final.total_return) > -0.03
        and float(severe_full.total_return) > 0
        and float(severe_full.max_drawdown) > -0.30
        and float(severe_full.annual_turnover) < 35
        and int((severe_components_final.total_return > 0).sum()) >= 2
    ) else "rejected_or_needs_iteration"

    pd.DataFrame([block_bootstrap(account_cache[("severe", "full")].equity, block_days=block) for block in (14, 30, 60)]).to_csv(output / "block_bootstrap.csv", index=False)
    pd.DataFrame({key: simulate(market, process_frames[key], *PERIODS["full"], COSTS["severe"], config).equity.pct_change() for key in selected}).corr().to_csv(output / "selected_process_correlations.csv")

    summary = {
        "status": status,
        "selected_processes": selected,
        "selection_excludes_2026h1": True,
        "final_2026h1_is_program_level_pristine": False,
        "prefinal_positive_severe_periods": prefinal_positive,
        "stress_final_2026h1": stress_final.to_dict(),
        "severe_final_2026h1": severe_final.to_dict(),
        "severe_full": severe_full.to_dict(),
        "positive_severe_final_components": int((severe_components_final.total_return > 0).sum()),
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (output / "config.json").write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")

    key_metrics = metrics_frame[(metrics_frame.label == "v9_ensemble") & metrics_frame.period.isin(["development", "validation_a", "validation_b", "bridge_2025", "final_2026h1", "full"])][[
        "scenario", "period", "total_return", "annualized_return", "max_drawdown", "sharpe", "calmar", "annual_turnover", "average_gross", "max_gross", "funding_pnl", "rolling_positive_share", "rolling_worst",
    ]]
    report = [
        "# Active V9 — fixed-universe USD-M cross-sectional long/short", "", f"Статус: **{status}**.", "",
        "## Выбранные процессы", "", selected_rows.to_markdown(index=False), "",
        "## Ключевые метрики", "", key_metrics.to_markdown(index=False), "",
        "## Методологические ограничения", "",
        "- Universe зафиксирован заранее и включает слабые и делистнутые активы, но полностью устранить survivorship bias невозможно.",
        "- Funding берётся из архивов Binance; bid/ask, mark-price liquidation и очередь заявок дневными свечами не воспроизводятся.",
        "- 2026 H1 не участвует в выборе V9, но уже наблюдался на уровне предыдущих исследований и не является чистым program-level out-of-sample.",
        "- Положительный статус допускает только неизменяемый paper-forward, не реальный капитал.",
    ]
    (output / "REPORT_RU.md").write_text("\n".join(report), encoding="utf-8")
    provenance = {path.name: {"bytes": path.stat().st_size, "sha256": file_hash(path)} for path in sorted(output.iterdir()) if path.is_file() and path.name != "provenance.json"}
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
