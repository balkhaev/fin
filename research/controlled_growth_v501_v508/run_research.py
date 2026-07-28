#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_SOURCE = REPO_ROOT / "research" / "active_v253_v260" / "run_research.py"
_SPEC = importlib.util.spec_from_file_location("v253_controlled_growth_base", BASE_SOURCE)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"missing pinned V253 dependency: {BASE_SOURCE}")
base = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = base
_SPEC.loader.exec_module(base)

PROGRAM = "V501_V508_CONTROLLED_GROWTH_ENGINE"
SYMBOLS = tuple(base.SYMBOLS)
START = "2021-01-01"
DEVELOPMENT_END = "2024-01-01"
VALIDATION_END = "2025-01-01"
HOLDOUT_END = "2026-01-01"
END_EXCLUSIVE = "2026-07-01"
INITIAL_EQUITY = 10_000.0
FORCED_EXIT_PENALTY_BPS = 100.0

PERIODS = {
    "development": (START, DEVELOPMENT_END),
    "validation_2024": (DEVELOPMENT_END, VALIDATION_END),
    "holdout_2025": (VALIDATION_END, HOLDOUT_END),
    "final_2026h1": (HOLDOUT_END, END_EXCLUSIVE),
    "full": (START, END_EXCLUSIVE),
}

FACTOR_MIXES: dict[str, dict[str, float]] = {
    "quality": {
        "downside_beta": 0.30,
        "idiosyncratic_skewness": 0.25,
        "downside_volatility_ratio": 0.25,
        "residual_resilience": 0.20,
    },
    "growth_quality": {
        "momentum": 0.35,
        "downside_beta": 0.20,
        "idiosyncratic_skewness": 0.15,
        "downside_volatility_ratio": 0.15,
        "market_correlation": 0.05,
        "residual_resilience": 0.10,
    },
    "diversified_growth": {
        "momentum": 0.30,
        "market_correlation": 0.20,
        "downside_volatility_ratio": 0.20,
        "idiosyncratic_skewness": 0.15,
        "residual_resilience": 0.15,
    },
}


@dataclass(frozen=True, slots=True)
class RiskProfile:
    name: str
    floor: float
    ceiling: float
    target_volatility: float
    max_gross: float
    signal_rebalance_days: int
    risk_rebalance_days: int
    smoothing_span: int
    opportunity_power: float
    dd_soft: float
    dd_hard: float
    dd_floor: float
    state_enabled: bool = True


PROFILES = {
    "balanced": RiskProfile(
        "balanced", 0.45, 1.35, 0.55, 1.35, 14, 14, 14, 1.00, 0.07, 0.16, 0.30
    ),
    "growth": RiskProfile(
        "growth", 0.60, 1.55, 0.70, 1.60, 14, 14, 14, 1.00, 0.08, 0.18, 0.35
    ),
    "convex": RiskProfile(
        "convex", 0.30, 1.70, 0.80, 1.70, 7, 7, 7, 1.35, 0.07, 0.15, 0.25
    ),
    "constant_100": RiskProfile(
        "constant_100", 1.00, 1.00, 1.00, 1.00, 14, 28, 1, 1.00, 0.08, 0.18, 0.35, False
    ),
    "constant_150": RiskProfile(
        "constant_150", 1.50, 1.50, 1.50, 1.50, 14, 28, 1, 1.00, 0.08, 0.18, 0.35, False
    ),
}


@dataclass(frozen=True, slots=True)
class Policy:
    factor_mix: str
    top_k: int
    profile: str
    promotable: bool = True

    @property
    def name(self) -> str:
        return f"{self.factor_mix}_k{self.top_k}_{self.profile}"


POLICIES = tuple(
    Policy(mix, top_k, profile)
    for mix in FACTOR_MIXES
    for top_k in (3, 4)
    for profile in ("balanced", "growth", "convex")
)
CONTROLS = (
    Policy("growth_quality", 3, "constant_100", False),
    Policy("growth_quality", 3, "constant_150", False),
)


@dataclass(frozen=True, slots=True)
class Audit:
    name: str
    cost_bps_per_side: float
    annual_financing_rate: float
    funding_multiplier: float
    initial_margin_ratio: float
    maintenance_margin_ratio: float
    operational_reserve: float
    execution_delay_days: int = 0
    intraday_widen: float = 0.0

    @property
    def cost_rate(self) -> float:
        return self.cost_bps_per_side / 10_000.0


AUDITS = (
    Audit("base", 30.0, 0.08, 1.0, 0.25, 0.10, 0.20),
    Audit("severe", 60.0, 0.12, 2.0, 0.30, 0.12, 0.22),
    Audit("extreme", 100.0, 0.18, 3.0, 0.40, 0.15, 0.25, 1, 0.10),
    Audit("delay_1d", 30.0, 0.08, 1.0, 0.25, 0.10, 0.20, 1),
)

DEVELOPMENT_GATES = {
    "cagr_min": 0.50,
    "sharpe_min": 1.40,
    "max_drawdown_min": -0.25,
    "severe_cagr_min": 0.35,
    "extreme_cagr_min": 0.15,
    "annual_turnover_max": 18.0,
    "all_years_positive": True,
    "top_positive_asset_pnl_share_max": 0.45,
    "liquidations_max": 0,
    "minimum_margin_buffer": 0.10,
}
POST_OOS_GATES = {
    "full_cagr_min": 0.50,
    "full_sharpe_min": 1.30,
    "full_max_drawdown_min": -0.25,
    "severe_full_cagr_min": 0.35,
    "extreme_full_cagr_min": 0.15,
    "worst_calendar_year_min": -0.10,
    "liquidations_max": 0,
    "minimum_margin_buffer": 0.10,
}


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [clean(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if np.isfinite(number) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if pd.isna(value):
        return None
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(clean(value), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        clean(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sigmoid(value: pd.Series) -> pd.Series:
    clipped = value.clip(-12.0, 12.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def cross_sectional_rank(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.rank(axis=1, pct=True, method="average")


def rolling_correlation(frame: pd.DataFrame, market_return: pd.Series, window: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            symbol: frame[symbol].rolling(window, min_periods=window).corr(market_return)
            for symbol in frame.columns
        },
        index=frame.index,
    )


def drawdown_duration(values: np.ndarray) -> float:
    if len(values) == 0 or not np.isfinite(values).all():
        return np.nan
    path = np.cumsum(values)
    peak = np.maximum.accumulate(path)
    underwater = path < peak - 1e-12
    longest = current = 0
    for flag in underwater:
        current = current + 1 if flag else 0
        longest = max(longest, current)
    return float(longest) / float(len(values))


def factor_book(market: Any) -> dict[str, pd.DataFrame]:
    beta90 = market.beta(90).shift(1)
    beta180 = market.beta(180).shift(1)
    residual90 = market.logret - beta90.mul(market.market, axis=0)
    residual180 = market.logret - beta180.mul(market.market, axis=0)

    downside_mask = market.market.lt(0.0)
    downside_market = market.market.where(downside_mask)
    denominator = downside_market.rolling(90, min_periods=30).var()
    downside_beta = pd.DataFrame(index=market.index, columns=market.symbols, dtype=float)
    for symbol in market.symbols:
        downside_asset = market.logret[symbol].where(downside_mask)
        covariance = downside_asset.rolling(90, min_periods=30).cov(downside_market)
        downside_beta[symbol] = covariance / denominator.replace(0.0, np.nan)

    idiosyncratic_skewness = residual180.rolling(180, min_periods=180).skew()
    downside = residual90.clip(upper=0.0).abs()
    short_downside = downside.pow(2).rolling(14, min_periods=14).mean().pow(0.5)
    long_downside = downside.pow(2).rolling(90, min_periods=90).mean().pow(0.5)
    downside_ratio = short_downside / long_downside.replace(0.0, np.nan)
    correlation = rolling_correlation(market.logret, market.market, 120)
    resilience = residual90.apply(
        lambda column: column.rolling(240, min_periods=240).apply(drawdown_duration, raw=True)
    )
    momentum = (
        0.50 * market.close.pct_change(63, fill_method=None)
        + 0.50 * market.close.pct_change(126, fill_method=None)
    ) / market.vol.replace(0.0, np.nan)

    raw = {
        "downside_beta": -downside_beta,
        "idiosyncratic_skewness": -idiosyncratic_skewness,
        "downside_volatility_ratio": -downside_ratio,
        "market_correlation": -correlation,
        "residual_resilience": -resilience,
        "momentum": momentum,
    }
    return {name: cross_sectional_rank(frame) for name, frame in raw.items()}


def composite_score(factors: dict[str, pd.DataFrame], mix_name: str) -> pd.DataFrame:
    mix = FACTOR_MIXES[mix_name]
    score: pd.DataFrame | None = None
    available_weight = pd.DataFrame(0.0, index=next(iter(factors.values())).index, columns=SYMBOLS)
    for factor, weight in mix.items():
        contribution = factors[factor] * weight
        score = contribution if score is None else score.add(contribution, fill_value=0.0)
        available_weight = available_weight.add(factors[factor].notna().astype(float) * weight)
    if score is None:
        raise RuntimeError("empty factor mix")
    return score.divide(available_weight.replace(0.0, np.nan))


def build_raw_long_weights(market: Any, score: pd.DataFrame, mix_name: str, top_k: int) -> pd.DataFrame:
    ema100 = market.close.ewm(span=100, adjust=False, min_periods=100).mean()
    ema150 = market.close.ewm(span=150, adjust=False, min_periods=150).mean()
    ema200 = market.close.ewm(span=200, adjust=False, min_periods=200).mean()
    ret63 = market.close.pct_change(63, fill_method=None)
    ret126 = market.close.pct_change(126, fill_method=None)
    if mix_name == "quality":
        trend = (market.close > ema200) & (ret63 > 0.0)
    elif mix_name == "growth_quality":
        trend = (market.close > ema100) & (ret126 > 0.0)
    else:
        trend = (market.close > ema150) & (ret63 > 0.0)

    values = score.reindex(market.index).to_numpy(float)
    volatility = market.vol.to_numpy(float)
    valid_mask = (market.available & trend).to_numpy(bool)
    output = np.zeros_like(values)
    for i in range(len(values)):
        valid = np.flatnonzero(
            valid_mask[i]
            & np.isfinite(values[i])
            & np.isfinite(volatility[i])
            & (volatility[i] > 1e-6)
        )
        if len(valid) < top_k:
            continue
        selected = valid[np.argsort(values[i, valid])[-top_k:]]
        invvol = 1.0 / volatility[i, selected]
        invvol /= invvol.sum()
        equal = np.full(top_k, 1.0 / top_k)
        strength = np.clip(values[i, selected], 0.05, 1.0)
        strength /= strength.sum()
        weights = 0.40 * equal + 0.40 * invvol + 0.20 * strength
        weights /= weights.sum()
        output[i, selected] = weights
    return pd.DataFrame(output, index=market.index, columns=market.symbols)


def schedule_weights(
    raw: pd.DataFrame,
    available: pd.DataFrame,
    every: int,
    band: float = 0.18,
) -> pd.DataFrame:
    values = raw.to_numpy(float)
    availability = available.reindex(raw.index).to_numpy(bool)
    output = np.zeros_like(values)
    current = np.zeros(values.shape[1], dtype=float)
    for i, row in enumerate(values):
        current[~availability[i]] = 0.0
        candidate = np.nan_to_num(row, nan=0.0, posinf=0.0, neginf=0.0)
        candidate[~availability[i]] = 0.0
        urgent_exit = bool(np.any((current > 1e-12) & (candidate <= 1e-12)))
        scheduled = i % every == 0 and float(np.abs(candidate - current).sum()) >= band
        if urgent_exit or scheduled:
            current = candidate.copy()
        total = float(current.sum())
        if total > 1.0:
            current /= total
        output[i] = current
    return pd.DataFrame(output, index=raw.index, columns=raw.columns)


def load_state(path: Path, index: pd.DatetimeIndex) -> pd.DataFrame:
    state = pd.read_csv(path)
    state["open_time"] = pd.to_datetime(state["open_time"], utc=True)
    state = state.set_index("open_time").sort_index().reindex(index)
    required = {
        "trend", "breadth", "stress", "rotation", "liquidity", "leverage",
        "assignment_confidence", "novelty_flag", "state_duration_days",
    }
    missing = sorted(required - set(state.columns))
    if missing:
        raise ValueError(f"market-state file lacks {missing}")
    return state


def build_risk_budget(
    market: Any,
    state: pd.DataFrame,
    unit_weights: pd.DataFrame,
    profile: RiskProfile,
) -> pd.Series:
    if not profile.state_enabled:
        return pd.Series(profile.floor, index=market.index, dtype=float)

    raw_opportunity = (
        0.32 * pd.to_numeric(state["trend"], errors="coerce")
        + 0.23 * pd.to_numeric(state["breadth"], errors="coerce")
        + 0.15 * pd.to_numeric(state["liquidity"], errors="coerce")
        + 0.08 * pd.to_numeric(state["leverage"], errors="coerce")
        - 0.15 * pd.to_numeric(state["stress"], errors="coerce")
        - 0.12 * pd.to_numeric(state["rotation"], errors="coerce")
    )
    opportunity = sigmoid(raw_opportunity / 1.50).pow(profile.opportunity_power)
    confidence = 0.55 + 0.45 * pd.to_numeric(
        state["assignment_confidence"], errors="coerce"
    ).clip(0.0, 1.0)
    novelty = np.where(
        state["novelty_flag"].astype(str).str.lower().isin({"true", "1"}), 0.72, 1.0
    )
    duration = pd.to_numeric(state["state_duration_days"], errors="coerce").fillna(1.0)
    persistence = np.where(duration > 5.0, 0.90 + 0.15 * opportunity, 0.85)
    state_fraction = (opportunity * confidence * novelty * persistence).clip(0.0, 1.0)
    state_budget = profile.floor + (profile.ceiling - profile.floor) * state_fraction

    unit_return = (unit_weights.shift(1) * market.returns).sum(axis=1).fillna(0.0)
    realized_vol = unit_return.rolling(42, min_periods=30).std(ddof=1) * math.sqrt(365.0)
    vol_scale = (profile.target_volatility / realized_vol.replace(0.0, np.nan)).clip(0.65, 1.35)
    raw_budget = (state_budget * vol_scale.fillna(0.75)).clip(0.15, profile.max_gross)
    raw_budget = raw_budget.ewm(span=profile.smoothing_span, adjust=False).mean()

    output = np.zeros(len(raw_budget), dtype=float)
    current = float(profile.floor)
    values = raw_budget.to_numpy(float)
    for i, value in enumerate(values):
        candidate = float(value) if np.isfinite(value) else float(profile.floor)
        urgent_reduction = candidate < current - 0.20
        scheduled = i % profile.risk_rebalance_days == 0 and abs(candidate - current) >= 0.08
        if urgent_reduction or scheduled:
            current = candidate
        output[i] = min(profile.max_gross, max(0.0, current))
    return pd.Series(output, index=market.index, name="risk_budget")


def drawdown_multiplier(drawdown: float, profile: RiskProfile) -> float:
    if drawdown >= -profile.dd_soft:
        return 1.0
    if drawdown >= -profile.dd_hard:
        fraction = (-drawdown - profile.dd_soft) / (profile.dd_hard - profile.dd_soft)
        return 1.0 - fraction * (1.0 - profile.dd_floor)
    if drawdown >= -0.25:
        fraction = (-drawdown - profile.dd_hard) / max(0.25 - profile.dd_hard, 1e-6)
        return profile.dd_floor - fraction * max(profile.dd_floor - 0.12, 0.0)
    return 0.10


def simulate(
    market: Any,
    unit_weights: pd.DataFrame,
    budget: pd.Series,
    profile: RiskProfile,
    start: str,
    end_exclusive: str,
    audit: Audit,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    index = market.index
    selected = np.flatnonzero(
        (index >= pd.Timestamp(start, tz="UTC"))
        & (index < pd.Timestamp(end_exclusive, tz="UTC"))
    )
    if not len(selected):
        return pd.DataFrame(), {}

    opens = market.open.to_numpy(float)
    highs = market.high.to_numpy(float)
    lows = market.low.to_numpy(float)
    closes = market.close.to_numpy(float)
    funding = market.funding.to_numpy(float) * audit.funding_multiplier
    available = market.available.to_numpy(bool)
    units = unit_weights.reindex(index).fillna(0.0).to_numpy(float)
    budgets = budget.reindex(index).fillna(0.0).to_numpy(float)

    notional = np.zeros(len(market.symbols), dtype=float)
    equity = INITIAL_EQUITY
    high_water = INITIAL_EQUITY
    previous = -1
    rows: list[dict[str, Any]] = []
    last_target: np.ndarray | None = None
    liquidations = 0
    forced_exits = 0
    asset_pnl = {symbol: 0.0 for symbol in market.symbols}
    ever_traded: set[str] = set()

    def allocate(value: float, j: int) -> None:
        asset_pnl[market.symbols[j]] += float(value)

    for i in selected:
        start_equity = max(equity, 1e-12)
        day_cost = day_funding = day_price = day_financing = 0.0
        day_liquidation = day_forced = 0

        if previous >= 0:
            for j in np.flatnonzero(notional > 1e-12):
                if np.isfinite(opens[i, j]) and np.isfinite(closes[previous, j]) and closes[previous, j] > 0:
                    ratio = opens[i, j] / closes[previous, j]
                    pnl = float(notional[j] * (ratio - 1.0))
                    equity += pnl
                    day_price += pnl
                    allocate(pnl, j)
                    notional[j] *= ratio
                else:
                    penalty_rate = max(audit.cost_rate, FORCED_EXIT_PENALTY_BPS / 10_000.0)
                    penalty = float(notional[j] * penalty_rate)
                    equity -= penalty
                    day_cost += penalty
                    allocate(-penalty, j)
                    notional[j] = 0.0
                    forced_exits += 1
                    day_forced += 1

        equity_open = max(equity, 1e-12)
        current_drawdown = equity_open / max(high_water, 1e-12) - 1.0
        dd_scale = drawdown_multiplier(current_drawdown, profile)
        signal_index = i - 1 - audit.execution_delay_days
        if signal_index >= 0:
            target = units[signal_index].copy() * budgets[signal_index] * dd_scale
        else:
            target = np.zeros(len(market.symbols), dtype=float)
        target[~available[i]] = 0.0
        target = np.clip(target, 0.0, None)
        target_gross = float(target.sum())
        if target_gross > profile.max_gross:
            target *= profile.max_gross / target_gross
            target_gross = profile.max_gross
        margin_cap = max(0.0, (1.0 - audit.operational_reserve) / audit.initial_margin_ratio)
        if target_gross > margin_cap:
            target *= margin_cap / target_gross
            target_gross = margin_cap

        current_gross = float(notional.sum() / equity_open)
        target_changed = bool(
            last_target is None or not np.allclose(target, last_target, rtol=0.0, atol=1e-12)
        )
        need_rebalance = target_changed or current_gross > profile.max_gross + 1e-12 or day_forced > 0
        turnover = 0.0
        rebalance_event = 0
        if need_rebalance:
            desired = target * equity_open
            delta = desired - notional
            turnover_notional = float(np.abs(delta).sum())
            turnover = turnover_notional / equity_open
            if turnover_notional > 0:
                cost_by_asset = np.abs(delta) * audit.cost_rate
                for j in np.flatnonzero(cost_by_asset > 0):
                    allocate(-float(cost_by_asset[j]), j)
                trade_cost = float(cost_by_asset.sum())
                equity -= trade_cost
                day_cost += trade_cost
                notional = target * max(equity, 0.0)
                ever_traded.update(
                    market.symbols[j] for j in np.flatnonzero(notional > 1e-12)
                )
                rebalance_event = int(turnover > 1e-4)
            last_target = target.copy()

        gross_open = float(notional.sum() / max(equity, 1e-12))
        financing = max(0.0, gross_open - 1.0) * max(equity, 0.0) * audit.annual_financing_rate / 365.0
        equity -= financing
        day_financing += financing

        active = np.flatnonzero(notional > 1e-12)
        stress_ratio = np.ones(len(market.symbols), dtype=float)
        stress_valid = True
        for j in active:
            if not (np.isfinite(opens[i, j]) and opens[i, j] > 0 and np.isfinite(lows[i, j])):
                stress_valid = False
                break
            stress_ratio[j] = max(0.0, lows[i, j] * (1.0 - audit.intraday_widen) / opens[i, j])
        if stress_valid:
            stress_pnl = float(np.sum(notional * (stress_ratio - 1.0)))
            stress_equity = equity + stress_pnl
            stress_notional = notional * stress_ratio
            maintenance = audit.maintenance_margin_ratio * float(stress_notional.sum())
            margin_buffer = (stress_equity - maintenance) / max(equity_open, 1e-12)
            stress_gross = (
                float(stress_notional.sum() / stress_equity) if stress_equity > 1e-12 else 1e6
            )
        else:
            margin_buffer = -1.0
            stress_gross = 1e6

        if margin_buffer < 0.0 and active.size:
            liquidation_notional = float(notional.sum())
            penalty = liquidation_notional * 0.01
            for j in active:
                loss = float(notional[j] * (stress_ratio[j] - 1.0))
                allocate(loss - penalty * notional[j] / liquidation_notional, j)
            equity += float(np.sum(notional * (stress_ratio - 1.0))) - penalty
            day_price += float(np.sum(notional * (stress_ratio - 1.0)))
            day_cost += penalty
            notional[:] = 0.0
            liquidations += 1
            day_liquidation = 1
        else:
            valid = np.isfinite(opens[i]) & np.isfinite(closes[i]) & (opens[i] > 0)
            close_ratio = np.divide(closes[i], opens[i], out=np.ones(len(market.symbols)), where=valid)
            for j in active:
                price_pnl = float(notional[j] * (close_ratio[j] - 1.0))
                funding_pnl = float(-(notional[j] * funding[i, j]))
                equity += price_pnl + funding_pnl
                day_price += price_pnl
                day_funding += funding_pnl
                allocate(price_pnl + funding_pnl, j)
                notional[j] *= close_ratio[j]

        equity = max(0.0, equity)
        high_water = max(high_water, equity)
        close_gross = float(notional.sum() / max(equity, 1e-12))
        rows.append(
            {
                "equity": equity,
                "daily_return": equity / start_equity - 1.0,
                "gross": close_gross,
                "stress_gross": stress_gross,
                "turnover": turnover,
                "costs": day_cost,
                "funding_pnl": day_funding,
                "price_pnl": day_price,
                "financing_drag": day_financing,
                "margin_buffer": margin_buffer,
                "liquidations": day_liquidation,
                "forced_exits": day_forced,
                "rebalance_events": rebalance_event,
                "risk_budget": target_gross,
                "drawdown_scale": dd_scale,
            }
        )
        previous = int(i)

    account = pd.DataFrame(rows, index=index[selected])
    positive = [max(0.0, value) for value in asset_pnl.values()]
    positive_total = float(sum(positive))
    diagnostics = {
        "asset_pnl": asset_pnl,
        "top_positive_asset_pnl_share": max(positive) / positive_total if positive_total > 0 else 1.0,
        "symbols_traded": sorted(ever_traded),
        "symbol_count_traded": len(ever_traded),
        "liquidation_count": liquidations,
        "forced_exit_count": forced_exits,
        "rebalance_events": int(account.rebalance_events.sum()) if not account.empty else 0,
    }
    return account, diagnostics


def elapsed_years(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 0.0
    return max((index[-1] - index[0]).total_seconds() / (365.2425 * 86400.0), 1 / 365.2425)


def account_metrics(account: pd.DataFrame) -> dict[str, Any]:
    if account.empty:
        return {key: 0.0 for key in (
            "total_return", "cagr", "sharpe", "max_drawdown", "annual_turnover",
            "average_gross", "max_gross", "max_stress_gross", "minimum_margin_buffer",
        )} | {"liquidations": 0, "forced_exits": 0, "final_equity": INITIAL_EQUITY}
    returns = pd.to_numeric(account["daily_return"], errors="coerce").fillna(0.0)
    equity = INITIAL_EQUITY * (1.0 + returns).cumprod()
    years = elapsed_years(account.index)
    total = float(equity.iloc[-1] / INITIAL_EQUITY - 1.0)
    cagr = float((equity.iloc[-1] / INITIAL_EQUITY) ** (1.0 / years) - 1.0) if years > 0 else 0.0
    std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    drawdown = equity / equity.cummax() - 1.0
    return {
        "total_return": total,
        "cagr": cagr,
        "sharpe": float(returns.mean() / std * math.sqrt(365.0)) if std > 0 else 0.0,
        "max_drawdown": float(drawdown.min()),
        "annual_turnover": float(account.turnover.sum() / years) if years > 0 else 0.0,
        "average_gross": float(account.gross.mean()),
        "max_gross": float(account.gross.max()),
        "max_stress_gross": float(account.stress_gross.max()),
        "minimum_margin_buffer": float(account.margin_buffer.min()),
        "liquidations": int(account.liquidations.sum()),
        "forced_exits": int(account.forced_exits.sum()),
        "costs": float(account.costs.sum()),
        "funding_pnl": float(account.funding_pnl.sum()),
        "financing_drag": float(account.financing_drag.sum()),
        "rebalance_events": int(account.rebalance_events.sum()),
        "average_risk_budget": float(account.risk_budget.mean()),
        "final_equity": float(equity.iloc[-1]),
    }


def period_metrics(account: pd.DataFrame, period: str) -> dict[str, Any]:
    start, end = PERIODS[period]
    selected = account.loc[
        (account.index >= pd.Timestamp(start, tz="UTC"))
        & (account.index < pd.Timestamp(end, tz="UTC"))
    ]
    return account_metrics(selected)


def yearly_returns(account: pd.DataFrame) -> pd.DataFrame:
    returns = pd.to_numeric(account.daily_return, errors="coerce").fillna(0.0)
    return pd.DataFrame(
        [
            {"year": int(year), "return": float((1.0 + group).prod() - 1.0)}
            for year, group in returns.groupby(returns.index.year)
        ]
    )


def score_candidate(metrics: dict[str, Any], severe: dict[str, Any], extreme: dict[str, Any]) -> float:
    return float(
        metrics["cagr"]
        + 0.08 * metrics["sharpe"]
        - 0.30 * abs(metrics["max_drawdown"])
        + 0.10 * severe["cagr"]
        + 0.05 * extreme["cagr"]
        - 0.0015 * metrics["annual_turnover"]
    )


def development_gate_results(
    metrics: dict[str, Any],
    severe: dict[str, Any],
    extreme: dict[str, Any],
    annual: pd.DataFrame,
    diagnostics: dict[str, Any],
) -> dict[str, bool]:
    return {
        "cagr": float(metrics["cagr"]) >= DEVELOPMENT_GATES["cagr_min"],
        "sharpe": float(metrics["sharpe"]) >= DEVELOPMENT_GATES["sharpe_min"],
        "max_drawdown": float(metrics["max_drawdown"]) >= DEVELOPMENT_GATES["max_drawdown_min"],
        "severe_cagr": float(severe["cagr"]) >= DEVELOPMENT_GATES["severe_cagr_min"],
        "extreme_cagr": float(extreme["cagr"]) >= DEVELOPMENT_GATES["extreme_cagr_min"],
        "annual_turnover": float(metrics["annual_turnover"]) <= DEVELOPMENT_GATES["annual_turnover_max"],
        "all_years_positive": bool(not annual.empty and (annual["return"] > 0.0).all()),
        "concentration": float(diagnostics["top_positive_asset_pnl_share"])
        <= DEVELOPMENT_GATES["top_positive_asset_pnl_share_max"],
        "liquidations": int(metrics["liquidations"]) <= DEVELOPMENT_GATES["liquidations_max"],
        "margin_buffer": float(metrics["minimum_margin_buffer"])
        >= DEVELOPMENT_GATES["minimum_margin_buffer"],
    }


def self_test() -> None:
    assert len(POLICIES) == 18
    assert len(CONTROLS) == 2
    assert max(profile.max_gross for profile in PROFILES.values()) <= 1.70
    index = pd.date_range("2018-01-01", periods=1500, freq="1D", tz="UTC")
    rng = np.random.default_rng(501)
    common = rng.normal(0.0004, 0.012, len(index))
    klines: dict[str, pd.DataFrame] = {}
    funding: dict[str, pd.Series] = {}
    for number, symbol in enumerate(SYMBOLS):
        residual = rng.normal(0.0001 + number * 0.00001, 0.008 + number * 0.0002, len(index))
        close = 100.0 * np.exp(np.cumsum(0.65 * common + residual))
        open_price = np.r_[close[0], close[:-1] * np.exp(rng.normal(0.0, 0.001, len(index) - 1))]
        width = np.abs(rng.normal(0.012, 0.004, len(index)))
        klines[symbol] = pd.DataFrame(
            {
                "open": open_price,
                "high": np.maximum(open_price, close) * (1.0 + width),
                "low": np.minimum(open_price, close) / (1.0 + width),
                "close": close,
                "volume": 1.0,
                "quote_volume": 1.0,
                "trades": 1.0,
                "taker_buy_base": 0.5,
                "taker_buy_quote": 0.5,
            },
            index=index,
        )
        funding[symbol] = pd.Series(0.0, index=index)
    market = base.Market(klines, funding)
    factors = factor_book(market)
    score = composite_score(factors, "growth_quality")
    raw = build_raw_long_weights(market, score, "growth_quality", 3)
    scheduled = schedule_weights(raw, market.available, 14)
    state = pd.DataFrame(
        {
            "trend": 0.5,
            "breadth": 0.5,
            "stress": 0.0,
            "rotation": 0.0,
            "liquidity": 0.5,
            "leverage": 0.2,
            "assignment_confidence": 0.7,
            "novelty_flag": False,
            "state_duration_days": 10,
        },
        index=index,
    )
    budget = build_risk_budget(market, state, scheduled, PROFILES["growth"])
    account, diagnostics = simulate(
        market, scheduled, budget, PROFILES["growth"], "2021-01-01", "2022-01-01", AUDITS[0]
    )
    assert not account.empty
    assert np.isfinite(account.equity).all()
    assert float(account.gross.max()) <= PROFILES["growth"].max_gross + 1e-9
    assert diagnostics["liquidation_count"] == 0
    changed = {symbol: frame.copy() for symbol, frame in klines.items()}
    changed[SYMBOLS[0]].iloc[-1, changed[SYMBOLS[0]].columns.get_loc("close")] *= 5.0
    changed_market = base.Market(changed, funding)
    changed_score = composite_score(factor_book(changed_market), "growth_quality")
    changed_raw = build_raw_long_weights(changed_market, changed_score, "growth_quality", 3)
    pd.testing.assert_frame_equal(raw.iloc[:-1], changed_raw.iloc[:-1])
    print("V501-V508 controlled-growth causal self-test passed")


def write_manifest(root: Path) -> None:
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative = str(path.relative_to(root))
        if relative in {"MANIFEST.json", "run.log"}:
            continue
        files[relative] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    write_json(root / "MANIFEST.json", {"program": PROGRAM, "files": files})


def run(root: Path, cache: Path, state_path: Path) -> int:
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True)
    config = base.V9Config(
        symbols=SYMBOLS,
        start="2020-01-01",
        end_exclusive=END_EXCLUSIVE,
        interval="1d",
        starting_equity=INITIAL_EQUITY,
        max_gross=1.70,
        forced_exit_penalty_bps=FORCED_EXIT_PENALTY_BPS,
    )
    klines, funding, records, quality = base.load_v9(config, cache, False)
    pd.DataFrame(records).to_csv(results / "data_manifest.csv", index=False)
    pd.DataFrame(quality).to_csv(results / "data_quality.csv", index=False)
    coverage = base.data_gate(klines, records)
    coverage["candidate"] = "V501_FIXED_UNIVERSE_DATA_COVERAGE"
    write_json(results / "coverage_gate.json", coverage)
    if not coverage["passed"]:
        summary = {
            "program": PROGRAM,
            "status": "data_access_insufficient",
            "data_gate_passed": False,
            "integration_permitted": False,
            "capital_change_authorized": False,
            "live_ready": False,
            "real_leverage_authorized": False,
            "profitability_proven": False,
        }
        write_json(results / "summary.json", summary)
        write_json(results / "FROZEN_DECISION.json", summary)
        (results / "REPORT_RU.md").write_text("# V501–V508\n\nData gate failed; no P&L was calculated.\n")
        write_manifest(root)
        return 0

    market = base.Market(klines, funding)
    state = load_state(state_path, market.index)
    factors = factor_book(market)
    score_cache = {mix: composite_score(factors, mix) for mix in FACTOR_MIXES}
    unit_cache: dict[tuple[str, int, str], pd.DataFrame] = {}
    budget_cache: dict[tuple[str, int, str], pd.Series] = {}

    def components(policy: Policy) -> tuple[pd.DataFrame, pd.Series, RiskProfile]:
        profile = PROFILES[policy.profile]
        unit_key = (policy.factor_mix, policy.top_k, profile.name)
        if unit_key not in unit_cache:
            raw = build_raw_long_weights(
                market, score_cache[policy.factor_mix], policy.factor_mix, policy.top_k
            )
            unit_cache[unit_key] = schedule_weights(
                raw, market.available, profile.signal_rebalance_days
            )
        if unit_key not in budget_cache:
            budget_cache[unit_key] = build_risk_budget(
                market, state, unit_cache[unit_key], profile
            )
        return unit_cache[unit_key], budget_cache[unit_key], profile

    rows: list[dict[str, Any]] = []
    dev_accounts: dict[str, pd.DataFrame] = {}
    for number, policy in enumerate((*POLICIES, *CONTROLS), 1):
        unit, budget, profile = components(policy)
        audit_results: dict[str, tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]] = {}
        for audit in AUDITS[:3]:
            account, diagnostics = simulate(
                market, unit, budget, profile, START, DEVELOPMENT_END, audit
            )
            audit_results[audit.name] = (account, diagnostics, account_metrics(account))
        base_account, base_diagnostics, metrics = audit_results["base"]
        severe = audit_results["severe"][2]
        extreme = audit_results["extreme"][2]
        annual = yearly_returns(base_account)
        gates = development_gate_results(metrics, severe, extreme, annual, base_diagnostics)
        eligible = bool(policy.promotable and all(gates.values()))
        row = {
            "policy": policy.name,
            "promotable": policy.promotable,
            "factor_mix": policy.factor_mix,
            "top_k": policy.top_k,
            "risk_profile": policy.profile,
            "eligible": eligible,
            "score": score_candidate(metrics, severe, extreme),
            **{f"development_{key}": value for key, value in metrics.items()},
            "development_severe_cagr": severe["cagr"],
            "development_extreme_cagr": extreme["cagr"],
            "development_top_positive_asset_pnl_share": base_diagnostics[
                "top_positive_asset_pnl_share"
            ],
            "development_annual_returns": clean(annual.to_dict(orient="records")),
            "gate_results": gates,
        }
        rows.append(row)
        dev_accounts[policy.name] = base_account
        print(
            f"{number}/{len(POLICIES)+len(CONTROLS)} {policy.name} "
            f"CAGR={metrics['cagr']:.4f} DD={metrics['max_drawdown']:.4f} eligible={eligible}",
            flush=True,
        )

    ranking = pd.DataFrame(
        [
            {key: value for key, value in row.items() if key not in {"gate_results", "development_annual_returns"}}
            for row in rows
        ]
    ).sort_values(["eligible", "score"], ascending=[False, False])
    ranking.to_csv(results / "development_ranking.csv", index=False)
    write_json(results / "development_details.json", rows)

    promotable_rows = [row for row in rows if row["promotable"]]
    eligible_rows = [row for row in promotable_rows if row["eligible"]]
    selected_row = max(eligible_rows or promotable_rows, key=lambda row: row["score"])
    selected_name = str(selected_row["policy"])
    selected_policy = next(policy for policy in POLICIES if policy.name == selected_name)
    development_passed = bool(eligible_rows)
    proof = {
        "program": PROGRAM,
        "design_sha256": sha256_file(root / "V501_V508_DESIGN.json"),
        "selection_period": [START, DEVELOPMENT_END],
        "selection_uses_2024": False,
        "selection_uses_2025": False,
        "selection_uses_2026": False,
        "program_level_holdout_pristine": False,
        "policy_count": len(POLICIES),
        "control_count": len(CONTROLS),
        "eligible_policy_count": len(eligible_rows),
        "development_passed": development_passed,
        "selected_policy": selected_name,
        "selected_policy_is_diagnostic_only": not development_passed,
        "selected_development": selected_row,
        "development_gates": DEVELOPMENT_GATES,
        "post_oos_gates": POST_OOS_GATES,
        "source_commits": {
            "v253_base": "751b519d74d10f58ee4433783f6d9abd38d1d148",
            "v269_low_skew": "ac09ad1206ffc0af349519ec0cda7408e2f6bd46",
            "v341_vol_term": "7907703aa909548fed491032c51d89fb7cd900a6",
            "v357_correlation": "a3f81dc4fa969932cd79d0684e68b976aacb5762",
            "v389_resilience": "f422b5a191896435fc07682d843e365206255374"
        },
    }
    proof["selection_proof_sha256"] = canonical_hash(proof)
    write_json(results / "selection_proof_before_oos.json", proof)

    unit, budget, profile = components(selected_policy)
    accounts: dict[str, pd.DataFrame] = {}
    diagnostics_by_audit: dict[str, Any] = {}
    audit_rows: list[dict[str, Any]] = []
    for audit in AUDITS:
        account, diagnostics = simulate(
            market, unit, budget, profile, START, END_EXCLUSIVE, audit
        )
        accounts[audit.name] = account
        diagnostics_by_audit[audit.name] = diagnostics
        account.to_csv(results / f"equity_{audit.name}.csv")
        for period in PERIODS:
            audit_rows.append(
                {"audit": audit.name, "period": period, **period_metrics(account, period)}
            )
    audit_table = pd.DataFrame(audit_rows)
    audit_table.to_csv(results / "audit_metrics.csv", index=False)
    annual = yearly_returns(accounts["base"])
    annual.to_csv(results / "ANNUAL_RETURNS.csv", index=False)
    unit.to_csv(results / "selected_unit_weights.csv")
    budget.to_csv(results / "selected_risk_budget.csv")

    def metric(audit: str, period: str) -> dict[str, Any]:
        row = audit_table[(audit_table.audit == audit) & (audit_table.period == period)].iloc[0]
        return clean(row.drop(labels=["audit", "period"]).to_dict())

    base_full = metric("base", "full")
    severe_full = metric("severe", "full")
    extreme_full = metric("extreme", "full")
    validation = metric("base", "validation_2024")
    holdout = metric("base", "holdout_2025")
    final = metric("base", "final_2026h1")
    worst_year = float(annual["return"].min()) if not annual.empty else -1.0
    post_gates = {
        "development_passed": development_passed,
        "validation_positive": float(validation["total_return"]) > 0.0,
        "holdout_positive": float(holdout["total_return"]) > 0.0,
        "final_positive": float(final["total_return"]) > 0.0,
        "full_cagr": float(base_full["cagr"]) >= POST_OOS_GATES["full_cagr_min"],
        "full_sharpe": float(base_full["sharpe"]) >= POST_OOS_GATES["full_sharpe_min"],
        "full_max_drawdown": float(base_full["max_drawdown"])
        >= POST_OOS_GATES["full_max_drawdown_min"],
        "severe_full_cagr": float(severe_full["cagr"])
        >= POST_OOS_GATES["severe_full_cagr_min"],
        "extreme_full_cagr": float(extreme_full["cagr"])
        >= POST_OOS_GATES["extreme_full_cagr_min"],
        "worst_calendar_year": worst_year >= POST_OOS_GATES["worst_calendar_year_min"],
        "liquidations": int(base_full["liquidations"]) <= POST_OOS_GATES["liquidations_max"],
        "margin_buffer": float(base_full["minimum_margin_buffer"])
        >= POST_OOS_GATES["minimum_margin_buffer"],
    }
    passed = bool(all(post_gates.values()))
    if passed:
        status = "controlled_growth_historical_candidate_non_pristine"
    elif development_passed:
        status = "rejected_after_frozen_oos"
    else:
        status = "diagnostic_oos_after_development_failure"

    summary = {
        "program": PROGRAM,
        "status": status,
        "target_cagr": 0.50,
        "selected_policy": selected_name,
        "eligible_policy_count": len(eligible_rows),
        "development_passed": development_passed,
        "oos_opened_for_diagnostic_if_needed": True,
        "selection": proof,
        "metrics": {
            "development": metric("base", "development"),
            "validation_2024": validation,
            "holdout_2025": holdout,
            "final_2026h1": final,
            "full_base": base_full,
            "full_severe": severe_full,
            "full_extreme": extreme_full,
            "full_delay_1d": metric("delay_1d", "full"),
        },
        "annual_returns": clean(annual.to_dict(orient="records")),
        "diagnostics": diagnostics_by_audit,
        "post_oos_gate_results": post_gates,
        "historical_target_passed": passed,
        "limitations": [
            "Factor families were known before this cycle; program-level OOS is not pristine.",
            "Public daily archives are not executable bid/ask or queue observations.",
            "The fixed universe contains EOS through delisting; survivor replacement is prohibited.",
            "A historical pass would require a new frozen forward period before any capital decision."
        ],
        "integration_permitted": False,
        "capital_change_authorized": False,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    write_json(results / "summary.json", summary)
    write_json(
        results / "FROZEN_DECISION.json",
        {
            "program": PROGRAM,
            "status": status,
            "historical_target_passed": passed,
            "selected_policy": selected_name,
            "integration_permitted": False,
            "capital_change_authorized": False,
            "live_ready": False,
            "real_leverage_authorized": False,
            "profitability_proven": False,
        },
    )
    report = (
        "# V501–V508 — controlled growth\n\n"
        f"Status: `{status}`.\n\n"
        f"Selected policy: `{selected_name}`.\n\n"
        f"Development CAGR: {float(metric('base','development')['cagr']):+.2%}; "
        f"Full CAGR: {float(base_full['cagr']):+.2%}; "
        f"Full Sharpe: {float(base_full['sharpe']):.3f}; "
        f"Full Max DD: {float(base_full['max_drawdown']):+.2%}.\n\n"
        f"Validation 2024: {float(validation['total_return']):+.2%}; "
        f"Holdout 2025: {float(holdout['total_return']):+.2%}; "
        f"Final 2026 H1: {float(final['total_return']):+.2%}.\n\n"
        f"Severe full CAGR: {float(severe_full['cagr']):+.2%}; "
        f"Extreme full CAGR: {float(extreme_full['cagr']):+.2%}.\n\n"
        "No capital, live trading or real leverage is authorized.\n"
    )
    (results / "REPORT_RU.md").write_text(report, encoding="utf-8")
    write_manifest(root)
    print(json.dumps(clean(summary), indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.root is None or args.cache is None or args.state is None:
        raise SystemExit("--root, --cache and --state are required")
    return run(args.root, args.cache, args.state)


if __name__ == "__main__":
    raise SystemExit(main())
