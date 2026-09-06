#!/usr/bin/env python3
"""One-off audit harness for agent/backtest-audit-20260801.

This file is intentionally isolated from production and paper entrypoints. It
runs reproducible reports, prints JSON, and never mutates a paper snapshot.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import traceback
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

AUDIT_NOW = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
INITIAL_NAV_USD = 10_000.0
TROPICAL_YEAR_DAYS = 365.2425


def _finite(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _daily_metrics(
    daily: list[dict[str, Any]], *, initial_nav_usd: float = INITIAL_NAV_USD
) -> dict[str, Any]:
    if not daily:
        raise ValueError("daily ledger is empty")
    ordered = sorted(daily, key=lambda item: str(item["date"]))
    start = date.fromisoformat(str(ordered[0]["date"]))
    end = date.fromisoformat(str(ordered[-1]["date"]))
    years = max((end - start).days / TROPICAL_YEAR_DAYS, 1.0 / TROPICAL_YEAR_DAYS)
    ending_nav = float(ordered[-1]["navUsd"])
    multiple = ending_nav / initial_nav_usd
    returns = [float(item["return"]) for item in ordered if _finite(item.get("return"))]
    mean = statistics.fmean(returns) if returns else 0.0
    deviation = statistics.stdev(returns) if len(returns) >= 2 else 0.0
    downside = [min(0.0, value) for value in returns]
    downside_deviation = (
        math.sqrt(statistics.fmean(value * value for value in downside))
        if downside
        else 0.0
    )
    peak = initial_nav_usd
    max_drawdown = 0.0
    for item in ordered:
        nav = float(item["navUsd"])
        peak = max(peak, nav)
        max_drawdown = min(max_drawdown, nav / peak - 1.0)
    trade_cost_usd = sum(float(item.get("tradeCostUsd") or 0.0) for item in ordered)
    funding_pnl_usd = sum(float(item.get("fundingPnlUsd") or 0.0) for item in ordered)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "observations": len(ordered),
        "years": years,
        "starting_nav_usd": initial_nav_usd,
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
        "trade_cost_usd": trade_cost_usd,
        "funding_pnl_usd": funding_pnl_usd,
        "average_gross_exposure": statistics.fmean(
            float(item.get("heldGrossExposure") or item.get("grossExposure") or 0.0)
            for item in ordered
        ),
    }


def _compact_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "strategy_id": report.get("strategy_id"),
        "strategy_identity": report.get("strategy_identity"),
        "strategy_name": report.get("strategy_name"),
        "report_kind": report.get("report_kind"),
        "execution": report.get("execution"),
        "window": report.get("window"),
        "evidence": report.get("evidence"),
        "metrics": report.get("metrics"),
        "trade_count": report.get("trade_count"),
        "blockers": report.get("blockers"),
        "limitations": report.get("limitations"),
        "provenance": report.get("provenance"),
        "historical_reference": report.get("historical_reference"),
    }


def _capture(name: str, function: Callable[[], Any]) -> dict[str, Any]:
    started = datetime.now(UTC)
    try:
        value = function()
        return {
            "status": "ok",
            "started_at": started.isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "result": value,
        }
    except Exception as error:  # noqa: BLE001 - audit must preserve every failure
        return {
            "status": "error",
            "started_at": started.isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "error_type": type(error).__name__,
            "error": str(error),
            "traceback": traceback.format_exc(),
            "audit": name,
        }


def _atlas_sensitivity() -> dict[str, Any]:
    import finruntime.observability.atlas_v517_backtest as atlas

    dates, source_equity, checksum = atlas._load_source()
    source_returns = atlas._source_returns(source_equity)
    original_policy = atlas.POLICY
    base_audit = atlas.Audit("base", 10.0, 0.08, 0.0)
    variants = {
        "frozen_original": original_policy,
        "high_1_75": replace(original_policy, high_leverage=1.75),
        "high_1_50": replace(original_policy, high_leverage=1.50),
        "base_0_85": replace(original_policy, base_leverage=0.85),
        "all_states_1x": replace(
            original_policy,
            high_leverage=1.0,
            base_leverage=1.0,
            low_leverage=1.0,
            guard_cap=1.0,
        ),
        "rebalance_5d": replace(original_policy, rebalance_days=5),
        "rebalance_20d": replace(original_policy, rebalance_days=20),
        "band_0": replace(original_policy, no_trade_band=0.0),
        "band_8pct": replace(original_policy, no_trade_band=0.08),
        "guard_cap_0_75": replace(original_policy, guard_cap=0.75),
        "guard_earlier": replace(
            original_policy,
            guard_enter_drawdown=-0.18,
            guard_exit_drawdown=-0.12,
            guard_cap=0.80,
        ),
        "guard_disabled": replace(
            original_policy,
            guard_enter_drawdown=-0.99,
            guard_exit_drawdown=-0.95,
        ),
    }
    latest_two_year_start = dates[-1] - timedelta(days=729)
    post_2024_start = date(2024, 1, 1)
    rows: dict[str, Any] = {}
    try:
        for name, policy in variants.items():
            atlas.POLICY = policy
            records = atlas._simulate(dates, source_equity, source_returns, base_audit)
            recent = [item for item in records if item["date"] >= latest_two_year_start]
            post_2024 = [item for item in records if item["date"] >= post_2024_start]
            rows[name] = {
                "policy": asdict(policy),
                "full": atlas._metrics(records, "full", name),
                "latest_two_years": atlas._metrics(recent, "latest_two_years", name),
                "post_2024": atlas._metrics(post_2024, "post_2024", name),
            }
    finally:
        atlas.POLICY = original_policy
    return {
        "input_sha256": checksum,
        "source_rows": len(dates),
        "source_start": dates[0].isoformat(),
        "source_end": dates[-1].isoformat(),
        "latest_two_year_start": latest_two_year_start.isoformat(),
        "variants": rows,
    }


def _ds_real_replay() -> dict[str, Any]:
    from finruntime.strategies.ds40180_t50c3_paper import (
        build_engine,
        compute_forward_state,
        load_market_data,
    )

    histories, failures = load_market_data(reset_date="2024-01-01")
    engine = build_engine(histories, failures)
    market_dates = list(engine["marketDates"])
    # The runtime clips history to 760 rows. Use a conservative 365-row signal
    # burn-in, leaving the longest honest forward-like segment available.
    burn_in = 365
    if len(market_dates) <= burn_in + 30:
        raise ValueError(f"insufficient DS history after burn-in: {len(market_dates)}")
    reset_date = market_dates[burn_in]
    snapshot = compute_forward_state(
        histories,
        failures,
        reset_date=reset_date,
        initial_nav_usd=INITIAL_NAV_USD,
    )
    daily = list(snapshot["paper"]["daily"])
    metrics = _daily_metrics(daily)
    metrics.update(
        {
            "total_executions": snapshot["paper"]["totalExecutions"],
            "funding_actual_intervals": snapshot["funding"]["actualIntervals"],
            "funding_fallback_intervals": snapshot["funding"]["fallbackIntervals"],
        }
    )
    return {
        "requested_two_year_start": (AUDIT_NOW.date() - timedelta(days=731)).isoformat(),
        "actual_reset_date": reset_date,
        "history_rows": len(market_dates),
        "burn_in_rows": burn_in,
        "full_two_year_test_available": False,
        "full_two_year_blocker": (
            "HISTORY_LIMIT=760 leaves about 395 observations after a 365-day "
            "signal burn-in; increase the immutable data window before claiming "
            "a two-year DS replay."
        ),
        "assets": snapshot["assets"],
        "failed_assets": snapshot["failedAssets"],
        "inactive_assets": snapshot["inactiveAssets"],
        "input_sha256": snapshot["inputSha256"],
        "metrics": metrics,
        "latest_state": {
            "as_of": snapshot["asOf"],
            "effective_date": snapshot["effectiveDate"],
            "target_gross": snapshot["targetGross"],
            "target_net": snapshot["targetNet"],
            "risk_scale": snapshot["riskScale"],
            "combined_bear": snapshot["regime"]["combinedBear"],
        },
        "warnings": snapshot["warnings"],
    }


def _on_demand(strategy_id: str) -> dict[str, Any]:
    from finruntime.observability.backtest_runner import run_backtest

    return _compact_report(run_backtest(strategy_id, now=AUDIT_NOW))


def run_core() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "audit_now": AUDIT_NOW.isoformat(),
        "atlas_existing_report": _capture(
            "atlas_existing_report", lambda: _on_demand("atlas-nx")
        ),
        "atlas_sensitivity": _capture("atlas_sensitivity", _atlas_sensitivity),
        "dyn_iv113_current_two_year": _capture(
            "dyn_iv113_current_two_year", lambda: _on_demand("dyn-iv113")
        ),
        "ds40180_real_okx_replay": _capture("ds40180_real_okx_replay", _ds_real_replay),
    }


def run_factors() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "audit_now": AUDIT_NOW.isoformat(),
        "funding_neutral": _capture(
            "funding_neutral", lambda: _on_demand("funding-neutral")
        ),
        "consensus_wif_dot": _capture(
            "consensus_wif_dot", lambda: _on_demand("consensus-wif-dot")
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=("core", "factors"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_core() if args.suite == "core" else run_factors()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
