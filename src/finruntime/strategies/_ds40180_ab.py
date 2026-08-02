"""Forward A/B observability for the frozen DS-40/180 v1 reference and v2.

The comparison is deliberately read-only: it cannot submit orders and it never
changes the v2 paper state. The v1 arm is recomputed from the pinned legacy
policy on the same public OKX histories, while the v2 arm is read from the
persistent forward snapshot. Only one paired observation per closed market day
is appended to the A/B hash-chain journal.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from ._ds40180_account import _mark_portfolio_to_live, paper_continuation
from ._ds40180_common import (
    ANNUAL_DAYS,
    EPSILON,
    MATERIAL_DELTA,
    STRATEGY_ID,
    STRATEGY_VERSION,
    _gross,
    _population_standard_deviation,
    _utc_now,
)
from ._ds40180_v1_reference import (
    V1_PAPER_ASSET_CAP,
    V1_PAPER_GROSS_CAP,
    V1_REFERENCE_PROFILE,
    V1_REFERENCE_SOURCE_COMMIT,
    V1_REFERENCE_STRATEGY_ID,
    V1_REFERENCE_VERSION,
    build_v1_reference_engine,
)

AB_SCHEMA_VERSION = 1
AB_JOURNAL_SCHEMA_VERSION = 1
AB_STUDY_ID = "ds40180_v1_v2_forward_ab"
AB_MINIMUM_REVIEW_DAYS = 30
AB_INTERMEDIATE_REVIEW_DAYS = 60
AB_PREFERRED_REVIEW_DAYS = 90


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


def _read_journal(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"A/B journal contains invalid JSON at line {line_number}"
                ) from error
            if not isinstance(event, dict):
                raise ValueError(f"A/B journal event {line_number} is not an object")
            events.append(event)
    return events


def verify_ab_journal(path: Path) -> dict[str, Any]:
    previous_hash = "sha256:" + "0" * 64
    first_observed_at: str | None = None
    last_observed_at: str | None = None
    last_as_of: str | None = None
    events = _read_journal(path)
    for sequence, event in enumerate(events, start=1):
        if event.get("schema_version") != AB_JOURNAL_SCHEMA_VERSION:
            raise ValueError(f"unsupported A/B journal schema at event {sequence}")
        if event.get("studyId") != AB_STUDY_ID:
            raise ValueError(f"unexpected A/B study identity at event {sequence}")
        if event.get("sequence") != sequence:
            raise ValueError(f"A/B journal sequence mismatch at event {sequence}")
        if event.get("previousHash") != previous_hash:
            raise ValueError(f"A/B journal hash-chain mismatch at event {sequence}")
        expected = dict(event)
        actual_hash = expected.pop("eventHash", None)
        calculated = _sha256(expected)
        if actual_hash != calculated:
            raise ValueError(f"A/B journal event hash mismatch at event {sequence}")
        observed_at = event.get("observedAt")
        as_of = event.get("asOf")
        if not isinstance(observed_at, str) or not isinstance(as_of, str):
            raise ValueError(f"A/B journal timestamps are invalid at event {sequence}")
        if last_as_of is not None and as_of <= last_as_of:
            raise ValueError("A/B journal market dates must be strictly increasing")
        first_observed_at = first_observed_at or observed_at
        last_observed_at = observed_at
        last_as_of = as_of
        previous_hash = str(actual_hash)
    return {
        "valid": True,
        "events": len(events),
        "lastHash": previous_hash,
        "firstObservedAt": first_observed_at,
        "lastObservedAt": last_observed_at,
        "lastAsOf": last_as_of,
    }


def _material_executions(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if abs(float(item.get("deltaWeight") or 0.0)) >= MATERIAL_DELTA
        or abs(float(item.get("oldWeight") or 0.0)) <= EPSILON
        or abs(float(item.get("newWeight") or 0.0)) <= EPSILON
    ]


def build_v1_reference_arm(
    histories: list[dict[str, Any]],
    failed_assets: list[dict[str, str]],
    *,
    reset_date: str,
    initial_nav_usd: float,
) -> dict[str, Any]:
    engine = build_v1_reference_engine(histories, failed_assets)
    continuation = paper_continuation(
        engine,
        histories,
        reset_date=reset_date,
        initial_nav_usd=initial_nav_usd,
    )
    live = _mark_portfolio_to_live(engine, histories, continuation)
    target = [float(value) for value in continuation["target"]]
    executions = _material_executions(continuation["executions"])
    latest_index = int(engine["latestMarketIndex"])
    return {
        "strategyId": V1_REFERENCE_STRATEGY_ID,
        "strategyVersion": V1_REFERENCE_VERSION,
        "profile": V1_REFERENCE_PROFILE,
        "sourceCommit": V1_REFERENCE_SOURCE_COMMIT,
        "identityKind": "frozen_counterfactual_reference",
        "asOf": engine["marketDates"][latest_index],
        "effectiveDate": engine["executionDate"],
        "actualResetDate": continuation["actualResetDate"],
        "initialNavUsd": initial_nav_usd,
        "navUsd": float(live["navUsd"]),
        "daily": continuation["daily"],
        "executions": executions,
        "totalExecutions": len(continuation["executions"]),
        "targetGross": _gross(target),
        "targetNet": sum(target),
        "paperGrossCap": V1_PAPER_GROSS_CAP,
        "paperAssetCap": V1_PAPER_ASSET_CAP,
        "liveFundingPnlUsd": float(live["liveFundingPnlUsd"]),
        "fundingActualIntervals": int(continuation["fundingActualIntervals"]),
        "fundingFallbackIntervals": int(continuation["fundingFallbackIntervals"]),
        "exchange_submission_available": False,
        "live_ready": False,
        "real_leverage_authorized": False,
    }


def build_v2_forward_arm(snapshot: dict[str, Any]) -> dict[str, Any]:
    if snapshot.get("schema_version") != 2:
        raise ValueError("A/B requires a DS-40/180 v2 snapshot schema")
    if snapshot.get("strategyId") != STRATEGY_ID:
        raise ValueError("A/B received an unexpected v2 strategy identity")
    if snapshot.get("strategyVersion") != STRATEGY_VERSION:
        raise ValueError("A/B received an unexpected v2 strategy version")
    paper = snapshot.get("paper")
    if not isinstance(paper, dict):
        raise ValueError("A/B v2 paper payload is unavailable")
    account = paper.get("account")
    if not isinstance(account, dict):
        raise ValueError("A/B v2 account payload is unavailable")
    executions = paper.get("executions")
    daily = paper.get("daily")
    if not isinstance(executions, list) or not isinstance(daily, list):
        raise ValueError("A/B v2 execution or daily history is unavailable")
    funding = snapshot.get("funding")
    funding = funding if isinstance(funding, dict) else {}
    return {
        "strategyId": snapshot["strategyId"],
        "strategyVersion": snapshot["strategyVersion"],
        "profile": snapshot.get("profile"),
        "identityKind": snapshot.get("identityKind"),
        "asOf": snapshot.get("asOf"),
        "effectiveDate": snapshot.get("effectiveDate"),
        "actualResetDate": account.get("actualResetDate"),
        "initialNavUsd": float(account.get("initialNavUsd") or 0.0),
        "navUsd": float(paper.get("navUsd") or 0.0),
        "daily": daily,
        "executions": executions,
        "totalExecutions": int(paper.get("totalExecutions") or 0),
        "targetGross": float(snapshot.get("targetGross") or 0.0),
        "targetNet": float(snapshot.get("targetNet") or 0.0),
        "paperGrossCap": float(snapshot.get("paperGrossCap") or 0.0),
        "paperAssetCap": float(snapshot.get("paperAssetCap") or 0.0),
        "dynamicGrossCap": snapshot.get("dynamicGrossCap"),
        "liveFundingPnlUsd": float(paper.get("liveFundingPnlUsd") or 0.0),
        "fundingActualIntervals": int(funding.get("actualIntervals") or 0),
        "fundingFallbackIntervals": int(funding.get("fallbackIntervals") or 0),
        "persistent": True,
        "exchange_submission_available": False,
        "live_ready": False,
        "real_leverage_authorized": False,
    }


def _daily_map(arm: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for item in arm.get("daily") or []:
        if not isinstance(item, dict):
            continue
        date_text = item.get("date")
        if isinstance(date_text, str):
            output[date_text] = item
    return output


def _daily_returns(arm: dict[str, Any], matched_dates: list[str]) -> list[float]:
    daily = _daily_map(arm)
    output: list[float] = []
    for date_text in matched_dates:
        item = daily.get(date_text)
        if item is None:
            continue
        value = item.get("return")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        number = float(value)
        if math.isfinite(number):
            output.append(number)
    return output


def _nav_series(arm: dict[str, Any], matched_dates: list[str]) -> list[float]:
    initial = float(arm["initialNavUsd"])
    daily = _daily_map(arm)
    values = [initial]
    for date_text in matched_dates:
        item = daily.get(date_text)
        if item is None:
            continue
        value = item.get("navUsd")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        number = float(value)
        if number > 0 and math.isfinite(number):
            values.append(number)
    live = float(arm["navUsd"])
    if live > 0 and math.isfinite(live) and abs(live - values[-1]) > 1e-9:
        values.append(live)
    return values


def _maximum_drawdown(values: list[float]) -> float:
    if not values:
        return 0.0
    peak = values[0]
    maximum = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            maximum = min(maximum, value / peak - 1.0)
    return maximum


def _annualized_volatility(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    deviation = _population_standard_deviation(values)
    return float(deviation or 0.0) * math.sqrt(ANNUAL_DAYS)


def _annualized_downside(values: list[float]) -> float:
    if not values:
        return 0.0
    mean_square = sum(min(value, 0.0) ** 2 for value in values) / len(values)
    return math.sqrt(mean_square * ANNUAL_DAYS)


def _sum_daily(arm: dict[str, Any], matched_dates: list[str], key: str) -> float:
    daily = _daily_map(arm)
    total = 0.0
    for date_text in matched_dates:
        item = daily.get(date_text)
        value = item.get(key) if item else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        number = float(value)
        if math.isfinite(number):
            total += number
    return total


def _arm_metrics(arm: dict[str, Any], matched_dates: list[str]) -> dict[str, Any]:
    initial = float(arm["initialNavUsd"])
    nav = float(arm["navUsd"])
    daily_returns = _daily_returns(arm, matched_dates)
    executions = [
        item for item in arm.get("executions") or [] if isinstance(item, dict)
    ]
    turnover_to_nav = sum(
        abs(float(item.get("deltaWeight") or 0.0)) for item in executions
    )
    trading_costs_usd = _sum_daily(arm, matched_dates, "tradeCostUsd")
    funding_pnl_usd = _sum_daily(arm, matched_dates, "fundingPnlUsd") + float(
        arm.get("liveFundingPnlUsd") or 0.0
    )
    maximum_drawdown = _maximum_drawdown(_nav_series(arm, matched_dates))
    annualized_volatility = _annualized_volatility(daily_returns)
    return_since_reset = nav / initial - 1.0 if initial > 0 else 0.0
    return {
        "strategyId": arm["strategyId"],
        "strategyVersion": arm["strategyVersion"],
        "profile": arm.get("profile"),
        "sourceCommit": arm.get("sourceCommit"),
        "asOf": arm.get("asOf"),
        "effectiveDate": arm.get("effectiveDate"),
        "actualResetDate": arm.get("actualResetDate"),
        "initialNavUsd": initial,
        "navUsd": nav,
        "returnSinceReset": return_since_reset,
        "maximumDrawdown": maximum_drawdown,
        "annualizedVolatility": annualized_volatility,
        "annualizedDownsideVolatility": _annualized_downside(daily_returns),
        "returnToDrawdown": (
            return_since_reset / abs(maximum_drawdown)
            if maximum_drawdown < -EPSILON
            else None
        ),
        "returnToVolatility": (
            return_since_reset / annualized_volatility
            if annualized_volatility > EPSILON
            else None
        ),
        "targetGross": float(arm.get("targetGross") or 0.0),
        "targetNet": float(arm.get("targetNet") or 0.0),
        "paperGrossCap": float(arm.get("paperGrossCap") or 0.0),
        "paperAssetCap": float(arm.get("paperAssetCap") or 0.0),
        "dynamicGrossCap": arm.get("dynamicGrossCap"),
        "materialExecutions": len(executions),
        "totalExecutions": int(arm.get("totalExecutions") or 0),
        "turnoverToNav": turnover_to_nav,
        "tradingCostsUsd": trading_costs_usd,
        "fundingPnlUsd": funding_pnl_usd,
        "fundingActualIntervals": int(arm.get("fundingActualIntervals") or 0),
        "fundingFallbackIntervals": int(arm.get("fundingFallbackIntervals") or 0),
        "matchedDailyPoints": len(daily_returns),
    }


def _numeric_deltas(v1: dict[str, Any], v2: dict[str, Any]) -> dict[str, float]:
    keys = (
        "navUsd",
        "returnSinceReset",
        "maximumDrawdown",
        "annualizedVolatility",
        "annualizedDownsideVolatility",
        "targetGross",
        "targetNet",
        "materialExecutions",
        "totalExecutions",
        "turnoverToNav",
        "tradingCostsUsd",
        "fundingPnlUsd",
    )
    return {
        key: float(v2.get(key) or 0.0) - float(v1.get(key) or 0.0)
        for key in keys
    }


def _quality(v1: dict[str, Any], v2: dict[str, Any]) -> dict[str, Any]:
    same_as_of = v1.get("asOf") == v2.get("asOf")
    same_effective_date = v1.get("effectiveDate") == v2.get("effectiveDate")
    same_reset = v1.get("actualResetDate") == v2.get("actualResetDate")
    same_starting_cash = math.isclose(
        float(v1.get("initialNavUsd") or 0.0),
        float(v2.get("initialNavUsd") or 0.0),
        rel_tol=0.0,
        abs_tol=1e-9,
    )
    return {
        "sameAsOf": same_as_of,
        "sameEffectiveDate": same_effective_date,
        "sameResetDate": same_reset,
        "sameStartingCash": same_starting_cash,
        "v1SourcePinned": v1.get("sourceCommit") == V1_REFERENCE_SOURCE_COMMIT,
        "v2Persistent": bool(v2.get("persistent")),
        "matched": (
            same_as_of and same_effective_date and same_reset and same_starting_cash
        ),
    }


def _append_observation(
    path: Path,
    *,
    as_of: str,
    quality: dict[str, Any],
    v1_metrics: dict[str, Any],
    v2_metrics: dict[str, Any],
    deltas: dict[str, float],
) -> dict[str, Any]:
    state = verify_ab_journal(path)
    if state["lastAsOf"] == as_of:
        return state
    if state["lastAsOf"] is not None and as_of < str(state["lastAsOf"]):
        raise ValueError("A/B observation would move the forward clock backwards")
    event = {
        "schema_version": AB_JOURNAL_SCHEMA_VERSION,
        "studyId": AB_STUDY_ID,
        "sequence": int(state["events"]) + 1,
        "observedAt": _utc_now(),
        "asOf": as_of,
        "previousHash": state["lastHash"],
        "quality": quality,
        "v1": v1_metrics,
        "v2": v2_metrics,
        "deltasV2MinusV1": deltas,
    }
    event["eventHash"] = _sha256(event)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                event,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        )
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return verify_ab_journal(path)


def _review_status(observations: int) -> str:
    if observations >= AB_PREFERRED_REVIEW_DAYS:
        return "eligible_for_decision"
    if observations >= AB_INTERMEDIATE_REVIEW_DAYS:
        return "intermediate_review"
    if observations >= AB_MINIMUM_REVIEW_DAYS:
        return "initial_review"
    return "collecting"


def build_ab_snapshot(
    histories: list[dict[str, Any]],
    failed_assets: list[dict[str, str]],
    *,
    v2_snapshot: dict[str, Any],
    snapshot_path: Path,
    journal_path: Path,
    reset_date: str,
    initial_nav_usd: float,
) -> dict[str, Any]:
    v1_arm = build_v1_reference_arm(
        histories,
        failed_assets,
        reset_date=reset_date,
        initial_nav_usd=initial_nav_usd,
    )
    v2_arm = build_v2_forward_arm(v2_snapshot)
    quality = _quality(v1_arm, v2_arm)
    v1_dates = set(_daily_map(v1_arm))
    v2_dates = set(_daily_map(v2_arm))
    matched_dates = sorted(v1_dates & v2_dates)
    v1_metrics = _arm_metrics(v1_arm, matched_dates)
    v2_metrics = _arm_metrics(v2_arm, matched_dates)
    deltas = _numeric_deltas(v1_metrics, v2_metrics)
    if quality["matched"]:
        journal = _append_observation(
            journal_path,
            as_of=str(v2_arm["asOf"]),
            quality=quality,
            v1_metrics=v1_metrics,
            v2_metrics=v2_metrics,
            deltas=deltas,
        )
    else:
        journal = verify_ab_journal(journal_path)
    observations = int(journal["events"])
    status = _review_status(observations) if quality["matched"] else "invalid_pair"
    result = {
        "schema_version": AB_SCHEMA_VERSION,
        "studyId": AB_STUDY_ID,
        "mode": "paper_observability",
        "status": status,
        "generatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "asOf": v2_arm.get("asOf"),
        "effectiveDate": v2_arm.get("effectiveDate"),
        "quality": quality,
        "forwardObservationDays": observations,
        "minimumReviewDays": AB_MINIMUM_REVIEW_DAYS,
        "intermediateReviewDays": AB_INTERMEDIATE_REVIEW_DAYS,
        "preferredReviewDays": AB_PREFERRED_REVIEW_DAYS,
        "remainingToInitialReview": max(0, AB_MINIMUM_REVIEW_DAYS - observations),
        "remainingToPreferredReview": max(0, AB_PREFERRED_REVIEW_DAYS - observations),
        "matchedHistoricalPoints": len(matched_dates),
        "arms": {
            "legacyV1Reference": v1_metrics,
            "forwardV2": v2_metrics,
        },
        "deltasV2MinusV1": deltas,
        "interpretation": {
            "winnerDeclared": False,
            "reason": (
                "Forward evidence is still collecting; no arm is promoted automatically."
                if status != "eligible_for_decision"
                else "The preferred observation window is complete; human review is required."
            ),
        },
        "persistence": {
            "snapshotPath": str(snapshot_path),
            "journalPath": str(journal_path),
            "journal": journal,
        },
        "exchange_submission_available": False,
        "live_ready": False,
        "real_leverage_authorized": False,
    }
    _write_atomic(snapshot_path, result)
    return result
