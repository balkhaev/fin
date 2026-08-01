"""Forward-only OKX paper port of the DS-40/180 T50-C3 strategy.

The worker consumes only public OKX USDT-margined perpetual market data. It
builds the frozen daily sleeves, applies the v2 early/confirmed bear regime,
funding guard, covariance stress cap, a small 4h crisis overlay and a real
no-trade band. Paper account history is persisted and never recomputed after
bootstrap. There is deliberately no authenticated exchange client and no
order-submission code in this module.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ._ds40180_common import (
    EPSILON,
    MATERIAL_DELTA,
    MISSING_FUNDING_FALLBACK_ANNUAL,
    OKX_BAR,
    OKX_INTRADAY_BAR,
    PAPER_ASSET_CAP,
    PAPER_GROSS_CAP,
    PROFILE_NAME,
    RISK_SCALE_CAP,
    RISK_SCALE_FLOOR,
    SNAPSHOT_DATE,
    STRATEGY_ID,
    STRATEGY_VERSION,
    TARGET_VOLATILITY,
    _gross,
    _utc_now,
)
from ._ds40180_engine import build_engine
from ._ds40180_forward import load_or_advance_forward_state, verify_journal
from ._ds40180_okx import load_market_data


def _input_digest(histories: list[dict[str, Any]], dates: list[str]) -> str:
    payload: list[dict[str, Any]] = []
    date_set = set(dates)
    for history in histories:
        payload.append(
            {
                "asset": history["asset"],
                "bars": [
                    [
                        date_text,
                        history["bars"][date_text]["open"],
                        history["bars"][date_text]["high"],
                        history["bars"][date_text]["low"],
                        history["bars"][date_text]["close"],
                        history["bars"][date_text]["quoteVolume"],
                    ]
                    for date_text in sorted(history["bars"])
                    if date_text in date_set
                ],
            }
        )
    encoded = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _candles_payload(
    histories: list[dict[str, Any]], assets: list[str]
) -> list[dict[str, Any]]:
    output = []
    for history in histories:
        if history["asset"] not in assets:
            continue
        items = [
            {
                "timestamp_ms": int(candle["openTime"]),
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
            }
            for _date_text, candle in sorted(history["bars"].items())[-120:]
        ]
        output.append(
            {
                "asset": history["asset"],
                "exchange_id": "okx",
                "instrument_id": history["instrumentId"],
                "market_type": "swap",
                "timeframe": OKX_BAR,
                "items": items,
            }
        )
    return output


def compute_forward_state(
    histories: list[dict[str, Any]],
    failed_assets: list[dict[str, str]],
    *,
    snapshot_path: Path,
    reset_date: str = SNAPSHOT_DATE,
    initial_nav_usd: float = 10_000.0,
) -> dict[str, Any]:
    if initial_nav_usd <= 0 or not math.isfinite(initial_nav_usd):
        raise ValueError("initial_nav_usd must be positive and finite")
    engine = build_engine(histories, failed_assets)
    latest_index = int(engine["latestMarketIndex"])
    decision_index = int(engine["executionIndex"])
    latest_date = engine["marketDates"][latest_index]
    effective_date = engine["executionDate"]
    state, live, persistence = load_or_advance_forward_state(
        snapshot_path=snapshot_path,
        engine=engine,
        histories=histories,
        reset_date=reset_date,
        initial_nav_usd=initial_nav_usd,
    )
    target = [float(value) for value in state["targetWeights"]]
    target_gross = _gross(target)
    target_net = sum(target)
    generated_at = _utc_now()
    material_executions = [
        item
        for item in state["executions"]
        if abs(float(item.get("deltaWeight") or 0.0)) >= MATERIAL_DELTA
        or abs(float(item.get("oldWeight") or 0.0)) <= EPSILON
        or abs(float(item.get("newWeight") or 0.0)) <= EPSILON
    ]
    warnings = [
        f"{failure['instrumentId']}: {failure['reason']}" for failure in failed_assets
    ]
    for history in histories:
        warnings.extend(
            f"{history['instrumentId']}: {warning}"
            for warning in history.get("warnings", [])
        )
    warnings.extend(str(item) for item in state.get("warnings") or [])
    mark_times = [
        int(history.get("markTimeMs") or 0)
        for history in histories
        if history["asset"] in engine["assets"]
    ]
    market_data_at = (
        datetime.fromtimestamp(max(mark_times) / 1000, UTC)
        .isoformat()
        .replace("+00:00", "Z")
        if mark_times and max(mark_times) > 0
        else generated_at
    )
    latest_allocation = engine["sleeveAllocations"][decision_index]
    latest_plan = state.get("lastPlan") if isinstance(state.get("lastPlan"), dict) else {}
    regime_state = int(engine["regimeState"][decision_index])
    return {
        "schema_version": 2,
        "strategyId": STRATEGY_ID,
        "strategyVersion": STRATEGY_VERSION,
        "profile": PROFILE_NAME,
        "identityKind": "persistent_okx_paper_port",
        "historicalMetricsInherited": False,
        "mode": "paper",
        "status": "ready",
        "generatedAt": generated_at,
        "marketDataAt": market_data_at,
        "asOf": latest_date,
        "effectiveDate": effective_date,
        "snapshotDate": reset_date,
        "inputSha256": _input_digest(histories, engine["marketDates"]),
        "venue": "okx",
        "instrumentType": "SWAP",
        "bar": OKX_BAR,
        "intradayBar": OKX_INTRADAY_BAR,
        "assets": engine["assets"],
        "failedAssets": failed_assets,
        "inactiveAssets": engine["inactiveAssets"],
        "eligibleAssets": [
            asset
            for asset, allowed in zip(
                engine["assets"], engine["eligible"][decision_index], strict=True
            )
            if allowed
        ],
        "regime": {
            "state": "confirmed_bear"
            if regime_state == 2
            else "early_bear"
            if regime_state == 1
            else "bull",
            "stateCode": regime_state,
            "mom180": engine["mom180"][decision_index],
            "mom40": engine["mom40"][decision_index],
            "re180Bear": bool(engine["re180Bear"][decision_index]),
            "early40Bear": bool(engine["early40Bear"][decision_index]),
            "combinedBear": bool(engine["combinedBear"][decision_index]),
            "slowLongBudget": engine["slowLongBudget"][decision_index],
            "slowShortBudget": engine["slowShortBudget"][decision_index],
        },
        "sleeveAllocation": {
            "longOnly": latest_allocation[0],
            "lightShortHedge": latest_allocation[1],
            "slowBear": latest_allocation[2],
        },
        "targetVolatility": TARGET_VOLATILITY,
        "riskScaleFloor": RISK_SCALE_FLOOR,
        "riskScaleCap": RISK_SCALE_CAP,
        "riskScale": engine["riskScale"][decision_index],
        "rawRiskScale": engine["rawRiskScale"][decision_index],
        "laggedRealizedVolatility": engine["laggedRealizedVolatility"][decision_index],
        "paperGrossCap": PAPER_GROSS_CAP,
        "paperAssetCap": PAPER_ASSET_CAP,
        "dynamicGrossCap": latest_plan.get("dynamicGrossCap"),
        "grossCapRegime": latest_plan.get("grossCapRegime"),
        "safetyGrossCapApplied": latest_plan.get("safetyApplied"),
        "targetGross": target_gross,
        "targetNet": target_net,
        "targetWeights": dict(zip(engine["assets"], target, strict=True)),
        "rawTargetWeights": dict(
            zip(engine["assets"], latest_plan.get("rawTarget") or target, strict=True)
        ),
        "netExposure": live["netExposure"],
        "grossExposure": live["grossExposure"],
        "positions": live["positions"],
        "candles": _candles_payload(histories, engine["assets"]),
        "overlays": {
            "funding": latest_plan.get("funding") or {},
            "covariance": latest_plan.get("covariance") or {},
            "crisis4h": latest_plan.get("crisis") or {},
            "noTrade": latest_plan.get("noTrade") or {},
        },
        "paper": {
            "account": {
                "initialNavUsd": state["initialNavUsd"],
                "requestedResetDate": state["requestedResetDate"],
                "actualResetDate": state["actualResetDate"],
                "venue": "okx-public-paper",
                "bootstrapMode": state["bootstrapMode"],
            },
            "currentDrawdown": live["navUsd"] / float(state["peakNavUsd"]) - 1.0,
            "daily": state["daily"],
            "executions": material_executions,
            "lastOrderDate": (
                material_executions[-1]["orderDate"] if material_executions else None
            ),
            "navUsd": live["navUsd"],
            "navAtLastCommittedReferenceUsd": state["navAtReferenceUsd"],
            "livePricePnlUsd": live["pricePnlUsd"],
            "liveFundingPnlUsd": live["fundingPnlUsd"],
            "pnlSinceSnapshotUsd": live["navUsd"] - float(state["initialNavUsd"]),
            "returnSinceSnapshot": live["navUsd"] / float(state["initialNavUsd"]) - 1.0,
            "targetEffectiveDate": state["targetEffectiveDate"],
            "totalExecutions": state["totalExecutions"],
            "lastProcessedMarketDate": state["lastProcessedMarketDate"],
        },
        "funding": {
            "source": "OKX realizedRate; current-rate sizing guard",
            "actualIntervals": state["fundingActualIntervals"],
            "fallbackIntervals": state["fundingFallbackIntervals"],
            "liveActualIntervals": live["fundingEvents"],
            "liveFundingPnlUsd": live["fundingPnlUsd"],
            "missingDataFallbackAnnual": MISSING_FUNDING_FALLBACK_ANNUAL,
        },
        "persistence": persistence,
        "warnings": warnings[-200:],
        "exchange_submission_available": False,
        "live_ready": False,
        "real_leverage_authorized": False,
    }


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


def run_once(
    path: Path, *, reset_date: str, initial_nav_usd: float
) -> dict[str, Any]:
    histories, failures = load_market_data(reset_date=reset_date)
    snapshot = compute_forward_state(
        histories,
        failures,
        snapshot_path=path,
        reset_date=reset_date,
        initial_nav_usd=initial_nav_usd,
    )
    _write_atomic(path, snapshot)
    return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run DS-40/180 T50-C3 v2 on public OKX swaps in persistent paper-only mode"
        )
    )
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=300.0)
    parser.add_argument("--reset-date", default=SNAPSHOT_DATE)
    parser.add_argument("--starting-cash", type=float, default=10_000.0)
    parser.add_argument("--verify-journal", type=Path)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if args.verify_journal is not None:
        print(json.dumps(verify_journal(args.verify_journal), indent=2))
        return 0
    try:
        date.fromisoformat(args.reset_date)
    except ValueError as error:
        parser.error(f"reset-date must be ISO YYYY-MM-DD: {error}")
    if args.poll_seconds < 60:
        parser.error("poll-seconds must be at least 60")
    if args.starting_cash <= 0 or not math.isfinite(args.starting_cash):
        parser.error("starting-cash must be positive and finite")
    while True:
        started = time.monotonic()
        try:
            snapshot = run_once(
                args.snapshot,
                reset_date=args.reset_date,
                initial_nav_usd=args.starting_cash,
            )
            print(
                json.dumps(
                    {
                        "event": "ds40180_t50c3_paper_snapshot",
                        "version": snapshot["strategyVersion"],
                        "status": snapshot["status"],
                        "as_of": snapshot["asOf"],
                        "effective_date": snapshot["effectiveDate"],
                        "assets": len(snapshot["assets"]),
                        "nav_usd": round(float(snapshot["paper"]["navUsd"]), 4),
                        "target_gross": round(float(snapshot["targetGross"]), 6),
                        "dynamic_gross_cap": snapshot["dynamicGrossCap"],
                        "risk_scale": round(float(snapshot["riskScale"]), 6),
                        "regime": snapshot["regime"]["state"],
                        "crisis": bool(snapshot["overlays"]["crisis4h"].get("active")),
                    },
                    separators=(",", ":"),
                ),
                flush=True,
            )
        except (OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError) as error:
            print(
                json.dumps(
                    {
                        "event": "ds40180_t50c3_paper_error",
                        "error": f"{type(error).__name__}: {error}",
                    },
                    separators=(",", ":"),
                ),
                flush=True,
            )
        if args.once:
            return 0 if args.snapshot.is_file() else 1
        time.sleep(max(0.0, args.poll_seconds - (time.monotonic() - started)))


if __name__ == "__main__":
    raise SystemExit(main())
