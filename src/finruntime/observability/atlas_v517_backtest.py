"""Pure-Python replay of the frozen Atlas V517/V524 account overlay.

The replay consumes the checksum-pinned V75 account stream committed by the
research program.  It deliberately does not claim that the reconstructed live
paper strategy (``atlas_nx_r1``) is byte-identical to V75.
"""

from __future__ import annotations

import csv
import hashlib
import math
import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from typing import Any

INITIAL_NAV_USD = 10_000.0
EXPECTED_INPUT_SHA256 = (
    "f9d543ba8ec15c90efa757e64ed772b1a5934e458463124b7df48ddcac96ef01"
)
EXPECTED_ROWS = 2007
EXPECTED_FULL_CAGR = 0.5054770638530515
EXPECTED_FULL_MAX_DRAWDOWN = -0.23679792568887836
EXPECTED_FULL_FINAL_EQUITY = 94_834.07210943113
V75_ANNUAL_TURNOVER = 10.643693754982161
SOURCE_RELATIVE_PATH = Path(
    "research/v75_risk_budget_v501_v508/inputs/v75_stress_equity.csv"
)


@dataclass(frozen=True, slots=True)
class Policy:
    high_leverage: float = 2.075
    base_leverage: float = 0.97
    low_leverage: float = 0.60
    rebalance_days: int = 10
    no_trade_band: float = 0.04
    guard_enter_drawdown: float = -0.245
    guard_exit_drawdown: float = -0.18
    guard_cap: float = 1.00
    guard_minimum_hold_days: int = 7


@dataclass(frozen=True, slots=True)
class Audit:
    name: str
    transfer_cost_bps: float
    financing_rate: float
    extra_underlying_cost_bps: float
    signal_delay_days: int = 0


POLICY = Policy()
AUDITS = (
    Audit("base", 10.0, 0.08, 0.0),
    Audit("severe", 25.0, 0.14, 40.0, 1),
    Audit("extreme", 50.0, 0.22, 80.0, 2),
    Audit("delay_1d", 10.0, 0.08, 0.0, 1),
)


def _source_path() -> Path:
    candidates = (
        Path.cwd() / SOURCE_RELATIVE_PATH,
        Path(__file__).resolve().parents[3] / SOURCE_RELATIVE_PATH,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Atlas V517 input is missing: {SOURCE_RELATIVE_PATH}")


def _load_source() -> tuple[list[date], list[float], str]:
    raw = _source_path().read_bytes()
    normalized = raw.replace(b"\r\n", b"\n")
    checksum = hashlib.sha256(normalized).hexdigest()
    if checksum != EXPECTED_INPUT_SHA256:
        raise RuntimeError(f"Atlas V517 input SHA-256 mismatch: {checksum}")
    rows = list(csv.DictReader(StringIO(normalized.decode("utf-8"))))
    if len(rows) != EXPECTED_ROWS:
        raise RuntimeError(f"Atlas V517 input row mismatch: {len(rows)}")
    dates: list[date] = []
    equity: list[float] = []
    for row in rows:
        timestamp = str(row.get("") or "").split(" ", maxsplit=1)[0]
        value = float(row["equity"])
        if not timestamp or not math.isfinite(value) or value <= 0:
            raise ValueError("Atlas V517 input contains an invalid observation")
        dates.append(date.fromisoformat(timestamp))
        equity.append(value)
    if len(set(dates)) != len(dates) or dates != sorted(dates):
        raise ValueError("Atlas V517 input dates are not unique and ordered")
    return dates, equity, checksum


def _source_returns(equity: list[float]) -> list[float]:
    values = [equity[0] / INITIAL_NAV_USD - 1]
    values.extend(
        equity[index] / equity[index - 1] - 1 for index in range(1, len(equity))
    )
    return values


def _lagged_momentum(equity: list[float], index: int, lookback: int) -> float:
    if index < lookback + 1:
        return math.nan
    return equity[index - 1] / equity[index - lookback - 1] - 1


def _market_states(equity: list[float]) -> list[dict[str, float | int]]:
    state = 0
    age = 14
    high_count = 0
    low_count = 0
    rows: list[dict[str, float | int]] = []
    for index in range(len(equity)):
        fast = _lagged_momentum(equity, index, 20)
        medium = _lagged_momentum(equity, index, 60)
        high_raw = (
            math.isfinite(fast)
            and math.isfinite(medium)
            and fast > 0.05
            and medium > -0.04
        )
        low_raw = (
            math.isfinite(fast)
            and math.isfinite(medium)
            and fast < -0.05
            and medium < -0.10
        )
        high_count = high_count + 1 if high_raw else 0
        low_count = low_count + 1 if low_raw else 0
        switched = 0
        if age >= 14:
            if state == 1:
                if low_count >= 3:
                    state, age, switched = -1, 0, 1
                elif (math.isfinite(fast) and fast < -0.01) or (
                    math.isfinite(medium) and medium < 0
                ):
                    state, age, switched = 0, 0, 1
                else:
                    age += 1
            elif state == -1:
                if high_count >= 1:
                    state, age, switched = 1, 0, 1
                elif math.isfinite(fast) and fast > 0.01:
                    state, age, switched = 0, 0, 1
                else:
                    age += 1
            elif high_count >= 1:
                state, age, switched = 1, 0, 1
            elif low_count >= 3:
                state, age, switched = -1, 0, 1
            else:
                age += 1
        else:
            age += 1
        rows.append(
            {
                "momentum20": fast,
                "momentum60": medium,
                "market_state": state,
                "state_age_days": age,
                "state_switched": switched,
            }
        )
    return rows


def _simulate(
    dates: list[date],
    source_equity: list[float],
    source_returns: list[float],
    audit: Audit,
) -> list[dict[str, Any]]:
    states = _market_states(source_equity)
    holdings = 0.0
    cash = INITIAL_NAV_USD
    equity = INITIAL_NAV_USD
    high_water = INITIAL_NAV_USD
    previous_target = 0.0
    guard_active = False
    guard_age = POLICY.guard_minimum_hold_days
    records: list[dict[str, Any]] = []
    for index, observed_date in enumerate(dates):
        state_index = index - audit.signal_delay_days
        market_state = (
            int(states[state_index]["market_state"]) if state_index >= 0 else 0
        )
        previous_equity = equity
        drawdown_open = equity / max(high_water, 1e-12) - 1
        if guard_age >= POLICY.guard_minimum_hold_days:
            if not guard_active and drawdown_open <= POLICY.guard_enter_drawdown:
                guard_active = True
                guard_age = 0
            elif guard_active and drawdown_open >= POLICY.guard_exit_drawdown:
                guard_active = False
                guard_age = 0
            else:
                guard_age += 1
        else:
            guard_age += 1

        if market_state == 1:
            raw_target = POLICY.high_leverage
        elif market_state == -1:
            raw_target = POLICY.low_leverage
        else:
            raw_target = POLICY.base_leverage
        target = min(raw_target, POLICY.guard_cap) if guard_active else raw_target
        current_weight = holdings / max(equity, 1e-12)
        risk_reduction = target < abs(current_weight) - POLICY.no_trade_band
        scheduled = index == 0 or index % POLICY.rebalance_days == 0
        target_changed = abs(target - previous_target) >= POLICY.no_trade_band
        rebalance = index == 0 or risk_reduction or (scheduled and target_changed)
        meta_turnover = 0.0
        transfer_cost = 0.0
        if rebalance:
            meta_turnover = abs(target - current_weight)
            transfer_cost = equity * meta_turnover * audit.transfer_cost_bps / 10_000
            after_cost = max(equity - transfer_cost, 1e-12)
            holdings = target * after_cost
            cash = after_cost - holdings
            previous_target = target
        else:
            cash = equity - holdings

        financing_cost = max(-cash, 0.0) * audit.financing_rate / 365
        cash -= financing_cost
        open_leverage = abs(holdings) / max(equity, 1e-12)
        extra_underlying_cost = (
            equity
            * open_leverage
            * V75_ANNUAL_TURNOVER
            * audit.extra_underlying_cost_bps
            / 10_000
            / 365
        )
        cash -= extra_underlying_cost
        holdings *= 1 + source_returns[index]
        equity = cash + holdings
        if not math.isfinite(equity) or equity <= 0:
            raise RuntimeError(f"Atlas V517 equity failed on {observed_date}")
        high_water = max(high_water, equity)
        records.append(
            {
                "date": observed_date,
                "return": equity / previous_equity - 1,
                "navUsd": equity,
                "previousNavUsd": previous_equity,
                "desiredLeverage": target,
                "rawTargetLeverage": raw_target,
                "metaTurnover": meta_turnover,
                "transferCostUsd": transfer_cost,
                "financingCostUsd": financing_cost,
                "extraUnderlyingCostUsd": extra_underlying_cost,
                "riskReduction": bool(risk_reduction and rebalance),
                "scheduledRebalance": bool(
                    scheduled and rebalance and not risk_reduction
                ),
                "guardActive": guard_active,
                "marketState": market_state,
                "sourceEquity": source_equity[index],
                "sourceReturn": source_returns[index],
            }
        )
    return records


def _metrics(records: list[dict[str, Any]], scope: str, label: str) -> dict[str, Any]:
    if not records:
        raise ValueError("Atlas V517 metric window is empty")
    returns = [float(row["return"]) for row in records]
    starting_nav = float(records[0]["previousNavUsd"])
    ending_nav = float(records[-1]["navUsd"])
    years = max(len(records) / 365, 1 / 365)
    deviation = statistics.stdev(returns) if len(returns) > 1 else 0.0
    downside = [min(value, 0.0) for value in returns]
    downside_deviation = statistics.stdev(downside) if len(downside) > 1 else 0.0
    high_water = float(records[0]["navUsd"])
    maximum_drawdown = 0.0
    for row in records:
        nav = float(row["navUsd"])
        high_water = max(high_water, nav)
        maximum_drawdown = min(maximum_drawdown, nav / high_water - 1)
    multiple = ending_nav / starting_nav
    return {
        "scope": scope,
        "scope_label": label,
        "cagr_percent": (multiple ** (1 / years) - 1) * 100,
        "total_return_percent": (multiple - 1) * 100,
        "sharpe": (
            statistics.fmean(returns) / deviation * math.sqrt(365)
            if deviation > 0
            else None
        ),
        "sortino": (
            statistics.fmean(returns) / downside_deviation * math.sqrt(365)
            if downside_deviation > 0
            else None
        ),
        "max_drawdown_percent": maximum_drawdown * 100,
        "years": years,
        "starting_nav_usd": starting_nav,
        "ending_nav_usd": ending_nav,
        "daily_observations": len(records),
        "average_target_leverage": statistics.fmean(
            float(row["desiredLeverage"]) for row in records
        ),
        "maximum_target_leverage": max(
            float(row["desiredLeverage"]) for row in records
        ),
    }


def _episodes(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    episodes: list[dict[str, Any]] = []
    start_index = 0
    while start_index < len(records):
        leverage = float(records[start_index]["desiredLeverage"])
        end_index = start_index
        while (
            end_index + 1 < len(records)
            and float(records[end_index + 1]["desiredLeverage"]) == leverage
        ):
            end_index += 1
        first = records[start_index]
        last = records[end_index]
        entry_source = float(first["sourceEquity"]) / (1 + float(first["sourceReturn"]))
        exit_source = float(last["sourceEquity"])
        net_pnl = sum(
            float(row["navUsd"]) - float(row["previousNavUsd"])
            for row in records[start_index : end_index + 1]
        )
        entry_date = first["date"]
        exit_date = last["date"]
        episodes.append(
            {
                "id": f"atlas-v517-{entry_date.isoformat()}-{leverage:.3f}",
                "asset": f"V75 · {leverage:.3f}x",
                "direction": "LONG",
                "status": "closed",
                "entry_date": entry_date.isoformat(),
                "exit_date": exit_date.isoformat(),
                "held_through": exit_date.isoformat(),
                "holding_days": (exit_date - entry_date).days,
                "entry_price": entry_source,
                "exit_price": exit_source,
                "asset_return_percent": (exit_source / entry_source - 1) * 100,
                "net_pnl_usd": net_pnl,
                "order_count": 1,
                "target_leverage": leverage,
            }
        )
        start_index = end_index + 1
    return sorted(episodes, key=lambda item: item["exit_date"], reverse=True)


def run_atlas_v517_replay() -> dict[str, Any]:
    """Recompute Atlas V517 base and stress accounts from the pinned source."""

    dates, source_equity, checksum = _load_source()
    source_returns = _source_returns(source_equity)
    accounts = {
        audit.name: _simulate(dates, source_equity, source_returns, audit)
        for audit in AUDITS
    }
    base = accounts["base"]
    full_metrics = _metrics(
        base,
        "full_frozen_research",
        "Полный V517/V524 · 2021—2026 H1",
    )
    if (
        abs(full_metrics["cagr_percent"] / 100 - EXPECTED_FULL_CAGR) > 1e-10
        or abs(full_metrics["max_drawdown_percent"] / 100 - EXPECTED_FULL_MAX_DRAWDOWN)
        > 1e-10
        or abs(full_metrics["ending_nav_usd"] - EXPECTED_FULL_FINAL_EQUITY) > 1e-6
    ):
        raise RuntimeError("Atlas V517 deterministic regression mismatch")

    requested_start = dates[-1] - timedelta(days=729)
    requested = [row for row in base if row["date"] >= requested_start]
    requested_metrics = _metrics(
        requested,
        "latest_two_years_in_frozen_stream",
        "Последние 2 года доступного V517-потока",
    )
    stress_metrics = {
        name: _metrics(
            records,
            f"full_frozen_{name}",
            f"Полный V517/V524 · {name}",
        )
        for name, records in accounts.items()
        if name != "base"
    }
    return {
        "dates": dates,
        "input_sha256": checksum,
        "metrics": full_metrics,
        "requested_window_metrics": requested_metrics,
        "stress_metrics": stress_metrics,
        "episodes": _episodes(requested),
        "requested_start": requested_start,
        "requested_end": dates[-1],
    }
