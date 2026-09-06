#!/usr/bin/env python3
"""Audit order-level no-trade bands for the DS-40/180 paper ledger."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import date
from pathlib import Path
from typing import Any, Callable

INITIAL_NAV_USD = 10_000.0
TROPICAL_YEAR_DAYS = 365.2425


def _metrics(daily: list[dict[str, Any]], executions: int) -> dict[str, Any]:
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
    for item in ordered:
        nav = float(item["navUsd"])
        peak = max(peak, nav)
        max_drawdown = min(max_drawdown, nav / peak - 1.0)
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
        "trade_cost_usd": sum(float(item.get("tradeCostUsd") or 0.0) for item in ordered),
        "funding_pnl_usd": sum(float(item.get("fundingPnlUsd") or 0.0) for item in ordered),
        "average_gross_exposure": statistics.fmean(
            float(item.get("heldGrossExposure") or 0.0) for item in ordered
        ),
        "executions": executions,
        "executions_per_observation": executions / len(ordered),
    }


def _deadband_solver(
    *, threshold_weight: float, execution_cost: float
) -> Callable[..., tuple[list[float], float, float]]:
    def solve(
        *,
        nav_before_cost: float,
        target: list[float],
        prices: list[float],
        current_quantities: list[float],
    ) -> tuple[list[float], float, float]:
        if nav_before_cost <= 0:
            raise ValueError("invalid NAV")
        current_weights = [
            current_quantities[index] * prices[index] / nav_before_cost
            for index in range(len(target))
        ]
        trade_mask = [
            abs(float(target[index]) - current_weights[index]) >= threshold_weight
            for index in range(len(target))
        ]
        nav_after_cost = nav_before_cost
        desired = list(current_quantities)
        trade_cost_usd = 0.0
        for _iteration in range(20):
            desired = [
                nav_after_cost * float(target[index]) / prices[index]
                if trade_mask[index]
                else current_quantities[index]
                for index in range(len(target))
            ]
            traded_notional = sum(
                abs(desired[index] - current_quantities[index]) * prices[index]
                for index in range(len(target))
            )
            trade_cost_usd = execution_cost * traded_notional
            updated_nav = nav_before_cost - trade_cost_usd
            if updated_nav <= 0 or not math.isfinite(updated_nav):
                raise ValueError("execution costs exhausted account")
            if abs(updated_nav - nav_after_cost) <= max(1e-9, nav_before_cost * 1e-12):
                nav_after_cost = updated_nav
                break
            nav_after_cost = updated_nav
        desired = [
            nav_after_cost * float(target[index]) / prices[index]
            if trade_mask[index]
            else current_quantities[index]
            for index in range(len(target))
        ]
        traded_notional = sum(
            abs(desired[index] - current_quantities[index]) * prices[index]
            for index in range(len(target))
        )
        trade_cost_usd = execution_cost * traded_notional
        return desired, trade_cost_usd, nav_before_cost - trade_cost_usd

    return solve


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    import finruntime.strategies._ds40180_account as account
    from finruntime.strategies._ds40180_common import PAPER_EXECUTION_COST
    from finruntime.strategies.ds40180_t50c3_paper import (
        _input_digest,
        build_engine,
        load_market_data,
    )

    histories, failures = load_market_data(reset_date="2024-01-01")
    engine = build_engine(histories, failures)
    reset_date = list(engine["marketDates"])[365]
    original_solver = account._solve_rebalance
    variants = {
        "baseline": 0.0,
        "order_band_10bps": 0.001,
        "order_band_25bps": 0.0025,
        "order_band_50bps": 0.005,
        "order_band_100bps": 0.010,
        "order_band_200bps": 0.020,
        "order_band_500bps": 0.050,
    }
    results: dict[str, Any] = {}
    try:
        for name, threshold in variants.items():
            account._solve_rebalance = (
                original_solver
                if threshold == 0
                else _deadband_solver(
                    threshold_weight=threshold,
                    execution_cost=PAPER_EXECUTION_COST,
                )
            )
            continuation = account.paper_continuation(
                engine,
                histories,
                reset_date=reset_date,
                initial_nav_usd=INITIAL_NAV_USD,
            )
            results[name] = {
                "threshold_weight": threshold,
                "metrics": _metrics(
                    continuation["daily"], len(continuation["executions"])
                ),
                "funding_actual_intervals": continuation["fundingActualIntervals"],
                "funding_fallback_intervals": continuation["fundingFallbackIntervals"],
            }
    finally:
        account._solve_rebalance = original_solver

    payload = {
        "schema_version": 1,
        "input_sha256": _input_digest(histories, engine["marketDates"]),
        "reset_date": reset_date,
        "history_rows": len(engine["marketDates"]),
        "assets": engine["assets"],
        "failures": failures,
        "variants": results,
        "implementation_note": (
            "Audit monkeypatch only. A production change needs explicit dust-close, "
            "minimum-notional and exchange-step-size rules."
        ),
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
