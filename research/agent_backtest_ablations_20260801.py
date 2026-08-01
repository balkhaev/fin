#!/usr/bin/env python3
"""Isolated parameter ablations for DYN-IV113 and DS-40/180.

The script changes module constants only inside this process, downloads one
immutable input snapshot per strategy, and never writes a paper account.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

INITIAL_NAV_USD = 10_000.0
TROPICAL_YEAR_DAYS = 365.2425
WINDOW_START = date(2024, 7, 31)
WINDOW_END = date(2026, 7, 31)


def metrics(daily: list[dict[str, Any]]) -> dict[str, Any]:
    if not daily:
        raise ValueError("empty daily ledger")
    ordered = sorted(daily, key=lambda item: str(item["date"]))
    start = date.fromisoformat(str(ordered[0]["date"]))
    end = date.fromisoformat(str(ordered[-1]["date"]))
    years = max((end - start).days / TROPICAL_YEAR_DAYS, 1 / TROPICAL_YEAR_DAYS)
    ending_nav = float(ordered[-1]["navUsd"])
    multiple = ending_nav / INITIAL_NAV_USD
    returns = [float(item["return"]) for item in ordered]
    mean = statistics.fmean(returns)
    deviation = statistics.stdev(returns) if len(returns) > 1 else 0.0
    downside = [min(0.0, value) for value in returns]
    downside_deviation = math.sqrt(statistics.fmean(value * value for value in downside))
    peak = INITIAL_NAV_USD
    max_drawdown = 0.0
    nav_before = INITIAL_NAV_USD
    trade_cost_usd = 0.0
    carry_usd = 0.0
    for item in ordered:
        trade_cost = float(item.get("tradeCost") or 0.0)
        carry = float(item.get("financingCost") or 0.0) - float(
            item.get("fundingReturn") or 0.0
        )
        trade_cost_usd += nav_before * trade_cost
        carry_usd += nav_before * carry
        nav = float(item["navUsd"])
        peak = max(peak, nav)
        max_drawdown = min(max_drawdown, nav / peak - 1.0)
        nav_before = nav
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "observations": len(ordered),
        "ending_nav_usd": ending_nav,
        "total_return_percent": (multiple - 1.0) * 100.0,
        "cagr_percent": (multiple ** (1.0 / years) - 1.0) * 100.0,
        "annualized_volatility_percent": deviation * math.sqrt(365.0) * 100.0,
        "sharpe": mean / deviation * math.sqrt(365.0) if deviation > 0 else None,
        "sortino": (
            mean / downside_deviation * math.sqrt(365.0)
            if downside_deviation > 0
            else None
        ),
        "max_drawdown_percent": max_drawdown * 100.0,
        "calmar": (
            ((multiple ** (1.0 / years) - 1.0) / abs(max_drawdown))
            if max_drawdown < 0
            else None
        ),
        "trade_cost_usd_approx": trade_cost_usd,
        "carry_cost_usd_approx": carry_usd,
        "average_gross_exposure": statistics.fmean(
            float(item.get("heldGrossExposure") or item.get("grossExposure") or 0.0)
            for item in ordered
        ),
    }


def no_trade_band(targets: list[list[float]], band: float) -> list[list[float]]:
    if not targets:
        return []
    held = [0.0] * len(targets[0])
    output: list[list[float]] = []
    for row in targets:
        held = [
            old if abs(new - old) < band else float(new)
            for old, new in zip(held, row, strict=True)
        ]
        output.append(list(held))
    return output


def periodic_hold(
    targets: list[list[float]], dates: list[str], *, every_days: int
) -> list[list[float]]:
    if not targets:
        return []
    held = [0.0] * len(targets[0])
    output: list[list[float]] = []
    last_update: date | None = None
    for row, date_text in zip(targets, dates, strict=True):
        observed = date.fromisoformat(date_text)
        if last_update is None or (observed - last_update).days >= every_days:
            held = list(row)
            last_update = observed
        output.append(list(held))
    return output


def scale_gross(targets: list[list[float]], maximum: float) -> list[list[float]]:
    output: list[list[float]] = []
    for row in targets:
        gross = sum(abs(value) for value in row)
        scale = min(1.0, maximum / gross) if gross > 0 else 1.0
        output.append([value * scale for value in row])
    return output


def shift_targets(targets: list[list[float]], days: int) -> list[list[float]]:
    if not targets:
        return []
    zeros = [0.0] * len(targets[0])
    return [list(zeros) for _ in range(days)] + [list(row) for row in targets[:-days]]


def dyn_ablation() -> dict[str, Any]:
    from finruntime.observability.backtest_runner import (
        WARMUP_DAYS,
        _input_sha256,
        load_binance_daily_histories,
    )
    from finruntime.strategies import dyn_paper

    history_start = WINDOW_START - timedelta(days=WARMUP_DAYS)
    histories, failures, requests = load_binance_daily_histories(
        dyn_paper.MARKET_SYMBOLS, history_start, WINDOW_END
    )
    original = {
        "TARGET_VOLATILITY": dyn_paper.TARGET_VOLATILITY,
        "MAXIMUM_GROSS": dyn_paper.MAXIMUM_GROSS,
        "ASSET_CAP": dyn_paper.ASSET_CAP,
        "EXECUTION_COST": dyn_paper.EXECUTION_COST,
        "FINANCING_ANNUAL": dyn_paper.FINANCING_ANNUAL,
    }
    variants: dict[str, dict[str, float]] = {
        "baseline": {},
        "target_vol_50": {"TARGET_VOLATILITY": 0.50},
        "gross_cap_2_0": {"MAXIMUM_GROSS": 2.0},
        "gross_cap_1_5": {"MAXIMUM_GROSS": 1.5},
        "asset_cap_0_50": {"ASSET_CAP": 0.50},
        "execution_cost_50bps": {"EXECUTION_COST": 0.005},
        "execution_cost_100bps": {"EXECUTION_COST": 0.010},
        "financing_40pct": {"FINANCING_ANNUAL": 0.40},
        "conservative_combo": {
            "TARGET_VOLATILITY": 0.50,
            "MAXIMUM_GROSS": 1.50,
            "ASSET_CAP": 0.50,
            "EXECUTION_COST": 0.005,
            "FINANCING_ANNUAL": 0.30,
        },
    }
    reset_date = (WINDOW_START - timedelta(days=1)).isoformat()
    results: dict[str, Any] = {}
    try:
        for name, changes in variants.items():
            for key, value in original.items():
                setattr(dyn_paper, key, value)
            for key, value in changes.items():
                setattr(dyn_paper, key, value)
            engine = dyn_paper.build_engine(histories, failures)
            continuation = dyn_paper.paper_continuation(
                engine, reset_date=reset_date, initial_nav_usd=INITIAL_NAV_USD
            )
            daily = [
                item
                for item in continuation["daily"]
                if WINDOW_START.isoformat() <= str(item["date"]) <= WINDOW_END.isoformat()
            ]
            result_metrics = metrics(daily)
            result_metrics["executions"] = len(continuation["executions"])
            result_metrics["average_engine_leverage"] = statistics.fmean(
                float(value) for value in engine["leverage"] if math.isfinite(float(value))
            )
            results[name] = {
                "parameters": {**original, **changes},
                "metrics": result_metrics,
            }

        for key, value in original.items():
            setattr(dyn_paper, key, value)
        baseline_engine = dyn_paper.build_engine(histories, failures)
        target_variants = {
            "target_band_2pct": no_trade_band(baseline_engine["target"], 0.02),
            "target_band_5pct": no_trade_band(baseline_engine["target"], 0.05),
            "rebalance_7d": periodic_hold(
                baseline_engine["target"], baseline_engine["dates"], every_days=7
            ),
            "extra_execution_delay_1d": shift_targets(baseline_engine["target"], 1),
        }
        for name, targets in target_variants.items():
            engine = {**baseline_engine, "target": targets}
            continuation = dyn_paper.paper_continuation(
                engine, reset_date=reset_date, initial_nav_usd=INITIAL_NAV_USD
            )
            daily = [
                item
                for item in continuation["daily"]
                if WINDOW_START.isoformat() <= str(item["date"]) <= WINDOW_END.isoformat()
            ]
            result_metrics = metrics(daily)
            result_metrics["executions"] = len(continuation["executions"])
            results[name] = {"parameters": {"target_transform": name}, "metrics": result_metrics}

        leave_one_out: dict[str, Any] = {}
        for removed in tuple(engine_history["asset"] for engine_history in histories):
            reduced = [item for item in histories if item["asset"] != removed]
            reduced_failures = [*failures, {"symbol": f"{removed}USDT", "reason": "audit leave-one-out"}]
            engine = dyn_paper.build_engine(reduced, reduced_failures)
            continuation = dyn_paper.paper_continuation(
                engine, reset_date=reset_date, initial_nav_usd=INITIAL_NAV_USD
            )
            daily = [
                item
                for item in continuation["daily"]
                if WINDOW_START.isoformat() <= str(item["date"]) <= WINDOW_END.isoformat()
            ]
            leave_one_out[removed] = metrics(daily)
    finally:
        for key, value in original.items():
            setattr(dyn_paper, key, value)
    return {
        "input_sha256": _input_sha256(histories),
        "market_requests": requests,
        "usable_assets": [item["asset"] for item in histories],
        "failures": failures,
        "variants": results,
        "leave_one_asset_out": leave_one_out,
    }


def ds_ablation() -> dict[str, Any]:
    from finruntime.strategies._ds40180_account import paper_continuation
    from finruntime.strategies.ds40180_t50c3_paper import (
        _input_digest,
        build_engine,
        load_market_data,
    )

    histories, failures = load_market_data(reset_date="2024-01-01")
    engine = build_engine(histories, failures)
    market_dates = list(engine["marketDates"])
    reset_date = market_dates[365]
    risk2_targets: list[list[float]] = []
    for index, row in enumerate(engine["target"]):
        scale = min(1.0, 2.0 / float(engine["riskScale"][index]))
        risk2_targets.append([value * scale for value in row])
    variants: dict[str, list[list[float]]] = {
        "baseline": deepcopy(engine["target"]),
        "band_0_25pct": no_trade_band(engine["target"], 0.0025),
        "band_0_50pct": no_trade_band(engine["target"], 0.0050),
        "band_1pct": no_trade_band(engine["target"], 0.0100),
        "band_2pct": no_trade_band(engine["target"], 0.0200),
        "rebalance_7d": periodic_hold(engine["target"], engine["dates"], every_days=7),
        "rebalance_14d": periodic_hold(engine["target"], engine["dates"], every_days=14),
        "gross_cap_1_0": scale_gross(engine["target"], 1.0),
        "risk_scale_cap_2": risk2_targets,
        "risk2_band_0_5pct": no_trade_band(risk2_targets, 0.005),
        "extra_execution_delay_1d": shift_targets(engine["target"], 1),
    }
    results: dict[str, Any] = {}
    for name, targets in variants.items():
        variant_engine = {**engine, "target": targets}
        continuation = paper_continuation(
            variant_engine,
            histories,
            reset_date=reset_date,
            initial_nav_usd=INITIAL_NAV_USD,
        )
        result_metrics = metrics(continuation["daily"])
        result_metrics.update(
            {
                "executions": len(continuation["executions"]),
                "funding_actual_intervals": continuation["fundingActualIntervals"],
                "funding_fallback_intervals": continuation["fundingFallbackIntervals"],
            }
        )
        results[name] = {"metrics": result_metrics}
    return {
        "input_sha256": _input_digest(histories, engine["marketDates"]),
        "reset_date": reset_date,
        "assets": engine["assets"],
        "failures": failures,
        "history_rows": len(market_dates),
        "variants": results,
    }


def capture(function: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return {"status": "ok", "result": function()}
    except Exception as error:  # noqa: BLE001 - preserve audit failure
        import traceback

        return {
            "status": "error",
            "error_type": type(error).__name__,
            "error": str(error),
            "traceback": traceback.format_exc(),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = {
        "schema_version": 1,
        "window": {"start": WINDOW_START.isoformat(), "end": WINDOW_END.isoformat()},
        "dyn_iv113": capture(dyn_ablation),
        "ds40180": capture(ds_ablation),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
