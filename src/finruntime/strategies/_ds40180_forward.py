"""Persistent append-only forward paper state for DS-40/180 T50-C3."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ._ds40180_account import (
    _funding_rates_between,
    _rebalance_tracker,
    _solve_rebalance,
    paper_continuation,
)
from ._ds40180_common import (
    EPSILON,
    INSTRUMENTS,
    PAPER_EXECUTION_COST,
    STRATEGY_ID,
    STRATEGY_VERSION,
    _candle_close_ms,
    _gross,
    _utc_now,
)
from ._ds40180_v2 import (
    build_forward_plan,
    current_weights_from_quantities,
    execution_prices,
)

STATE_SCHEMA_VERSION = 2
MAX_DAILY_POINTS = 1000
MAX_EXECUTIONS = 5000


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical(value)).hexdigest()}"


def _write_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                value,
                handle,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _bar_digest(
    histories: list[dict[str, Any]], assets: list[str], date_text: str
) -> str:
    history_by_asset = {history["asset"]: history for history in histories}
    payload = []
    for asset in assets:
        bar = history_by_asset[asset]["bars"][date_text]
        payload.append(
            [
                asset,
                bar["openTime"],
                bar["open"],
                bar["high"],
                bar["low"],
                bar["close"],
                bar["quoteVolume"],
            ]
        )
    return _sha256(payload)


def _append_journal_event(
    journal_path: Path,
    state: dict[str, Any],
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    sequence = int(state.get("journalSequence") or 0) + 1
    previous_hash = str(state.get("journalHash") or "sha256:" + "0" * 64)
    event = {
        "schema_version": 1,
        "strategyId": STRATEGY_ID,
        "strategyVersion": STRATEGY_VERSION,
        "sequence": sequence,
        "eventType": event_type,
        "occurredAt": _utc_now(),
        "previousHash": previous_hash,
        "payload": payload,
    }
    event["eventHash"] = _sha256(event)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    with journal_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    state["journalSequence"] = sequence
    state["journalHash"] = event["eventHash"]
    return event


def verify_journal(path: Path) -> dict[str, Any]:
    previous_hash = "sha256:" + "0" * 64
    sequence = 0
    if not path.is_file():
        return {"valid": True, "events": 0, "lastHash": previous_hash}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            event = json.loads(raw)
            sequence += 1
            if event.get("sequence") != sequence:
                raise ValueError(f"journal sequence mismatch at line {line_number}")
            if event.get("previousHash") != previous_hash:
                raise ValueError(f"journal hash chain mismatch at line {line_number}")
            expected = dict(event)
            actual_hash = expected.pop("eventHash", None)
            calculated = _sha256(expected)
            if actual_hash != calculated:
                raise ValueError(f"journal event hash mismatch at line {line_number}")
            previous_hash = str(actual_hash)
    return {"valid": True, "events": sequence, "lastHash": previous_hash}


def _state_paths(snapshot_path: Path) -> tuple[Path, Path]:
    return (
        snapshot_path.with_name("ds40180_t50c3_paper_state.json"),
        snapshot_path.with_name("ds40180_t50c3_paper_events.jsonl"),
    )


def _history_map(histories: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {history["asset"]: history for history in histories}


def _funding_pnl_between(
    *,
    assets: list[str],
    quantities: list[float],
    reference_prices: list[float],
    histories: list[dict[str, Any]],
    start_ms_by_asset: dict[str, int],
    end_ms: int,
    trackers: dict[str, dict[str, Any]],
) -> tuple[float, int]:
    history_by_asset = _history_map(histories)
    total = 0.0
    events = 0
    for index, asset in enumerate(assets):
        quantity = float(quantities[index])
        if abs(quantity) <= EPSILON:
            continue
        rates = _funding_rates_between(
            history_by_asset[asset], int(start_ms_by_asset.get(asset) or 0), end_ms
        )
        if not rates:
            continue
        pnl = -quantity * float(reference_prices[index]) * sum(rates)
        total += pnl
        events += len(rates)
        tracker = trackers.get(asset)
        if tracker is not None:
            tracker["fundingPnlUsd"] = float(tracker.get("fundingPnlUsd") or 0.0) + pnl
    return total, events


def _rebalance_state(
    *,
    state: dict[str, Any],
    assets: list[str],
    desired_target: list[float],
    execution_price_values: list[float],
    signal_date: str,
    effective_date: str,
    plan: dict[str, Any],
) -> tuple[float, list[float], list[dict[str, Any]]]:
    nav_before_cost = float(state["navAtReferenceUsd"])
    current_quantities = [float(value) for value in state["quantities"]]
    desired_quantities, trade_cost_usd, nav_after = _solve_rebalance(
        nav_before_cost=nav_before_cost,
        target=desired_target,
        prices=execution_price_values,
        current_quantities=current_quantities,
    )
    trackers = state["positionTrackers"]
    executions: list[dict[str, Any]] = []
    for index, asset in enumerate(assets):
        old_quantity = current_quantities[index]
        desired_quantity = desired_quantities[index]
        delta_quantity = desired_quantity - old_quantity
        if abs(delta_quantity) <= EPSILON:
            continue
        price = execution_price_values[index]
        cost_usd = abs(delta_quantity) * price * PAPER_EXECUTION_COST
        old_weight = old_quantity * price / nav_before_cost
        new_weight = desired_quantity * price / nav_after
        next_tracker = _rebalance_tracker(
            desired_quantity=desired_quantity,
            execution_price=price,
            opened_on=signal_date,
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
                "effectiveDate": effective_date,
                "id": f"forward-{signal_date.replace('-', '')}-{asset}-{len(state['executions']) + len(executions)}",
                "newWeight": new_weight,
                "oldWeight": old_weight,
                "orderDate": signal_date,
                "price": price,
                "side": "BUY" if delta_quantity > 0 else "SELL",
                "signalDate": signal_date,
            }
        )
    state["navAtReferenceUsd"] = nav_after
    state["quantities"] = desired_quantities
    state["positionTrackers"] = trackers
    state["targetWeights"] = desired_target
    state["targetEffectiveDate"] = effective_date
    state["lastPrices"] = execution_price_values
    state["executions"].extend(executions)
    state["executions"] = state["executions"][-MAX_EXECUTIONS:]
    state["totalExecutions"] = int(state.get("totalExecutions") or 0) + len(executions)
    state["lastPlan"] = plan
    return trade_cost_usd, desired_quantities, executions


def _bootstrap_state(
    engine: dict[str, Any],
    histories: list[dict[str, Any]],
    *,
    reset_date: str,
    initial_nav_usd: float,
) -> dict[str, Any]:
    continuation = paper_continuation(
        engine,
        histories,
        reset_date=reset_date,
        initial_nav_usd=initial_nav_usd,
    )
    assets = list(engine["assets"])
    latest_index = int(engine["latestMarketIndex"])
    decision_index = int(engine["executionIndex"])
    latest_date = engine["marketDates"][latest_index]
    latest_close_ms = _candle_close_ms(latest_date)
    quantities = [float(value) for value in continuation["quantities"]]
    last_prices = [float(value) for value in engine["closes"][latest_index]]
    trackers = continuation["positionTrackers"]
    history_by_asset = _history_map(histories)
    mark_end = max(
        int(history_by_asset[asset].get("markTimeMs") or latest_close_ms)
        for asset in assets
    )
    funding_cursor = {asset: latest_close_ms for asset in assets}
    live_funding, live_events = _funding_pnl_between(
        assets=assets,
        quantities=quantities,
        reference_prices=last_prices,
        histories=histories,
        start_ms_by_asset=funding_cursor,
        end_ms=mark_end,
        trackers=trackers,
    )
    live_price_pnl = sum(
        quantities[index]
        * (float(history_by_asset[asset].get("liveMark") or last_prices[index]) - last_prices[index])
        for index, asset in enumerate(assets)
    )
    nav_at_reference = float(continuation["nav"]) + live_funding + live_price_pnl
    if nav_at_reference <= 0 or not math.isfinite(nav_at_reference):
        raise ValueError("bootstrap live mark exhausted the paper account")
    live_reference_prices = [
        float(history_by_asset[asset].get("liveMark") or last_prices[index])
        for index, asset in enumerate(assets)
    ]
    current_weights = current_weights_from_quantities(
        quantities, live_reference_prices, nav_at_reference
    )
    plan = build_forward_plan(
        engine,
        histories,
        decision_index=decision_index,
        current_weights=current_weights,
        use_live_overlays=True,
    )
    selected_prices, quote_diagnostics = execution_prices(
        assets,
        histories,
        current_weights,
        plan["executedTarget"],
        live_reference_prices,
    )
    state: dict[str, Any] = {
        "schema_version": STATE_SCHEMA_VERSION,
        "strategyId": STRATEGY_ID,
        "strategyVersion": STRATEGY_VERSION,
        "createdAt": _utc_now(),
        "updatedAt": _utc_now(),
        "bootstrapMode": "historical_replay_once_then_frozen",
        "initialNavUsd": initial_nav_usd,
        "requestedResetDate": reset_date,
        "actualResetDate": continuation["actualResetDate"],
        "assets": assets,
        "lastProcessedMarketDate": latest_date,
        "lastProcessedBarHash": _bar_digest(histories, assets, latest_date),
        "lastRevisionHash": None,
        "navAtReferenceUsd": nav_at_reference,
        "peakNavUsd": max(
            initial_nav_usd,
            nav_at_reference,
            *(float(point["navUsd"]) for point in continuation["daily"]),
        ),
        "quantities": quantities,
        "positionTrackers": trackers,
        "lastPrices": live_reference_prices,
        "lastFundingCursorMs": {asset: mark_end for asset in assets},
        "targetWeights": list(continuation["target"]),
        "targetEffectiveDate": continuation["targetEffectiveDate"],
        "daily": list(continuation["daily"])[-MAX_DAILY_POINTS:],
        "executions": list(continuation["executions"])[-MAX_EXECUTIONS:],
        "totalExecutions": len(continuation["executions"]),
        "fundingActualIntervals": int(continuation["fundingActualIntervals"]) + live_events,
        "fundingFallbackIntervals": int(continuation["fundingFallbackIntervals"]),
        "journalSequence": 0,
        "journalHash": "sha256:" + "0" * 64,
        "lastPlan": plan,
        "quoteDiagnostics": quote_diagnostics,
        "warnings": [],
    }
    trade_cost_usd, _desired_quantities, executions = _rebalance_state(
        state=state,
        assets=assets,
        desired_target=list(plan["executedTarget"]),
        execution_price_values=selected_prices,
        signal_date=latest_date,
        effective_date=engine["executionDate"],
        plan=plan,
    )
    state["quoteDiagnostics"] = quote_diagnostics
    state["lastFundingCursorMs"] = {asset: mark_end for asset in assets}
    state["peakNavUsd"] = max(float(state["peakNavUsd"]), float(state["navAtReferenceUsd"]))
    if state["daily"]:
        state["daily"][-1] = {
            **state["daily"][-1],
            "navUsd": state["navAtReferenceUsd"],
            "bootstrapV2TradeCostUsd": trade_cost_usd,
            "bootstrapV2Executions": len(executions),
        }
    return state


def _load_state(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValueError("unsupported DS-40/180 persistent state")
    if value.get("strategyId") != STRATEGY_ID:
        raise ValueError("unexpected DS-40/180 persistent identity")
    return value


def _advance_state(
    state: dict[str, Any],
    engine: dict[str, Any],
    histories: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    assets = list(engine["assets"])
    if list(state.get("assets") or []) != assets:
        raise ValueError("DS-40/180 active asset set changed; manual migration is required")
    market_dates = list(engine["marketDates"])
    last_processed = str(state["lastProcessedMarketDate"])
    if last_processed not in market_dates:
        raise ValueError("last processed market date is outside the current history window")
    events: list[dict[str, Any]] = []
    start_index = market_dates.index(last_processed) + 1
    latest_index = int(engine["latestMarketIndex"])
    execution_index = int(engine["executionIndex"])
    history_by_asset = _history_map(histories)
    for date_index in range(start_index, latest_index + 1):
        current_date = market_dates[date_index]
        close_ms = _candle_close_ms(current_date)
        quantities = [float(value) for value in state["quantities"]]
        previous_prices = [float(value) for value in state["lastPrices"]]
        current_prices = [float(value) for value in engine["closes"][date_index]]
        nav_before = float(state["navAtReferenceUsd"])
        price_pnl_usd = sum(
            quantities[index] * (current_prices[index] - previous_prices[index])
            for index in range(len(assets))
        )
        funding_pnl_usd, funding_events = _funding_pnl_between(
            assets=assets,
            quantities=quantities,
            reference_prices=previous_prices,
            histories=histories,
            start_ms_by_asset={
                asset: int(state["lastFundingCursorMs"].get(asset) or 0)
                for asset in assets
            },
            end_ms=close_ms,
            trackers=state["positionTrackers"],
        )
        state["fundingActualIntervals"] = int(state.get("fundingActualIntervals") or 0) + funding_events
        nav_before_cost = nav_before + price_pnl_usd + funding_pnl_usd
        if nav_before_cost <= 0 or not math.isfinite(nav_before_cost):
            raise ValueError("DS-40/180 forward paper account exhausted its capital")
        state["navAtReferenceUsd"] = nav_before_cost
        current_weights = current_weights_from_quantities(
            quantities, current_prices, nav_before_cost
        )
        decision_index = min(date_index + 1, execution_index)
        latest_decision = decision_index == execution_index
        plan = build_forward_plan(
            engine,
            histories,
            decision_index=decision_index,
            current_weights=current_weights,
            use_live_overlays=latest_decision,
        )
        if latest_decision:
            selected_prices, quote_diagnostics = execution_prices(
                assets,
                histories,
                current_weights,
                plan["executedTarget"],
                current_prices,
            )
        else:
            selected_prices = current_prices
            quote_diagnostics = {
                asset: {"side": "historical_catchup", "selected": current_prices[index]}
                for index, asset in enumerate(assets)
            }
        trade_cost_usd, _new_quantities, executions = _rebalance_state(
            state=state,
            assets=assets,
            desired_target=list(plan["executedTarget"]),
            execution_price_values=selected_prices,
            signal_date=current_date,
            effective_date=engine["dates"][decision_index],
            plan=plan,
        )
        state["quoteDiagnostics"] = quote_diagnostics
        state["lastProcessedMarketDate"] = current_date
        state["lastProcessedBarHash"] = _bar_digest(histories, assets, current_date)
        state["lastFundingCursorMs"] = {
            asset: int(quote_diagnostics.get(asset, {}).get("observedAtMs") or close_ms)
            if latest_decision
            else close_ms
            for asset in assets
        }
        state["lastPrices"] = selected_prices
        state["peakNavUsd"] = max(
            float(state["peakNavUsd"]), float(state["navAtReferenceUsd"])
        )
        daily_return = float(state["navAtReferenceUsd"]) / nav_before - 1.0
        point = {
            "date": current_date,
            "fundingPnlUsd": funding_pnl_usd,
            "fundingReturn": funding_pnl_usd / nav_before,
            "grossExposure": _gross(plan["executedTarget"]),
            "navUsd": state["navAtReferenceUsd"],
            "pricePnlUsd": price_pnl_usd,
            "priceReturn": price_pnl_usd / nav_before,
            "return": daily_return,
            "targetEffectiveDate": engine["dates"][decision_index],
            "tradeCost": trade_cost_usd / nav_before,
            "tradeCostUsd": trade_cost_usd,
        }
        state["daily"].append(point)
        state["daily"] = state["daily"][-MAX_DAILY_POINTS:]
        events.append(
            {
                "date": current_date,
                "navUsd": state["navAtReferenceUsd"],
                "pricePnlUsd": price_pnl_usd,
                "fundingPnlUsd": funding_pnl_usd,
                "tradeCostUsd": trade_cost_usd,
                "executions": executions,
                "plan": {
                    "dynamicGrossCap": plan["dynamicGrossCap"],
                    "grossCapRegime": plan["grossCapRegime"],
                    "crisis": plan["crisis"],
                    "funding": plan["funding"],
                    "covariance": plan["covariance"],
                    "noTrade": plan["noTrade"],
                },
            }
        )
    return events


def _mark_live(
    state: dict[str, Any],
    histories: list[dict[str, Any]],
) -> dict[str, Any]:
    assets = list(state["assets"])
    history_by_asset = _history_map(histories)
    quantities = [float(value) for value in state["quantities"]]
    reference_prices = [float(value) for value in state["lastPrices"]]
    nav_usd = float(state["navAtReferenceUsd"])
    mark_time = max(
        int(history_by_asset[asset].get("markTimeMs") or 0) for asset in assets
    )
    funding_pnl_usd, funding_events = _funding_pnl_between(
        assets=assets,
        quantities=quantities,
        reference_prices=reference_prices,
        histories=histories,
        start_ms_by_asset={
            asset: int(state["lastFundingCursorMs"].get(asset) or 0) for asset in assets
        },
        end_ms=mark_time,
        trackers={asset: dict(value) for asset, value in state["positionTrackers"].items()},
    )
    positions: list[dict[str, Any]] = []
    price_pnl = 0.0
    for index, asset in enumerate(assets):
        quantity = quantities[index]
        if abs(quantity) <= EPSILON:
            continue
        live_price = float(history_by_asset[asset].get("liveMark") or reference_prices[index])
        increment = quantity * (live_price - reference_prices[index])
        price_pnl += increment
        tracker = state["positionTrackers"].get(asset) or {}
        signed_notional = quantity * live_price
        direction = 1.0 if quantity > 0 else -1.0
        unrealized = quantity * (live_price - float(tracker.get("averageEntryPrice") or live_price))
        positions.append(
            {
                "asset": asset,
                "instrumentId": INSTRUMENTS[asset],
                **tracker,
                "direction": "long" if quantity > 0 else "short",
                "markPrice": live_price,
                "notionalUsd": abs(signed_notional),
                "signedNotionalUsd": signed_notional,
                "unrealizedPnlUsd": unrealized,
                "unrealizedPnlPercent": (
                    live_price / float(tracker.get("averageEntryPrice") or live_price) - 1.0
                )
                * direction,
            }
        )
    nav_usd += price_pnl + funding_pnl_usd
    if nav_usd <= 0 or not math.isfinite(nav_usd):
        raise ValueError("live mark exhausted DS-40/180 paper capital")
    for position in positions:
        position["weight"] = float(position["signedNotionalUsd"]) / nav_usd
    positions.sort(key=lambda item: -abs(float(item["weight"])))
    return {
        "navUsd": nav_usd,
        "pricePnlUsd": price_pnl,
        "fundingPnlUsd": funding_pnl_usd,
        "fundingEvents": funding_events,
        "grossExposure": sum(abs(float(item["weight"])) for item in positions),
        "netExposure": sum(float(item["weight"]) for item in positions),
        "positions": positions,
        "markTimeMs": mark_time,
    }


def load_or_advance_forward_state(
    *,
    snapshot_path: Path,
    engine: dict[str, Any],
    histories: list[dict[str, Any]],
    reset_date: str,
    initial_nav_usd: float,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    state_path, journal_path = _state_paths(snapshot_path)
    journal_status = verify_journal(journal_path)
    if state_path.is_file():
        state = _load_state(state_path)
    else:
        state = _bootstrap_state(
            engine,
            histories,
            reset_date=reset_date,
            initial_nav_usd=initial_nav_usd,
        )
        _write_atomic(state_path, state)
        _append_journal_event(
            journal_path,
            state,
            "bootstrap",
            {
                "lastProcessedMarketDate": state["lastProcessedMarketDate"],
                "navUsd": state["navAtReferenceUsd"],
                "targetEffectiveDate": state["targetEffectiveDate"],
                "targetGross": _gross(state["targetWeights"]),
                "bootstrapMode": state["bootstrapMode"],
            },
        )
        _write_atomic(state_path, state)
        journal_status = verify_journal(journal_path)

    warnings = list(state.get("warnings") or [])
    last_date = str(state["lastProcessedMarketDate"])
    current_digest = _bar_digest(histories, list(state["assets"]), last_date)
    if current_digest != state.get("lastProcessedBarHash"):
        revision_key = _sha256([last_date, current_digest])
        if revision_key != state.get("lastRevisionHash"):
            state["lastRevisionHash"] = revision_key
            warnings.append(
                f"OKX revised already processed {last_date} candle; paper ledger was not rewritten"
            )
            _append_journal_event(
                journal_path,
                state,
                "data_revision_detected",
                {"date": last_date, "newBarHash": current_digest},
            )
    events = _advance_state(state, engine, histories)
    for payload in events:
        _append_journal_event(journal_path, state, "daily_close_processed", payload)
    state["warnings"] = warnings[-100:]
    state["updatedAt"] = _utc_now()
    _write_atomic(state_path, state)
    live = _mark_live(state, histories)
    return state, live, {
        "statePath": str(state_path),
        "journalPath": str(journal_path),
        "journal": verify_journal(journal_path),
        "initialJournal": journal_status,
        "newEvents": len(events),
    }
