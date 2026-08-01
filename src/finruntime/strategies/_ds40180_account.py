"""Instrument-level paper ledger with fees, funding and live marks."""

from __future__ import annotations

import math
from typing import Any, Iterable

from ._ds40180_common import (
    ANNUAL_DAYS,
    EPSILON,
    INSTRUMENTS,
    MISSING_FUNDING_FALLBACK_ANNUAL,
    PAPER_EXECUTION_COST,
    _candle_close_ms,
    _gross,
    _timestamp_ms,
    _zero_row,
)


def _funding_rates_for_candle(
    history: dict[str, Any], date_text: str
) -> tuple[list[float], bool]:
    start_ms = _timestamp_ms(f"{date_text}T00:00:00+00:00")
    end_ms = _candle_close_ms(date_text)
    rates = [
        float(item["rate"])
        for item in history.get("funding", [])
        if start_ms < int(item["fundingTime"]) <= end_ms
    ]
    return rates, bool(rates)


def _funding_return_for_weight(weight: float, rates: Iterable[float]) -> float:
    return -weight * sum(float(rate) for rate in rates)


def _initial_tracker(
    *, quantity: float, price: float, opened_on: str, trading_cost_usd: float
) -> dict[str, Any]:
    return {
        "averageEntryPrice": price,
        "fundingPnlUsd": 0.0,
        "openedOn": opened_on,
        "quantity": quantity,
        "realizedPnlUsd": 0.0,
        "tradingCostsUsd": trading_cost_usd,
    }


def _rebalance_tracker(
    *,
    desired_quantity: float,
    execution_price: float,
    opened_on: str,
    previous: dict[str, Any] | None,
    trading_cost_usd: float,
) -> dict[str, Any] | None:
    if abs(desired_quantity) <= EPSILON:
        return None
    if previous is None or abs(float(previous["quantity"])) <= EPSILON:
        return _initial_tracker(
            quantity=desired_quantity,
            price=execution_price,
            opened_on=opened_on,
            trading_cost_usd=trading_cost_usd,
        )
    old_quantity = float(previous["quantity"])
    old_direction = math.copysign(1.0, old_quantity)
    new_direction = math.copysign(1.0, desired_quantity)
    if old_direction != new_direction:
        return _initial_tracker(
            quantity=desired_quantity,
            price=execution_price,
            opened_on=opened_on,
            trading_cost_usd=trading_cost_usd,
        )
    old_size = abs(old_quantity)
    new_size = abs(desired_quantity)
    average_entry = float(previous["averageEntryPrice"])
    realized = float(previous["realizedPnlUsd"])
    if new_size > old_size:
        added = new_size - old_size
        average_entry = (
            old_size * average_entry + added * execution_price
        ) / new_size
    else:
        closed = old_size - new_size
        realized += closed * (execution_price - average_entry) * old_direction
    return {
        **previous,
        "averageEntryPrice": average_entry,
        "quantity": desired_quantity,
        "realizedPnlUsd": realized,
        "tradingCostsUsd": float(previous["tradingCostsUsd"]) + trading_cost_usd,
    }


def _solve_rebalance(
    *,
    nav_before_cost: float,
    target: list[float],
    prices: list[float],
    current_quantities: list[float],
) -> tuple[list[float], float, float]:
    """Solve the fee/target fixed point at one closing mark."""
    nav_after_cost = nav_before_cost
    desired = list(current_quantities)
    trade_cost_usd = 0.0
    for _iteration in range(12):
        desired = [
            nav_after_cost * target[index] / prices[index]
            for index in range(len(target))
        ]
        traded_notional = sum(
            abs(desired[index] - current_quantities[index]) * prices[index]
            for index in range(len(target))
        )
        trade_cost_usd = PAPER_EXECUTION_COST * traded_notional
        updated_nav = nav_before_cost - trade_cost_usd
        if updated_nav <= 0 or not math.isfinite(updated_nav):
            raise ValueError("Paper execution costs exhausted the account")
        if abs(updated_nav - nav_after_cost) <= max(1e-9, nav_before_cost * 1e-12):
            nav_after_cost = updated_nav
            break
        nav_after_cost = updated_nav
    desired = [
        nav_after_cost * target[index] / prices[index]
        for index in range(len(target))
    ]
    traded_notional = sum(
        abs(desired[index] - current_quantities[index]) * prices[index]
        for index in range(len(target))
    )
    trade_cost_usd = PAPER_EXECUTION_COST * traded_notional
    nav_after_cost = nav_before_cost - trade_cost_usd
    return desired, trade_cost_usd, nav_after_cost


def paper_continuation(
    engine: dict[str, Any],
    histories: list[dict[str, Any]],
    *,
    reset_date: str,
    initial_nav_usd: float,
) -> dict[str, Any]:
    indexes = [
        index for index, date_text in enumerate(engine["dates"]) if date_text <= reset_date
    ]
    if not indexes:
        raise ValueError("The OKX market window does not include the paper reset date")
    reset_index = indexes[-1]
    asset_count = len(engine["assets"])
    history_by_asset = {history["asset"]: history for history in histories}
    daily: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []
    trackers: dict[str, dict[str, Any]] = {}
    funding_actual_intervals = 0
    funding_fallback_intervals = 0

    initial_date = engine["dates"][reset_index]
    target = list(engine["target"][reset_index])
    initial_prices = list(engine["closes"][reset_index])
    quantities, initial_cost_usd, nav = _solve_rebalance(
        nav_before_cost=initial_nav_usd,
        target=target,
        prices=initial_prices,
        current_quantities=_zero_row(asset_count),
    )
    signal_date = engine["dates"][max(0, reset_index - 1)]
    for asset_index, quantity in enumerate(quantities):
        if abs(quantity) <= EPSILON:
            continue
        asset = engine["assets"][asset_index]
        price = initial_prices[asset_index]
        traded_notional = abs(quantity) * price
        cost_usd = traded_notional * PAPER_EXECUTION_COST
        trackers[asset] = _initial_tracker(
            quantity=quantity,
            price=price,
            opened_on=initial_date,
            trading_cost_usd=cost_usd,
        )
        executions.append(
            {
                "asset": asset,
                "instrumentId": INSTRUMENTS[asset],
                "costToNav": cost_usd / initial_nav_usd,
                "deltaQuantity": quantity,
                "deltaWeight": quantity * price / nav,
                "id": f"paper-{initial_date.replace('-', '')}-{asset}-0",
                "newWeight": quantity * price / nav,
                "oldWeight": 0.0,
                "orderDate": initial_date,
                "price": price,
                "side": "BUY" if quantity > 0 else "SELL",
                "signalDate": signal_date,
            }
        )
    daily.append(
        {
            "date": initial_date,
            "fundingPnlUsd": 0.0,
            "fundingReturn": 0.0,
            "grossExposure": _gross(target),
            "navUsd": nav,
            "pricePnlUsd": 0.0,
            "priceReturn": 0.0,
            "return": nav / initial_nav_usd - 1.0,
            "tradeCost": initial_cost_usd / initial_nav_usd,
            "tradeCostUsd": initial_cost_usd,
        }
    )

    for date_index in range(reset_index + 1, len(engine["dates"])):
        current_date = engine["dates"][date_index]
        nav_before = nav
        previous_prices = engine["closes"][date_index - 1]
        current_prices = engine["closes"][date_index]
        price_pnl_usd = sum(
            quantities[asset] * (current_prices[asset] - previous_prices[asset])
            for asset in range(asset_count)
        )
        funding_pnl_usd = 0.0
        for asset_index, quantity in enumerate(quantities):
            if abs(quantity) <= EPSILON:
                continue
            asset = engine["assets"][asset_index]
            rates, actual = _funding_rates_for_candle(
                history_by_asset[asset], current_date
            )
            previous_notional = quantity * previous_prices[asset_index]
            if actual:
                asset_funding_pnl = -previous_notional * sum(rates)
                funding_actual_intervals += 1
            else:
                asset_funding_pnl = (
                    -abs(previous_notional)
                    * MISSING_FUNDING_FALLBACK_ANNUAL
                    / ANNUAL_DAYS
                )
                funding_fallback_intervals += 1
            funding_pnl_usd += asset_funding_pnl
            tracker = trackers.get(asset)
            if tracker is not None:
                tracker["fundingPnlUsd"] = (
                    float(tracker["fundingPnlUsd"]) + asset_funding_pnl
                )

        nav_before_cost = nav_before + price_pnl_usd + funding_pnl_usd
        if nav_before_cost <= 0 or not math.isfinite(nav_before_cost):
            raise ValueError("DS-40/180 forward paper account exhausted its capital")
        target = list(engine["target"][date_index])
        desired_quantities, trade_cost_usd, nav = _solve_rebalance(
            nav_before_cost=nav_before_cost,
            target=target,
            prices=current_prices,
            current_quantities=quantities,
        )
        signal_date = engine["dates"][date_index - 1]
        for asset_index, desired_quantity in enumerate(desired_quantities):
            old_quantity = quantities[asset_index]
            delta_quantity = desired_quantity - old_quantity
            if abs(delta_quantity) <= EPSILON:
                continue
            asset = engine["assets"][asset_index]
            price = current_prices[asset_index]
            traded_notional = abs(delta_quantity) * price
            cost_usd = traded_notional * PAPER_EXECUTION_COST
            old_weight = old_quantity * price / nav_before_cost
            new_weight = desired_quantity * price / nav
            next_tracker = _rebalance_tracker(
                desired_quantity=desired_quantity,
                execution_price=price,
                opened_on=current_date,
                previous=trackers.get(asset),
                trading_cost_usd=cost_usd,
            )
            if next_tracker is None:
                trackers.pop(asset, None)
            else:
                trackers[asset] = next_tracker
            executions.append(
                {
                    "asset": asset,
                    "instrumentId": INSTRUMENTS[asset],
                    "costToNav": cost_usd / nav_before_cost,
                    "deltaQuantity": delta_quantity,
                    "deltaWeight": new_weight - old_weight,
                    "id": f"paper-{current_date.replace('-', '')}-{asset}-{len(executions)}",
                    "newWeight": new_weight,
                    "oldWeight": old_weight,
                    "orderDate": current_date,
                    "price": price,
                    "side": "BUY" if delta_quantity > 0 else "SELL",
                    "signalDate": signal_date,
                }
            )
        quantities = desired_quantities
        net_return = nav / nav_before - 1.0
        daily.append(
            {
                "date": current_date,
                "fundingPnlUsd": funding_pnl_usd,
                "fundingReturn": funding_pnl_usd / nav_before,
                "grossExposure": _gross(target),
                "navUsd": nav,
                "pricePnlUsd": price_pnl_usd,
                "priceReturn": price_pnl_usd / nav_before,
                "return": net_return,
                "tradeCost": trade_cost_usd / nav_before,
                "tradeCostUsd": trade_cost_usd,
            }
        )

    return {
        "daily": daily,
        "executions": executions,
        "fundingActualIntervals": funding_actual_intervals,
        "fundingFallbackIntervals": funding_fallback_intervals,
        "nav": nav,
        "positionTrackers": trackers,
        "quantities": quantities,
        "target": target,
    }


def _mark_portfolio_to_live(
    engine: dict[str, Any],
    histories: list[dict[str, Any]],
    continuation: dict[str, Any],
) -> dict[str, Any]:
    latest_index = len(engine["dates"]) - 1
    history_by_asset = {history["asset"]: history for history in histories}
    nav_usd = float(continuation["nav"])
    positions: list[dict[str, Any]] = []
    for asset, tracker in continuation["positionTrackers"].items():
        asset_index = engine["assets"].index(asset)
        closed_price = engine["closes"][latest_index][asset_index]
        live_price = float(history_by_asset[asset].get("liveMark") or closed_price)
        quantity = float(tracker["quantity"])
        live_increment = quantity * (live_price - closed_price)
        nav_usd += live_increment
        signed_notional = quantity * live_price
        direction = math.copysign(1.0, quantity)
        unrealized = quantity * (live_price - float(tracker["averageEntryPrice"]))
        net_pnl = (
            float(tracker["realizedPnlUsd"])
            + unrealized
            + float(tracker["fundingPnlUsd"])
            - float(tracker["tradingCostsUsd"])
        )
        positions.append(
            {
                "asset": asset,
                "instrumentId": INSTRUMENTS[asset],
                **tracker,
                "direction": "long" if quantity > 0 else "short",
                "markPrice": live_price,
                "netPnlUsd": net_pnl,
                "notionalUsd": abs(signed_notional),
                "signedNotionalUsd": signed_notional,
                "unrealizedPnlPercent": (
                    live_price / float(tracker["averageEntryPrice"]) - 1.0
                )
                * direction,
                "unrealizedPnlUsd": unrealized,
            }
        )
    if nav_usd <= 0 or not math.isfinite(nav_usd):
        raise ValueError("Live mark exhausted the paper account")
    for position in positions:
        position["weight"] = position["signedNotionalUsd"] / nav_usd
    positions.sort(key=lambda item: -abs(float(item["weight"])))
    return {
        "navUsd": nav_usd,
        "netExposure": sum(float(item["weight"]) for item in positions),
        "grossExposure": sum(abs(float(item["weight"])) for item in positions),
        "positions": positions,
    }
