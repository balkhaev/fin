"""Forward-only risk, funding, covariance, crisis and execution overlays."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from ._ds40180_common import (
    ANNUAL_DAYS,
    CALM_CORRELATION_THRESHOLD,
    CALM_VOL_THRESHOLD,
    COVARIANCE_LOOKBACK,
    COVARIANCE_SHRINK,
    CRISIS_BREADTH_THRESHOLD,
    CRISIS_BREAKOUT_BARS,
    CRISIS_FAST_EMA,
    CRISIS_GROSS_CAP,
    CRISIS_SLOW_EMA,
    CRISIS_VOLATILITY_BARS,
    EPSILON,
    FUNDING_HARD_ANNUAL,
    FUNDING_MEDIUM_ANNUAL,
    FUNDING_MULTIPLIER_FLOOR,
    FUNDING_SOFT_ANNUAL,
    NO_TRADE_COST_MULTIPLIER,
    NO_TRADE_MIN_BAND,
    PAPER_ASSET_CAP,
    PAPER_EXECUTION_COST,
    PAPER_GROSS_CAP,
    PAPER_GROSS_CAP_BASE,
    PAPER_GROSS_CAP_CALM,
    PAPER_GROSS_CAP_STRESS,
    PAPER_IMPACT_BPS,
    STRESS_CORRELATION,
    STRESS_CORRELATION_THRESHOLD,
    STRESS_VOL_THRESHOLD,
    _clamp,
    _ema,
    _gross,
    _population_standard_deviation,
    _zero_row,
)
from ._ds40180_signals import _apply_target_safety


def _current_funding_rate(history: dict[str, Any]) -> tuple[float | None, float]:
    current = history.get("currentFunding")
    if not isinstance(current, dict):
        return None, 8.0
    raw = current.get("nextFundingRate")
    if raw in (None, ""):
        raw = current.get("fundingRate")
    try:
        rate = float(raw)
    except (TypeError, ValueError):
        return None, 8.0
    if not math.isfinite(rate):
        return None, 8.0
    try:
        funding_time = int(current.get("fundingTime") or 0)
        next_time = int(current.get("nextFundingTime") or 0)
    except (TypeError, ValueError):
        funding_time = next_time = 0
    interval_hours = (
        (next_time - funding_time) / 3_600_000
        if next_time > funding_time > 0
        else 8.0
    )
    return rate, _clamp(interval_hours, 1.0, 24.0)


def _funding_multiplier(adverse_annual: float) -> float:
    if adverse_annual <= FUNDING_SOFT_ANNUAL:
        return 1.0
    if adverse_annual <= FUNDING_MEDIUM_ANNUAL:
        progress = (adverse_annual - FUNDING_SOFT_ANNUAL) / (
            FUNDING_MEDIUM_ANNUAL - FUNDING_SOFT_ANNUAL
        )
        return 1.0 - 0.25 * progress
    if adverse_annual <= FUNDING_HARD_ANNUAL:
        progress = (adverse_annual - FUNDING_MEDIUM_ANNUAL) / (
            FUNDING_HARD_ANNUAL - FUNDING_MEDIUM_ANNUAL
        )
        return 0.75 - 0.25 * progress
    return FUNDING_MULTIPLIER_FLOOR


def apply_funding_guard(
    assets: list[str],
    target: list[float],
    histories: list[dict[str, Any]],
) -> tuple[list[float], dict[str, Any]]:
    history_by_asset = {history["asset"]: history for history in histories}
    adjusted: list[float] = []
    details: dict[str, dict[str, float | None]] = {}
    maximum_adverse = 0.0
    for asset, weight in zip(assets, target, strict=True):
        rate, interval_hours = _current_funding_rate(history_by_asset.get(asset, {}))
        adverse_annual = 0.0
        multiplier = 1.0
        if rate is not None and abs(weight) > EPSILON:
            adverse_rate = rate if weight > 0 else -rate
            adverse_annual = max(0.0, adverse_rate) * 24.0 / interval_hours * ANNUAL_DAYS
            multiplier = _funding_multiplier(adverse_annual)
        maximum_adverse = max(maximum_adverse, adverse_annual)
        adjusted.append(weight * multiplier)
        details[asset] = {
            "fundingRate": rate,
            "intervalHours": interval_hours,
            "adverseAnnual": adverse_annual,
            "multiplier": multiplier,
        }
    return adjusted, {
        "maximumAdverseAnnual": maximum_adverse,
        "assets": details,
    }


def _returns_matrix(
    engine: dict[str, Any], end_exclusive: int, lookback: int = COVARIANCE_LOOKBACK
) -> list[list[float]]:
    start = max(1, end_exclusive - lookback)
    return [list(map(float, row)) for row in engine["returns"][start:end_exclusive]]


def _covariance_matrix(rows: list[list[float]]) -> tuple[list[list[float]], list[float]]:
    if not rows:
        return [], []
    columns = len(rows[0])
    means = [sum(row[column] for row in rows) / len(rows) for column in range(columns)]
    covariance = [[0.0] * columns for _ in range(columns)]
    denominator = max(1, len(rows) - 1)
    for left in range(columns):
        for right in range(columns):
            value = sum(
                (row[left] - means[left]) * (row[right] - means[right])
                for row in rows
            ) / denominator
            covariance[left][right] = value
    variances = [max(covariance[index][index], 0.0) for index in range(columns)]
    for left in range(columns):
        for right in range(columns):
            if left == right:
                continue
            covariance[left][right] *= 1.0 - COVARIANCE_SHRINK
    return covariance, variances


def _quadratic(weights: list[float], matrix: list[list[float]]) -> float:
    return sum(
        weights[left] * matrix[left][right] * weights[right]
        for left in range(len(weights))
        for right in range(len(weights))
    )


def covariance_diagnostics(
    engine: dict[str, Any], target: list[float], decision_index: int
) -> dict[str, float]:
    rows = _returns_matrix(engine, decision_index)
    covariance, variances = _covariance_matrix(rows)
    if not covariance:
        return {
            "projectedVolatility": 0.0,
            "stressVolatility": 0.0,
            "averageAbsoluteCorrelation": 1.0,
        }
    normal_variance = max(0.0, _quadratic(target, covariance))
    normal_vol = math.sqrt(normal_variance * ANNUAL_DAYS)
    stress = [[0.0] * len(target) for _ in target]
    correlations: list[float] = []
    for left in range(len(target)):
        left_vol = math.sqrt(variances[left])
        for right in range(len(target)):
            right_vol = math.sqrt(variances[right])
            if left == right:
                stress[left][right] = variances[left]
                continue
            denominator = left_vol * right_vol
            correlation = covariance[left][right] / denominator if denominator > 0 else 0.0
            correlations.append(abs(correlation))
            stress[left][right] = STRESS_CORRELATION * left_vol * right_vol
    stress_variance = max(0.0, _quadratic(target, stress))
    return {
        "projectedVolatility": normal_vol,
        "stressVolatility": math.sqrt(stress_variance * ANNUAL_DAYS),
        "averageAbsoluteCorrelation": (
            sum(correlations) / len(correlations) if correlations else 1.0
        ),
    }


def select_dynamic_gross_cap(
    covariance: dict[str, float], funding: dict[str, Any]
) -> tuple[float, str]:
    stress_vol = float(covariance["stressVolatility"])
    correlation = float(covariance["averageAbsoluteCorrelation"])
    adverse = float(funding.get("maximumAdverseAnnual") or 0.0)
    if stress_vol >= STRESS_VOL_THRESHOLD or correlation >= STRESS_CORRELATION_THRESHOLD:
        return PAPER_GROSS_CAP_STRESS, "stress"
    if (
        stress_vol <= CALM_VOL_THRESHOLD
        and correlation <= CALM_CORRELATION_THRESHOLD
        and adverse <= FUNDING_MEDIUM_ANNUAL
    ):
        return PAPER_GROSS_CAP_CALM, "calm_diversified"
    return PAPER_GROSS_CAP_BASE, "base"


def _intraday_closes(history: dict[str, Any]) -> list[float]:
    values = history.get("bars4h")
    if not isinstance(values, list):
        return []
    output: list[float] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        try:
            value = float(item["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            output.append(value)
    return output


def crisis_overlay(
    assets: list[str],
    histories: list[dict[str, Any]],
    *,
    daily_bear: bool,
) -> tuple[list[float], dict[str, Any]]:
    if not daily_bear:
        return _zero_row(len(assets)), {"active": False, "reason": "daily_bull"}
    history_by_asset = {history["asset"]: history for history in histories}
    candidates: list[int] = []
    below_fast = 0
    usable = 0
    inverse_volatility: dict[int, float] = {}
    btc_break = False
    for index, asset in enumerate(assets):
        closes = _intraday_closes(history_by_asset.get(asset, {}))
        if len(closes) < max(CRISIS_SLOW_EMA, CRISIS_BREAKOUT_BARS + 1):
            continue
        usable += 1
        ema_fast = _ema(closes, CRISIS_FAST_EMA)[-1]
        ema_slow = _ema(closes, CRISIS_SLOW_EMA)[-1]
        if ema_fast is not None and closes[-1] < ema_fast:
            below_fast += 1
        breakout = closes[-1] < min(closes[-CRISIS_BREAKOUT_BARS - 1 : -1])
        candidate = breakout and ema_slow is not None and closes[-1] < ema_slow
        if candidate:
            candidates.append(index)
            if asset == "BTC":
                btc_break = True
            returns = [closes[pos] / closes[pos - 1] - 1.0 for pos in range(1, len(closes))]
            sample = returns[-CRISIS_VOLATILITY_BARS:]
            deviation = _population_standard_deviation(sample)
            inverse_volatility[index] = 1.0 / deviation if deviation and deviation > 0 else 0.0
    breadth = below_fast / usable if usable else 0.0
    active = btc_break and breadth >= CRISIS_BREADTH_THRESHOLD and bool(candidates)
    if not active:
        return _zero_row(len(assets)), {
            "active": False,
            "reason": "confirmation_missing",
            "breadth": breadth,
            "btcBreak": btc_break,
            "candidates": [assets[index] for index in candidates],
        }
    total = sum(inverse_volatility.get(index, 0.0) for index in candidates)
    overlay = _zero_row(len(assets))
    if total > 0:
        for index in candidates:
            overlay[index] = -CRISIS_GROSS_CAP * inverse_volatility[index] / total
    return overlay, {
        "active": True,
        "breadth": breadth,
        "btcBreak": btc_break,
        "candidates": [assets[index] for index in candidates],
        "gross": _gross(overlay),
    }


def _quote_cost(history: dict[str, Any]) -> float:
    quote = history.get("quote")
    if not isinstance(quote, dict):
        return PAPER_EXECUTION_COST + PAPER_IMPACT_BPS / 10_000.0
    try:
        bid = float(quote.get("bidPx") or 0.0)
        ask = float(quote.get("askPx") or 0.0)
    except (TypeError, ValueError):
        bid = ask = 0.0
    if bid <= 0 or ask <= 0 or ask < bid:
        half_spread = 0.0
    else:
        mid = (bid + ask) / 2.0
        half_spread = (ask - bid) / (2.0 * mid) if mid > 0 else 0.0
    return PAPER_EXECUTION_COST + half_spread + PAPER_IMPACT_BPS / 10_000.0


def apply_no_trade_band(
    assets: list[str],
    current: list[float],
    desired: list[float],
    histories: list[dict[str, Any]],
) -> tuple[list[float], dict[str, Any]]:
    history_by_asset = {history["asset"]: history for history in histories}
    output: list[float] = []
    held: list[str] = []
    bands: dict[str, float] = {}
    for asset, old, new in zip(assets, current, desired, strict=True):
        cost = _quote_cost(history_by_asset.get(asset, {}))
        band = max(NO_TRADE_MIN_BAND, NO_TRADE_COST_MULTIPLIER * cost)
        bands[asset] = band
        sign_flip = old * new < -EPSILON
        exit_required = abs(old) > EPSILON and abs(new) <= EPSILON
        risk_reduction = abs(new) + EPSILON < abs(old)
        if sign_flip or exit_required or risk_reduction or abs(new - old) >= band:
            output.append(new)
        else:
            output.append(old)
            if abs(new - old) > EPSILON:
                held.append(asset)
    return output, {"heldAssets": held, "bands": bands}


def build_forward_plan(
    engine: dict[str, Any],
    histories: list[dict[str, Any]],
    *,
    decision_index: int,
    current_weights: list[float] | None = None,
    use_live_overlays: bool = True,
) -> dict[str, Any]:
    assets = list(engine["assets"])
    raw = list(engine["rawTarget"][decision_index])
    regime_state = int(engine["regimeState"][decision_index])
    overlay, crisis = crisis_overlay(
        assets,
        histories,
        daily_bear=regime_state > 0 and use_live_overlays,
    )
    with_crisis = [left + right for left, right in zip(raw, overlay, strict=True)]
    if use_live_overlays:
        funding_target, funding = apply_funding_guard(assets, with_crisis, histories)
    else:
        funding_target = with_crisis
        funding = {"maximumAdverseAnnual": 0.0, "assets": {}}
    covariance = covariance_diagnostics(engine, funding_target, decision_index)
    dynamic_cap, cap_regime = select_dynamic_gross_cap(covariance, funding)
    desired, safety_applied = _apply_target_safety(
        funding_target,
        gross_cap=min(dynamic_cap, PAPER_GROSS_CAP),
        asset_cap=PAPER_ASSET_CAP,
    )
    no_trade = {"heldAssets": [], "bands": {}}
    executed = desired
    if current_weights is not None:
        executed, no_trade = apply_no_trade_band(
            assets, current_weights, desired, histories
        )
        executed, additional_safety = _apply_target_safety(
            executed,
            gross_cap=min(dynamic_cap, PAPER_GROSS_CAP),
            asset_cap=PAPER_ASSET_CAP,
        )
        safety_applied = safety_applied or additional_safety
    return {
        "rawTarget": raw,
        "crisisAdjustedTarget": with_crisis,
        "fundingAdjustedTarget": funding_target,
        "desiredTarget": desired,
        "executedTarget": executed,
        "dynamicGrossCap": dynamic_cap,
        "grossCapRegime": cap_regime,
        "safetyApplied": safety_applied,
        "funding": funding,
        "covariance": covariance,
        "crisis": crisis,
        "noTrade": no_trade,
    }


def current_weights_from_quantities(
    quantities: Iterable[float], prices: Iterable[float], nav_usd: float
) -> list[float]:
    if nav_usd <= 0:
        raise ValueError("nav_usd must be positive")
    return [
        float(quantity) * float(price) / nav_usd
        for quantity, price in zip(quantities, prices, strict=True)
    ]


def execution_prices(
    assets: list[str],
    histories: list[dict[str, Any]],
    current_weights: list[float],
    desired_weights: list[float],
    fallback_prices: list[float],
) -> tuple[list[float], dict[str, Any]]:
    history_by_asset = {history["asset"]: history for history in histories}
    prices: list[float] = []
    diagnostics: dict[str, Any] = {}
    for asset, old, new, fallback in zip(
        assets, current_weights, desired_weights, fallback_prices, strict=True
    ):
        quote = history_by_asset.get(asset, {}).get("quote")
        bid = ask = float(fallback)
        observed_at = None
        if isinstance(quote, dict):
            try:
                bid_value = float(quote.get("bidPx") or fallback)
                ask_value = float(quote.get("askPx") or fallback)
                if bid_value > 0 and ask_value > 0 and ask_value >= bid_value:
                    bid, ask = bid_value, ask_value
                observed_at = int(quote.get("ts") or 0)
            except (TypeError, ValueError):
                pass
        if new > old + EPSILON:
            selected = ask * (1.0 + PAPER_IMPACT_BPS / 10_000.0)
            side = "buy"
        elif new < old - EPSILON:
            selected = bid * (1.0 - PAPER_IMPACT_BPS / 10_000.0)
            side = "sell"
        else:
            selected = (bid + ask) / 2.0
            side = "hold"
        prices.append(selected)
        diagnostics[asset] = {
            "side": side,
            "bid": bid,
            "ask": ask,
            "selected": selected,
            "observedAtMs": observed_at,
        }
    return prices, diagnostics
