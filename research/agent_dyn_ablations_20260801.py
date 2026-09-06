#!/usr/bin/env python3
"""Reproducible DYN-IV113 parameter and universe ablations."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import date, timedelta
from pathlib import Path
from typing import Any

INITIAL_NAV_USD = 10_000.0
START = date(2024, 7, 31)
END = date(2026, 7, 31)
TROPICAL_YEAR_DAYS = 365.2425


def _metrics(daily: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(daily, key=lambda item: str(item["date"]))
    if not ordered:
        raise ValueError("empty daily ledger")
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
    financing_usd = 0.0
    for item in ordered:
        trade_cost_usd += nav_before * float(item.get("tradeCost") or 0.0)
        financing_usd += nav_before * float(item.get("financingCost") or 0.0)
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
        "sortino": mean / downside_deviation * math.sqrt(365.0)
        if downside_deviation > 0
        else None,
        "max_drawdown_percent": max_drawdown * 100.0,
        "calmar": ((multiple ** (1.0 / years) - 1.0) / abs(max_drawdown))
        if max_drawdown < 0
        else None,
        "trade_cost_usd_approx": trade_cost_usd,
        "financing_cost_usd_approx": financing_usd,
        "average_gross_exposure": statistics.fmean(
            float(item.get("grossExposure") or 0.0) for item in ordered
        ),
    }


def _band(targets: list[list[float]], threshold: float) -> list[list[float]]:
    if not targets:
        return []
    held = [0.0] * len(targets[0])
    output: list[list[float]] = []
    for row in targets:
        held = [
            old if abs(new - old) < threshold else float(new)
            for old, new in zip(held, row, strict=True)
        ]
        output.append(list(held))
    return output


def _periodic(
    targets: list[list[float]], dates: list[str], every_days: int
) -> list[list[float]]:
    held = [0.0] * len(targets[0])
    output: list[list[float]] = []
    updated: date | None = None
    for row, date_text in zip(targets, dates, strict=True):
        observed = date.fromisoformat(date_text)
        if updated is None or (observed - updated).days >= every_days:
            held = list(row)
            updated = observed
        output.append(list(held))
    return output


def _shift(targets: list[list[float]], days: int) -> list[list[float]]:
    zeros = [0.0] * len(targets[0])
    return [list(zeros) for _ in range(days)] + [list(row) for row in targets[:-days]]


def _run_engine(engine: dict[str, Any], dyn_paper: Any, reset_date: str) -> dict[str, Any]:
    continuation = dyn_paper.paper_continuation(
        engine, reset_date=reset_date, initial_nav_usd=INITIAL_NAV_USD
    )
    daily = [
        item
        for item in continuation["daily"]
        if START.isoformat() <= str(item["date"]) <= END.isoformat()
    ]
    result = _metrics(daily)
    result["executions"] = len(continuation["executions"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    from finruntime.observability.backtest_runner import (
        WARMUP_DAYS,
        _input_sha256,
        load_binance_daily_histories,
    )
    from finruntime.strategies import dyn_paper

    histories, failures, requests = load_binance_daily_histories(
        dyn_paper.MARKET_SYMBOLS, START - timedelta(days=WARMUP_DAYS), END
    )
    original = {
        "TARGET_VOLATILITY": dyn_paper.TARGET_VOLATILITY,
        "MAXIMUM_GROSS": dyn_paper.MAXIMUM_GROSS,
        "ASSET_CAP": dyn_paper.ASSET_CAP,
        "EXECUTION_COST": dyn_paper.EXECUTION_COST,
        "FINANCING_ANNUAL": dyn_paper.FINANCING_ANNUAL,
    }
    reset_date = (START - timedelta(days=1)).isoformat()
    variants = {
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
    results: dict[str, Any] = {}
    leave_one_out: dict[str, Any] = {}
    try:
        for name, changes in variants.items():
            for key, value in original.items():
                setattr(dyn_paper, key, value)
            for key, value in changes.items():
                setattr(dyn_paper, key, value)
            engine = dyn_paper.build_engine(histories, failures)
            results[name] = {
                "parameters": {**original, **changes},
                "metrics": _run_engine(engine, dyn_paper, reset_date),
            }

        for key, value in original.items():
            setattr(dyn_paper, key, value)
        baseline_engine = dyn_paper.build_engine(histories, failures)
        target_variants = {
            "target_band_2pct": _band(baseline_engine["target"], 0.02),
            "target_band_5pct": _band(baseline_engine["target"], 0.05),
            "rebalance_7d": _periodic(
                baseline_engine["target"], baseline_engine["dates"], 7
            ),
            "extra_execution_delay_1d": _shift(baseline_engine["target"], 1),
        }
        for name, targets in target_variants.items():
            results[name] = {
                "parameters": {"target_transform": name},
                "metrics": _run_engine(
                    {**baseline_engine, "target": targets}, dyn_paper, reset_date
                ),
            }

        for removed in tuple(item["asset"] for item in histories if item["asset"] != "BTC"):
            try:
                reduced = [item for item in histories if item["asset"] != removed]
                reduced_failures = [
                    *failures,
                    {"symbol": f"{removed}USDT", "reason": "audit leave-one-out"},
                ]
                engine = dyn_paper.build_engine(reduced, reduced_failures)
                leave_one_out[removed] = {
                    "status": "ok",
                    "metrics": _run_engine(engine, dyn_paper, reset_date),
                }
            except Exception as error:  # noqa: BLE001 - retain individual failure
                leave_one_out[removed] = {
                    "status": "error",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
    finally:
        for key, value in original.items():
            setattr(dyn_paper, key, value)

    payload = {
        "schema_version": 1,
        "window": {"start": START.isoformat(), "end": END.isoformat()},
        "input_sha256": _input_sha256(histories),
        "market_requests": requests,
        "usable_assets": [item["asset"] for item in histories],
        "failures": failures,
        "variants": results,
        "leave_one_asset_out": leave_one_out,
        "btc_leave_one_out": {
            "status": "not_applicable",
            "reason": "BTC is a required regime input, so removing it changes strategy identity.",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
