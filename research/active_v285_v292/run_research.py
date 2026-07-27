#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
LOTTERY_ROOT = REPO_ROOT / "research" / "active_v269_v276"
LOTTERY_SOURCE = LOTTERY_ROOT / "run_research.py"
_spec = importlib.util.spec_from_file_location("v269_hourly_alpha", LOTTERY_SOURCE)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"cannot import V269 engine from {LOTTERY_SOURCE}")
lottery = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = lottery
_spec.loader.exec_module(lottery)

CANDIDATE = "ACTIVE_V285_HOURLY_GROSS_CONTROLLED_LOW_SKEW"
SOURCE_POLICY_NAME = "low_idiosyncratic_skewness_l180_k3_r28_beta"
POLICY = lottery.Policy("low_idiosyncratic_skewness", 180, 3, 28, "beta")
SYMBOLS = tuple(lottery.SYMBOLS)
START = "2021-01-01"
DEVELOPMENT_END_EXCLUSIVE = "2024-01-01"
VALIDATION_START = "2024-01-01"
VALIDATION_END_EXCLUSIVE = "2025-01-01"
HOLDOUT_START = "2025-01-01"
HOLDOUT_END_EXCLUSIVE = "2026-01-01"
FINAL_START = "2026-01-01"
END_EXCLUSIVE = "2026-07-01"
INITIAL_EQUITY = 10_000.0
TARGET_GROSS = 0.50
SOFT_GROSS_TRIGGER = 0.60
HARD_CLOSE_GROSS = 0.70
DEVELOPMENT_STRESS_GROSS = 0.85
POST_OOS_STRESS_GROSS = 0.90
FORCED_EXIT_PENALTY_BPS = 100.0

lottery.INITIAL_EQUITY = INITIAL_EQUITY
lottery.TARGET_GROSS = TARGET_GROSS
lottery.MAX_REALIZED_GROSS = HARD_CLOSE_GROSS
lottery.base.INITIAL_EQUITY = INITIAL_EQUITY
lottery.base.TARGET_GROSS = TARGET_GROSS
lottery.base.MAX_REALIZED_GROSS = HARD_CLOSE_GROSS
lottery.base.FORCED_EXIT_PENALTY_BPS = FORCED_EXIT_PENALTY_BPS


@dataclass(frozen=True, slots=True)
class Audit:
    name: str
    cost_bps_per_side: float
    alpha_delay_days: int = 0

    @property
    def cost_rate(self) -> float:
        return self.cost_bps_per_side / 10_000.0


AUDITS = (
    Audit("base", 30.0),
    Audit("severe", 60.0),
    Audit("extreme", 100.0),
    Audit("delay_1d", 30.0, alpha_delay_days=1),
)
DEVELOPMENT_GATES = {
    "cagr_min": 0.04,
    "sharpe_min": 0.80,
    "max_drawdown_min": -0.15,
    "rebalance_events_min": 18,
    "annual_turnover_max": 20.0,
    "max_hourly_close_gross": HARD_CLOSE_GROSS,
    "max_adverse_intrahour_stress_gross": DEVELOPMENT_STRESS_GROSS,
    "all_development_years_positive": True,
    "net_long_leg_pnl_positive": True,
    "net_short_leg_pnl_positive": True,
    "symbols_traded_min": 10,
    "top_positive_asset_pnl_share_max": 0.35,
}
POST_OOS_GATES = {
    "validation_return_positive": True,
    "holdout_return_positive": True,
    "final_return_positive": True,
    "full_cagr_min": 0.08,
    "full_sharpe_min": 0.80,
    "full_max_drawdown_min": -0.15,
    "severe_full_cagr_positive": True,
    "extreme_full_cagr_positive": True,
    "latency_full_cagr_positive": True,
    "worst_calendar_year_min": -0.10,
    "full_long_leg_pnl_positive": True,
    "full_short_leg_pnl_positive": True,
    "all_audits_max_hourly_close_gross": HARD_CLOSE_GROSS,
    "all_audits_max_adverse_intrahour_stress_gross": POST_OOS_STRESS_GROSS,
    "top_positive_asset_pnl_share_max": 0.35,
    "forced_exit_count_max": 4,
}
SOURCE_EVIDENCE = {
    "v269_workflow_run": 30252073440,
    "v269_artifact": 8647414440,
    "v269_artifact_digest": "sha256:d1c62d3f0a3b741357020f4ee76eaa86c35bf87d6c04b14da0e01138c62ad1e9",
    "v277_workflow_run": 30252852852,
    "v277_artifact": 8647694050,
    "v277_artifact_digest": "sha256:8869135f97ffc141fa8e85c5f58ab8c4d9b04bb2890967da8eb6a63814c3627c",
}


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if np.isfinite(number) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(clean(value), indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        clean(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def month_range(first: str, last: str) -> set[str]:
    return {str(value) for value in pd.period_range(first, last, freq="M")}


def verify_source_evidence() -> dict[str, Any]:
    ranking_path = LOTTERY_ROOT / "results" / "selection_ranking_before_validation.csv"
    summary_path = LOTTERY_ROOT / "results" / "summary.json"
    v277_path = REPO_ROOT / "research" / "active_v277_v284" / "results" / "summary.json"
    if not ranking_path.exists() or not summary_path.exists() or not v277_path.exists():
        raise RuntimeError("committed V269 and V277 evidence is required")
    ranking = pd.read_csv(ranking_path)
    selected = ranking[ranking.policy == SOURCE_POLICY_NAME]
    if len(selected) != 1:
        raise RuntimeError(f"expected one source policy row, got {len(selected)}")
    row = selected.iloc[0].to_dict()
    if not (
        float(row["development_cagr"]) >= 0.04
        and float(row["development_sharpe"]) >= 0.80
        and float(row["development_max_drawdown"]) >= -0.15
        and str(row["all_development_years_positive"]).strip().lower() == "true"
        and float(row["long_leg_pnl"]) > 0.0
        and float(row["short_leg_pnl"]) > 0.0
        and float(row["development_max_gross"]) > HARD_CLOSE_GROSS
    ):
        raise RuntimeError("V269 source policy is not the declared gross-only near-miss")
    v277 = json.loads(v277_path.read_text())
    if v277.get("oos_opened") is not False or v277.get("status") != "rejected_before_oos":
        raise RuntimeError("V277 must leave OOS closed")
    return {
        "v269_source_row": clean(row),
        "v269_summary_sha256": sha256_file(summary_path),
        "v269_ranking_file_sha256": sha256_file(ranking_path),
        "v277_summary_sha256": sha256_file(v277_path),
    }


def archive_gate(
    klines: dict[str, pd.DataFrame],
    records: list[dict[str, Any]],
    candidate: str,
    expected_frequency: str,
) -> dict[str, Any]:
    details: dict[str, Any] = {}
    all_passed = True
    for symbol in SYMBOLS:
        price_records = [
            row for row in records if row["symbol"] == symbol and row["kind"] == "klines"
        ]
        funding_records = [
            row for row in records if row["symbol"] == symbol and row["kind"] == "fundingRate"
        ]
        valid_price = sorted(
            row["month"]
            for row in price_records
            if not row.get("missing") and int(row.get("rows", 0)) > 0
        )
        valid_funding = {
            row["month"]
            for row in funding_records
            if not row.get("missing") and int(row.get("rows", 0)) > 0
        }
        if valid_price:
            active_months = month_range(valid_price[0], valid_price[-1])
            price_share = len(set(valid_price) & active_months) / len(active_months)
            funding_share = len(valid_funding & active_months) / len(active_months)
        else:
            active_months = set()
            price_share = 0.0
            funding_share = 0.0
        development_months = month_range("2021-01", "2023-12")
        development_price_months = len(set(valid_price) & development_months)
        latest_required = symbol in {"BTCUSDT", "ETHUSDT"}
        latest_present = "2026-06" in set(valid_price)
        frame = klines.get(symbol, pd.DataFrame())
        if frame.empty:
            observation_share = 0.0
        else:
            expected = pd.date_range(frame.index.min(), frame.index.max(), freq=expected_frequency)
            observation_share = len(frame.index.intersection(expected)) / len(expected) if len(expected) else 0.0
        symbol_passed = bool(
            symbol in klines
            and price_share >= 0.95
            and funding_share >= 0.90
            and development_price_months >= 34
            and observation_share >= 0.98
            and (not latest_required or latest_present)
        )
        details[symbol] = {
            "first_price_month": valid_price[0] if valid_price else None,
            "last_price_month": valid_price[-1] if valid_price else None,
            "active_month_count": len(active_months),
            "price_active_month_share": price_share,
            "funding_active_month_share": funding_share,
            "development_price_months": development_price_months,
            "observation_share": observation_share,
            "rows": len(frame),
            "timestamp_min": frame.index.min().isoformat() if not frame.empty else None,
            "timestamp_max": frame.index.max().isoformat() if not frame.empty else None,
            "latest_required": latest_required,
            "latest_present": latest_present,
            "passed": symbol_passed,
        }
        all_passed = all_passed and symbol_passed
    return {
        "candidate": candidate,
        "fixed_universe_size": len(SYMBOLS),
        "minimum_price_active_month_share": 0.95,
        "minimum_funding_active_month_share": 0.90,
        "minimum_observation_share": 0.98,
        "minimum_development_price_months": 34,
        "details": details,
        "passed": bool(all_passed),
        "survivor_replacement_permitted": False,
    }


def panels(
    klines: dict[str, pd.DataFrame], funding: dict[str, pd.Series]
) -> tuple[pd.DatetimeIndex, dict[str, pd.DataFrame]]:
    index: pd.DatetimeIndex | None = None
    for frame in klines.values():
        index = frame.index if index is None else index.union(frame.index)
    if index is None:
        raise RuntimeError("empty hourly panel")
    index = index.sort_values()
    def field_series(symbol: str, field: str) -> pd.Series:
        frame = klines.get(symbol)
        if frame is None or frame.empty or field not in frame.columns:
            return pd.Series(index=index, dtype=float)
        return frame[field].reindex(index)

    result = {
        field: pd.DataFrame(
            {symbol: field_series(symbol, field) for symbol in SYMBOLS},
            index=index,
        )
        for field in ("open", "high", "low", "close")
    }
    result["funding"] = pd.DataFrame(
        {
            symbol: funding.get(symbol, pd.Series(dtype=float))
            .groupby(level=0)
            .sum()
            .reindex(index)
            .fillna(0.0)
            for symbol in SYMBOLS
        },
        index=index,
    )
    return index, result


def elapsed_years(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 0.0
    return max((index[-1] - index[0]).total_seconds() / (365.2425 * 86400.0), 1 / 365.2425)


def account_metrics(account: pd.DataFrame) -> dict[str, Any]:
    if account.empty:
        return {
            "total_return": 0.0,
            "cagr": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "annual_turnover": 0.0,
            "average_gross": 0.0,
            "max_gross": 0.0,
            "max_stress_gross": 0.0,
            "costs": 0.0,
            "funding_pnl": 0.0,
            "daily_rebalance_events": 0,
            "risk_reduction_events": 0,
            "forced_exits": 0,
        }
    years = elapsed_years(account.index)
    final = float(account.equity.iloc[-1])
    total = final / INITIAL_EQUITY - 1.0
    cagr = (final / INITIAL_EQUITY) ** (1.0 / years) - 1.0 if final > 0 and years > 0 else -1.0
    returns = account.equity.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    frequency = len(returns) / years if years > 0 else 0.0
    std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    sharpe = float(returns.mean() / std * np.sqrt(frequency)) if std > 0 else 0.0
    drawdown = account.equity / account.equity.cummax() - 1.0
    return {
        "total_return": total,
        "cagr": cagr,
        "max_drawdown": float(drawdown.min()),
        "sharpe": sharpe,
        "annual_turnover": float(account.turnover.sum() / years) if years > 0 else 0.0,
        "average_gross": float(account.gross.mean()),
        "max_gross": float(account.gross.max()),
        "max_stress_gross": float(account.stress_gross.max()),
        "costs": float(account.costs.sum()),
        "funding_pnl": float(account.funding_pnl.sum()),
        "daily_rebalance_events": int(account.daily_rebalance.sum()),
        "risk_reduction_events": int(account.risk_reduction.sum()),
        "forced_exits": int(account.forced_exits.sum()),
    }


def yearly_returns(account: pd.DataFrame, name: str) -> pd.DataFrame:
    previous = INITIAL_EQUITY
    rows: list[dict[str, Any]] = []
    for year, part in account.groupby(account.index.year):
        end_value = float(part.equity.iloc[-1])
        rows.append({"year": int(year), name: end_value / previous - 1.0})
        previous = end_value
    return pd.DataFrame(rows)


def simulate_hourly(
    index: pd.DatetimeIndex,
    panel: dict[str, pd.DataFrame],
    daily_weights: pd.DataFrame,
    start: str,
    end_exclusive: str,
    audit: Audit,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    selected = np.flatnonzero(
        (index >= pd.Timestamp(start, tz="UTC"))
        & (index < pd.Timestamp(end_exclusive, tz="UTC"))
    )
    if not len(selected):
        return pd.DataFrame(), {}
    opens = panel["open"].to_numpy(float)
    highs = panel["high"].to_numpy(float)
    lows = panel["low"].to_numpy(float)
    closes = panel["close"].to_numpy(float)
    funding = panel["funding"].to_numpy(float)
    available = np.isfinite(opens) & np.isfinite(closes) & (opens > 0) & (closes > 0)

    executed_daily = daily_weights.shift(1 + audit.alpha_delay_days)
    normalized_days = index.normalize()
    target_values = executed_daily.reindex(normalized_days).fillna(0.0).to_numpy(float)

    n = len(SYMBOLS)
    notional = np.zeros(n, dtype=float)
    equity = INITIAL_EQUITY
    previous = -1
    rows: list[dict[str, Any]] = []
    asset_pnl = {symbol: 0.0 for symbol in SYMBOLS}
    long_leg_pnl = 0.0
    short_leg_pnl = 0.0
    ever_traded: set[str] = set()
    forced_exit_count = 0
    last_alpha_target: np.ndarray | None = None

    def allocate(value: float, j: int, side: float) -> None:
        nonlocal long_leg_pnl, short_leg_pnl
        asset_pnl[SYMBOLS[j]] += float(value)
        if side > 0:
            long_leg_pnl += float(value)
        elif side < 0:
            short_leg_pnl += float(value)

    for i in selected:
        timestamp = index[i]
        hour_cost = 0.0
        hour_funding = 0.0
        hour_price = 0.0
        hour_forced = 0
        daily_event = 0
        risk_event = 0
        turnover = 0.0

        if previous >= 0:
            for j in np.flatnonzero(np.abs(notional) > 1e-12):
                side = float(np.sign(notional[j]))
                if np.isfinite(opens[i, j]) and np.isfinite(closes[previous, j]) and closes[previous, j] > 0:
                    ratio = opens[i, j] / closes[previous, j]
                    pnl = float(notional[j] * (ratio - 1.0))
                    equity += pnl
                    hour_price += pnl
                    allocate(pnl, j, side)
                    notional[j] *= ratio
                else:
                    penalty_rate = max(audit.cost_rate, FORCED_EXIT_PENALTY_BPS / 10_000.0)
                    penalty = float(abs(notional[j]) * penalty_rate)
                    equity -= penalty
                    hour_cost += penalty
                    allocate(-penalty, j, side)
                    notional[j] = 0.0
                    forced_exit_count += 1
                    hour_forced += 1

        for j in np.flatnonzero(np.abs(notional) > 1e-12):
            rate = funding[i, j] if np.isfinite(funding[i, j]) else 0.0
            if rate != 0.0:
                side = float(np.sign(notional[j]))
                pnl = float(-(notional[j] * rate))
                equity += pnl
                hour_funding += pnl
                allocate(pnl, j, side)

        equity_before_trade = max(equity, 1e-12)
        current_gross = float(np.abs(notional).sum() / equity_before_trade)
        new_day = previous < 0 or timestamp.normalize() != index[previous].normalize()
        desired_weights: np.ndarray | None = None
        rebalance_kind: str | None = None
        if new_day:
            candidate = np.nan_to_num(target_values[i].copy())
            candidate[~available[i]] = 0.0
            gross = float(np.abs(candidate).sum())
            if gross > TARGET_GROSS:
                candidate *= TARGET_GROSS / gross
            target_changed = bool(
                last_alpha_target is None
                or not np.allclose(candidate, last_alpha_target, rtol=0.0, atol=1e-12)
            )
            if target_changed:
                desired_weights = candidate
                rebalance_kind = "daily"
                last_alpha_target = candidate.copy()
        if desired_weights is None and current_gross > SOFT_GROSS_TRIGGER:
            current_weights = notional / equity_before_trade
            desired_weights = current_weights * (TARGET_GROSS / current_gross)
            desired_weights[~available[i]] = 0.0
            rebalance_kind = "risk"

        if desired_weights is not None:
            desired = desired_weights * equity_before_trade
            delta = desired - notional
            turnover_notional = float(np.abs(delta).sum())
            turnover = turnover_notional / equity_before_trade
            if turnover_notional > 0:
                costs = np.abs(delta) * audit.cost_rate
                for j in np.flatnonzero(costs > 0):
                    side = (
                        float(np.sign(desired[j]))
                        if abs(desired[j]) > 1e-12
                        else float(np.sign(notional[j]))
                    )
                    allocate(-float(costs[j]), j, side)
                trade_cost = float(costs.sum())
                equity -= trade_cost
                hour_cost += trade_cost
                notional = desired_weights * max(equity, 0.0)
                for j in np.flatnonzero(np.abs(notional) > 1e-12):
                    ever_traded.add(SYMBOLS[j])
                if turnover > 1e-4:
                    daily_event = int(rebalance_kind == "daily")
                    risk_event = int(rebalance_kind == "risk")

        stress_ratios = np.ones(n, dtype=float)
        stress_valid = True
        for j in np.flatnonzero(np.abs(notional) > 1e-12):
            if not (
                np.isfinite(opens[i, j])
                and opens[i, j] > 0
                and np.isfinite(highs[i, j])
                and np.isfinite(lows[i, j])
            ):
                stress_valid = False
                break
            stress_ratios[j] = (
                lows[i, j] / opens[i, j]
                if notional[j] > 0
                else highs[i, j] / opens[i, j]
            )
        if stress_valid:
            stress_pnl = float(np.sum(notional * (stress_ratios - 1.0)))
            stress_equity = equity + stress_pnl
            stress_notional = notional * stress_ratios
            stress_gross = (
                float(np.abs(stress_notional).sum() / stress_equity)
                if stress_equity > 1e-12
                else 1e6
            )
        else:
            stress_gross = 1e6

        valid = available[i]
        close_ratios = np.divide(
            closes[i], opens[i], out=np.ones(n), where=valid
        )
        for j in np.flatnonzero(np.abs(notional) > 1e-12):
            side = float(np.sign(notional[j]))
            pnl = float(notional[j] * (close_ratios[j] - 1.0))
            equity += pnl
            hour_price += pnl
            allocate(pnl, j, side)
            notional[j] *= close_ratios[j]

        close_gross = float(np.abs(notional).sum() / max(equity, 1e-12))
        rows.append(
            {
                "equity": max(equity, 0.0),
                "gross": close_gross,
                "stress_gross": stress_gross,
                "turnover": turnover,
                "costs": hour_cost,
                "funding_pnl": hour_funding,
                "price_pnl": hour_price,
                "daily_rebalance": daily_event,
                "risk_reduction": risk_event,
                "forced_exits": hour_forced,
            }
        )
        previous = int(i)

    account = pd.DataFrame(rows, index=index[selected])
    positive = [max(0.0, value) for value in asset_pnl.values()]
    positive_total = float(sum(positive))
    top_share = max(positive) / positive_total if positive_total > 0 else 1.0
    diagnostics = {
        "asset_pnl": asset_pnl,
        "long_leg_pnl": long_leg_pnl,
        "short_leg_pnl": short_leg_pnl,
        "symbols_traded": sorted(ever_traded),
        "symbol_count_traded": len(ever_traded),
        "forced_exit_count": forced_exit_count,
        "daily_rebalance_events": int(account.daily_rebalance.sum()) if not account.empty else 0,
        "risk_reduction_events": int(account.risk_reduction.sum()) if not account.empty else 0,
        "top_positive_asset_pnl_share": top_share,
    }
    return account, diagnostics


def development_gate_results(
    metrics: dict[str, Any], diagnostics: dict[str, Any], annual: pd.DataFrame
) -> dict[str, bool]:
    all_years_positive = bool(
        not annual.empty and (pd.to_numeric(annual["return"], errors="coerce") > 0).all()
    )
    return {
        "cagr": float(metrics["cagr"]) >= DEVELOPMENT_GATES["cagr_min"],
        "sharpe": float(metrics["sharpe"]) >= DEVELOPMENT_GATES["sharpe_min"],
        "max_drawdown": float(metrics["max_drawdown"]) >= DEVELOPMENT_GATES["max_drawdown_min"],
        "rebalance_events": int(diagnostics["daily_rebalance_events"])
        >= DEVELOPMENT_GATES["rebalance_events_min"],
        "annual_turnover": float(metrics["annual_turnover"])
        <= DEVELOPMENT_GATES["annual_turnover_max"],
        "max_hourly_close_gross": float(metrics["max_gross"])
        <= DEVELOPMENT_GATES["max_hourly_close_gross"],
        "max_adverse_intrahour_stress_gross": float(metrics["max_stress_gross"])
        <= DEVELOPMENT_GATES["max_adverse_intrahour_stress_gross"],
        "all_development_years_positive": all_years_positive,
        "net_long_leg_pnl_positive": float(diagnostics["long_leg_pnl"]) > 0,
        "net_short_leg_pnl_positive": float(diagnostics["short_leg_pnl"]) > 0,
        "symbols_traded": int(diagnostics["symbol_count_traded"])
        >= DEVELOPMENT_GATES["symbols_traded_min"],
        "concentration": float(diagnostics["top_positive_asset_pnl_share"])
        <= DEVELOPMENT_GATES["top_positive_asset_pnl_share_max"],
    }


def write_manifest(root: Path) -> None:
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative = str(path.relative_to(root))
        if relative in {"MANIFEST.json", "run.log"}:
            continue
        files[relative] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    write_json(root / "MANIFEST.json", {"candidate": CANDIDATE, "files": files})


def self_test() -> None:
    assert POLICY.name == SOURCE_POLICY_NAME
    assert TARGET_GROSS < SOFT_GROSS_TRIGGER < HARD_CLOSE_GROSS
    source = verify_source_evidence()
    assert source["v269_source_row"]["policy"] == SOURCE_POLICY_NAME

    index = pd.date_range("2021-01-01", periods=96, freq="1h", tz="UTC")
    columns = list(SYMBOLS[:6])
    opens = pd.DataFrame(100.0, index=index, columns=columns)
    closes = opens.copy()
    highs = opens * 1.01
    lows = opens * 0.99
    closes.iloc[30, 0] = 600.0
    highs.iloc[30, 0] = 620.0
    opens.iloc[31:, 0] = 600.0
    closes.iloc[31:, 0] = 600.0
    highs.iloc[31:, 0] = 606.0
    lows.iloc[31:, 0] = 594.0
    hourly_klines: dict[str, pd.DataFrame] = {}
    hourly_funding: dict[str, pd.Series] = {}
    for symbol in SYMBOLS:
        if symbol in columns:
            hourly_klines[symbol] = pd.DataFrame(
                {
                    "open": opens[symbol],
                    "high": highs[symbol],
                    "low": lows[symbol],
                    "close": closes[symbol],
                }
            )
        else:
            hourly_klines[symbol] = pd.DataFrame(
                {field: pd.Series(dtype=float) for field in ("open", "high", "low", "close")}
            )
        hourly_funding[symbol] = pd.Series(dtype=float)
    hourly_index, panel = panels(
        {symbol: frame for symbol, frame in hourly_klines.items() if not frame.empty},
        hourly_funding,
    )
    daily_index = pd.date_range("2020-12-31", periods=6, freq="1D", tz="UTC")
    daily_weights = pd.DataFrame(0.0, index=daily_index, columns=SYMBOLS)
    daily_weights.loc[:, columns[:3]] = TARGET_GROSS / 6
    daily_weights.loc[:, columns[3:]] = -TARGET_GROSS / 6
    account, diagnostics = simulate_hourly(
        hourly_index, panel, daily_weights, "2021-01-01", "2021-01-05", AUDITS[0]
    )
    assert not account.empty
    assert np.isfinite(account.equity).all()
    assert diagnostics["daily_rebalance_events"] == 1
    assert diagnostics["risk_reduction_events"] >= 1
    print("V285-V292 hourly controller self-test passed")


def run(root: Path, daily_cache: Path, hourly_cache: Path) -> int:
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
    source = verify_source_evidence()

    daily_config = lottery.base.V9Config(
        symbols=SYMBOLS,
        start="2020-01-01",
        end_exclusive=END_EXCLUSIVE,
        interval="1d",
        starting_equity=INITIAL_EQUITY,
        max_gross=TARGET_GROSS,
        forced_exit_penalty_bps=FORCED_EXIT_PENALTY_BPS,
    )
    daily_klines, daily_funding, daily_records, daily_quality = lottery.base.load_v9(
        daily_config, daily_cache, False
    )
    hourly_config = lottery.base.V9Config(
        symbols=SYMBOLS,
        start="2020-01-01",
        end_exclusive=END_EXCLUSIVE,
        interval="1h",
        starting_equity=INITIAL_EQUITY,
        max_gross=TARGET_GROSS,
        forced_exit_penalty_bps=FORCED_EXIT_PENALTY_BPS,
    )
    hourly_klines, hourly_funding, hourly_records, hourly_quality = lottery.base.load_v9(
        hourly_config, hourly_cache, False
    )
    pd.DataFrame(daily_records).to_csv(results / "daily_data_manifest.csv", index=False)
    pd.DataFrame(hourly_records).to_csv(results / "hourly_data_manifest.csv", index=False)
    pd.DataFrame(daily_quality).to_csv(results / "daily_data_quality.csv", index=False)
    pd.DataFrame(hourly_quality).to_csv(results / "hourly_data_quality.csv", index=False)

    daily_gate = archive_gate(
        daily_klines, daily_records, "V285_DAILY_SIGNAL_DATA_COVERAGE", "1D"
    )
    hourly_gate = archive_gate(
        hourly_klines, hourly_records, "V285_HOURLY_EXECUTION_DATA_COVERAGE", "1h"
    )
    coverage_gate = {
        "candidate": "V285_COMBINED_DAILY_HOURLY_COVERAGE",
        "daily_gate": daily_gate,
        "hourly_gate": hourly_gate,
        "passed": bool(daily_gate["passed"] and hourly_gate["passed"]),
    }
    write_json(results / "coverage_gate.json", coverage_gate)
    if not coverage_gate["passed"]:
        proof = {
            "candidate": CANDIDATE,
            "selection_not_run": True,
            "selection_uses_2024": False,
            "selection_uses_2025": False,
            "selection_uses_2026": False,
            "coverage_gate": coverage_gate,
        }
        proof["selection_proof_sha256"] = canonical_hash(proof)
        decision = {
            "candidate": CANDIDATE,
            "status": "data_access_insufficient",
            "development_reproof_passed": False,
            "oos_opened": False,
            "standalone_selection_passed": False,
            "integration_permitted": False,
            "promoted_candidates": [],
            "live_ready": False,
            "real_leverage_authorized": False,
            "profitability_proven": False,
        }
        summary = {**decision, "coverage_gate": coverage_gate, "selection": proof}
        write_json(results / "selection_proof_before_oos.json", proof)
        write_json(results / "FROZEN_DECISION.json", decision)
        write_json(results / "summary.json", summary)
        pd.DataFrame().to_csv(results / "audit_metrics.csv", index=False)
        (results / "REPORT_RU.md").write_text(
            "# Active V285–V292\n\nStatus: `data_access_insufficient`. OOS not opened.\n"
        )
        write_manifest(root)
        return 0

    daily_market = lottery.base.Market(daily_klines, daily_funding)
    daily_weights = lottery.build_weights(daily_market, POLICY, {}, {})
    daily_weights.to_csv(results / "frozen_daily_weights.csv")
    hourly_index, hourly_panel = panels(hourly_klines, hourly_funding)

    development_account, development_diagnostics = simulate_hourly(
        hourly_index,
        hourly_panel,
        daily_weights,
        START,
        DEVELOPMENT_END_EXCLUSIVE,
        AUDITS[0],
    )
    development_metrics = account_metrics(development_account)
    development_annual = yearly_returns(development_account, "return")
    development_annual.to_csv(results / "DEVELOPMENT_ANNUAL_RETURNS.csv", index=False)
    dev_gates = development_gate_results(
        development_metrics, development_diagnostics, development_annual
    )
    development_passed = bool(all(dev_gates.values()))
    proof = {
        "candidate": CANDIDATE,
        "source_policy": SOURCE_POLICY_NAME,
        "source_evidence": SOURCE_EVIDENCE,
        "source_verification": source,
        "daily_target_gross": TARGET_GROSS,
        "soft_gross_trigger": SOFT_GROSS_TRIGGER,
        "hard_hourly_close_gross": HARD_CLOSE_GROSS,
        "neighboring_triggers_tested": 0,
        "selection_uses_2024": False,
        "selection_uses_2025": False,
        "selection_uses_2026": False,
        "program_level_holdout_pristine": False,
        "development_metrics": development_metrics,
        "development_diagnostics": development_diagnostics,
        "development_annual_returns": development_annual.to_dict(orient="records"),
        "development_gate_results": dev_gates,
        "development_reproof_passed": development_passed,
        "coverage_gate": coverage_gate,
        "design_sha256": sha256_file(root / "V285_V292_DESIGN.json"),
    }
    proof["selection_proof_sha256"] = canonical_hash(proof)
    write_json(results / "selection_proof_before_oos.json", proof)

    if not development_passed:
        decision = {
            "candidate": CANDIDATE,
            "status": "rejected_before_oos",
            "development_reproof_passed": False,
            "oos_opened": False,
            "standalone_selection_passed": False,
            "integration_permitted": False,
            "promoted_candidates": [],
            "live_ready": False,
            "real_leverage_authorized": False,
            "profitability_proven": False,
        }
        summary = {
            **decision,
            "selection": proof,
            "coverage_gate": coverage_gate,
            "development_metrics": development_metrics,
            "development_diagnostics": development_diagnostics,
            "failed_development_gates": [key for key, value in dev_gates.items() if not value],
        }
        write_json(results / "FROZEN_DECISION.json", decision)
        write_json(results / "summary.json", summary)
        pd.DataFrame().to_csv(results / "audit_metrics.csv", index=False)
        development_account.to_csv(results / "development_equity.csv")
        (results / "REPORT_RU.md").write_text(
            "# Active V285–V292 — hourly gross controller\n\n"
            "Status: `rejected_before_oos`. Hourly development gates failed; OOS remained closed.\n"
        )
        write_manifest(root)
        print(json.dumps(clean(summary), indent=2))
        return 0

    period_bounds = {
        "development": (START, DEVELOPMENT_END_EXCLUSIVE),
        "validation": (VALIDATION_START, VALIDATION_END_EXCLUSIVE),
        "holdout": (HOLDOUT_START, HOLDOUT_END_EXCLUSIVE),
        "final": (FINAL_START, END_EXCLUSIVE),
        "full": (START, END_EXCLUSIVE),
    }
    audit_rows: list[dict[str, Any]] = []
    diagnostics_by_audit: dict[str, Any] = {}
    accounts: dict[tuple[str, str], pd.DataFrame] = {}
    for audit in AUDITS:
        row: dict[str, Any] = {"audit": audit.name, **asdict(audit)}
        diagnostics_by_period: dict[str, Any] = {}
        for period, (period_start, period_end) in period_bounds.items():
            account, diagnostics = simulate_hourly(
                hourly_index,
                hourly_panel,
                daily_weights,
                period_start,
                period_end,
                audit,
            )
            accounts[(audit.name, period)] = account
            diagnostics_by_period[period] = diagnostics
            metrics = account_metrics(account)
            for key, value in metrics.items():
                row[f"{period}_{key}"] = value
            row[f"{period}_long_leg_pnl"] = diagnostics.get("long_leg_pnl", 0.0)
            row[f"{period}_short_leg_pnl"] = diagnostics.get("short_leg_pnl", 0.0)
            row[f"{period}_top_positive_asset_pnl_share"] = diagnostics.get(
                "top_positive_asset_pnl_share", 1.0
            )
            row[f"{period}_symbol_count_traded"] = diagnostics.get("symbol_count_traded", 0)
            row[f"{period}_forced_exit_count"] = diagnostics.get("forced_exit_count", 0)
        diagnostics_by_audit[audit.name] = diagnostics_by_period
        audit_rows.append(row)

    audits = pd.DataFrame(audit_rows)
    audits.to_csv(results / "audit_metrics.csv", index=False)
    for audit in AUDITS:
        accounts[(audit.name, "full")].to_csv(results / f"equity_{audit.name}.csv")
    base_row = audits[audits.audit == "base"].iloc[0]
    severe_row = audits[audits.audit == "severe"].iloc[0]
    extreme_row = audits[audits.audit == "extreme"].iloc[0]
    delay_row = audits[audits.audit == "delay_1d"].iloc[0]
    annual = yearly_returns(accounts[("base", "full")], SOURCE_POLICY_NAME)
    annual.to_csv(results / "ANNUAL_RETURNS.csv", index=False)
    worst_year = float(pd.to_numeric(annual[SOURCE_POLICY_NAME], errors="coerce").min())
    all_audits_max_gross = float(audits.full_max_gross.max())
    all_audits_max_stress = float(audits.full_max_stress_gross.max())
    base_full_diagnostics = diagnostics_by_audit["base"]["full"]
    post_gate_results = {
        "validation_return_positive": float(base_row.validation_total_return) > 0,
        "holdout_return_positive": float(base_row.holdout_total_return) > 0,
        "final_return_positive": float(base_row.final_total_return) > 0,
        "full_cagr": float(base_row.full_cagr) >= POST_OOS_GATES["full_cagr_min"],
        "full_sharpe": float(base_row.full_sharpe) >= POST_OOS_GATES["full_sharpe_min"],
        "full_max_drawdown": float(base_row.full_max_drawdown)
        >= POST_OOS_GATES["full_max_drawdown_min"],
        "severe_full_cagr_positive": float(severe_row.full_cagr) > 0,
        "extreme_full_cagr_positive": float(extreme_row.full_cagr) > 0,
        "latency_full_cagr_positive": float(delay_row.full_cagr) > 0,
        "worst_calendar_year": worst_year >= POST_OOS_GATES["worst_calendar_year_min"],
        "full_long_leg_pnl_positive": float(base_full_diagnostics["long_leg_pnl"]) > 0,
        "full_short_leg_pnl_positive": float(base_full_diagnostics["short_leg_pnl"]) > 0,
        "all_audits_max_hourly_close_gross": all_audits_max_gross
        <= POST_OOS_GATES["all_audits_max_hourly_close_gross"],
        "all_audits_max_adverse_intrahour_stress_gross": all_audits_max_stress
        <= POST_OOS_GATES["all_audits_max_adverse_intrahour_stress_gross"],
        "concentration": float(base_full_diagnostics["top_positive_asset_pnl_share"])
        <= POST_OOS_GATES["top_positive_asset_pnl_share_max"],
        "forced_exit_count": int(base_full_diagnostics["forced_exit_count"])
        <= POST_OOS_GATES["forced_exit_count_max"],
    }
    standalone_passed = bool(all(post_gate_results.values()))
    decision = {
        "candidate": CANDIDATE,
        "status": (
            "paper_forward_candidate_non_pristine_oos"
            if standalone_passed
            else "rejected_after_oos"
        ),
        "development_reproof_passed": True,
        "oos_opened": True,
        "standalone_selection_passed": standalone_passed,
        "integration_permitted": False,
        "promoted_candidates": [SOURCE_POLICY_NAME] if standalone_passed else [],
        "paper_forward_earliest_start": "2026-07-27" if standalone_passed else None,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    summary = {
        **decision,
        "selection": proof,
        "coverage_gate": coverage_gate,
        "post_oos_gate_results": post_gate_results,
        "base_audit": clean(base_row.to_dict()),
        "severe_audit": clean(severe_row.to_dict()),
        "extreme_audit": clean(extreme_row.to_dict()),
        "delay_audit": clean(delay_row.to_dict()),
        "annual_returns": annual.to_dict(orient="records"),
        "diagnostics_by_audit": diagnostics_by_audit,
        "all_audits_max_hourly_close_gross": all_audits_max_gross,
        "all_audits_max_adverse_intrahour_stress_gross": all_audits_max_stress,
        "limitations": [
            "The alpha policy was generated from V269 development diagnostics.",
            "The hourly controller has one preregistered trigger/reset pair and no neighboring search.",
            "The program-level 2024–2026 holdout is not pristine.",
            "Public hourly bars are not executable bid/ask or queue observations.",
            "A historical pass permits only paper-forward monitoring after 2026-07-27.",
        ],
        "daily_data_manifest_sha256": canonical_hash(daily_records),
        "hourly_data_manifest_sha256": canonical_hash(hourly_records),
    }
    write_json(results / "FROZEN_DECISION.json", decision)
    write_json(results / "summary.json", summary)
    (results / "REPORT_RU.md").write_text(
        "# Active V285–V292 — hourly gross controller\n\n"
        f"Status: `{decision['status']}`.\n\n"
        f"Development CAGR: {float(development_metrics['cagr']):+.2%}; "
        f"max hourly gross: {float(development_metrics['max_gross']):.3f}x; "
        f"max adverse stress gross: {float(development_metrics['max_stress_gross']):.3f}x.\n\n"
        f"Validation: {float(base_row.validation_total_return):+.2%}; "
        f"holdout: {float(base_row.holdout_total_return):+.2%}; "
        f"final: {float(base_row.final_total_return):+.2%}.\n"
    )
    write_manifest(root)
    print(json.dumps(clean(summary), indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--daily-cache", type=Path, default=Path(".cache/v9"))
    parser.add_argument("--hourly-cache", type=Path, default=Path(".cache/v285_hourly"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return run(args.root, args.daily_cache, args.hourly_cache)


if __name__ == "__main__":
    raise SystemExit(main())
