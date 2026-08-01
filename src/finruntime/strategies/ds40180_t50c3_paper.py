"""Paper-only OKX port of the frozen DS-40/180 T50-C3 trend strategy.

The worker consumes only public OKX USDT-margined perpetual market data. It
reconstructs the three frozen Stage-1 sleeves, applies the DS-40/180 regime and
T50-C3 weekly volatility target, and replays an isolated paper account from a
new forward-clock reset. There is deliberately no authenticated exchange
client and no order-submission code in this module.
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

from ._ds40180_account import (
    _funding_return_for_weight,
    _mark_portfolio_to_live,
    paper_continuation,
)
from ._ds40180_common import (
    ASSETS,
    EPSILON,
    INSTRUMENTS,
    MATERIAL_DELTA,
    MISSING_FUNDING_FALLBACK_ANNUAL,
    OKX_BAR,
    PAPER_ASSET_CAP,
    PAPER_GROSS_CAP,
    PROFILE_NAME,
    RISK_SCALE_CAP,
    RISK_SCALE_FLOOR,
    SNAPSHOT_DATE,
    STRATEGY_ID,
    TARGET_VOLATILITY,
    _gross,
    _utc_now,
)
from ._ds40180_engine import build_engine
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
                "funding": [
                    [item["fundingTime"], item["rate"]]
                    for item in history.get("funding", [])
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


def compute_forward_state(
    histories: list[dict[str, Any]],
    failed_assets: list[dict[str, str]],
    *,
    reset_date: str = SNAPSHOT_DATE,
    initial_nav_usd: float = 10_000.0,
) -> dict[str, Any]:
    if initial_nav_usd <= 0 or not math.isfinite(initial_nav_usd):
        raise ValueError("initial_nav_usd must be positive and finite")
    engine = build_engine(histories, failed_assets)
    latest_index = len(engine["dates"]) - 1
    latest_date = engine["dates"][latest_index]
    continuation = paper_continuation(
        engine,
        histories,
        reset_date=reset_date,
        initial_nav_usd=initial_nav_usd,
    )
    live = _mark_portfolio_to_live(engine, histories, continuation)
    peak_nav = max(
        initial_nav_usd,
        live["navUsd"],
        *(float(point["navUsd"]) for point in continuation["daily"]),
    )
    target = continuation["target"]
    target_gross = _gross(target)
    target_net = sum(target)
    generated_at = _utc_now()
    material_executions = [
        item
        for item in continuation["executions"]
        if abs(float(item["deltaWeight"])) >= MATERIAL_DELTA
        or abs(float(item["oldWeight"])) <= EPSILON
        or abs(float(item["newWeight"])) <= EPSILON
    ]
    warnings = [
        f"{failure['instrumentId']}: {failure['reason']}" for failure in failed_assets
    ]
    for history in histories:
        warnings.extend(
            f"{history['instrumentId']}: {warning}"
            for warning in history.get("warnings", [])
        )
    candles = []
    for history in histories:
        if history["asset"] not in engine["assets"]:
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
        candles.append(
            {
                "asset": history["asset"],
                "exchange_id": "okx",
                "instrument_id": history["instrumentId"],
                "market_type": "swap",
                "timeframe": OKX_BAR,
                "items": items,
            }
        )
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
    latest_allocation = engine["sleeveAllocations"][latest_index]
    return {
        "schema_version": 1,
        "strategyId": STRATEGY_ID,
        "strategyVersion": "okx-paper-v1",
        "profile": PROFILE_NAME,
        "identityKind": "new_okx_paper_port",
        "historicalMetricsInherited": False,
        "mode": "paper",
        "status": "ready",
        "generatedAt": generated_at,
        "marketDataAt": market_data_at,
        "asOf": latest_date,
        "snapshotDate": reset_date,
        "inputSha256": _input_digest(histories, engine["dates"]),
        "venue": "okx",
        "instrumentType": "SWAP",
        "bar": OKX_BAR,
        "assets": engine["assets"],
        "failedAssets": failed_assets,
        "inactiveAssets": engine["inactiveAssets"],
        "eligibleAssets": [
            asset
            for asset, allowed in zip(
                engine["assets"], engine["eligible"][latest_index], strict=True
            )
            if allowed
        ],
        "regime": {
            "mom180": engine["mom180"][latest_index],
            "mom40": engine["mom40"][latest_index],
            "re180Bear": bool(engine["re180Bear"][latest_index]),
            "early40Bear": bool(engine["early40Bear"][latest_index]),
            "combinedBear": bool(engine["combinedBear"][latest_index]),
        },
        "sleeveAllocation": {
            "longOnly": latest_allocation[0],
            "lightShortHedge": latest_allocation[1],
            "slowBear": latest_allocation[2],
        },
        "targetVolatility": TARGET_VOLATILITY,
        "riskScaleFloor": RISK_SCALE_FLOOR,
        "riskScaleCap": RISK_SCALE_CAP,
        "riskScale": engine["riskScale"][latest_index],
        "rawRiskScale": engine["rawRiskScale"][latest_index],
        "laggedRealizedVolatility": engine["laggedRealizedVolatility"][latest_index],
        "paperGrossCap": PAPER_GROSS_CAP,
        "paperAssetCap": PAPER_ASSET_CAP,
        "safetyGrossCapApplied": engine["grossCapApplied"][latest_index],
        "targetGross": target_gross,
        "targetNet": target_net,
        "targetWeights": dict(zip(engine["assets"], target, strict=True)),
        "netExposure": live["netExposure"],
        "grossExposure": live["grossExposure"],
        "positions": live["positions"],
        "candles": candles,
        "paper": {
            "account": {
                "initialNavUsd": initial_nav_usd,
                "resetDate": reset_date,
                "venue": "okx-public-paper",
            },
            "currentDrawdown": live["navUsd"] / peak_nav - 1.0,
            "daily": continuation["daily"],
            "executions": material_executions,
            "lastOrderDate": (
                material_executions[-1]["orderDate"] if material_executions else None
            ),
            "navUsd": live["navUsd"],
            "pnlSinceSnapshotUsd": live["navUsd"] - initial_nav_usd,
            "returnSinceSnapshot": live["navUsd"] / initial_nav_usd - 1.0,
            "totalExecutions": len(continuation["executions"]),
        },
        "funding": {
            "source": "OKX realizedRate; fundingRate fallback",
            "actualIntervals": continuation["fundingActualIntervals"],
            "fallbackIntervals": continuation["fundingFallbackIntervals"],
            "missingDataFallbackAnnual": MISSING_FUNDING_FALLBACK_ANNUAL,
        },
        "warnings": warnings,
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
        reset_date=reset_date,
        initial_nav_usd=initial_nav_usd,
    )
    _write_atomic(path, snapshot)
    return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the frozen DS-40/180 T50-C3 strategy on public OKX swaps in "
            "paper-only mode"
        )
    )
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=300.0)
    parser.add_argument("--reset-date", default=SNAPSHOT_DATE)
    parser.add_argument("--starting-cash", type=float, default=10_000.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
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
                        "status": snapshot["status"],
                        "as_of": snapshot["asOf"],
                        "assets": len(snapshot["assets"]),
                        "nav_usd": round(float(snapshot["paper"]["navUsd"]), 4),
                        "target_gross": round(float(snapshot["targetGross"]), 6),
                        "risk_scale": round(float(snapshot["riskScale"]), 6),
                        "bear": snapshot["regime"]["combinedBear"],
                    },
                    separators=(",", ":"),
                ),
                flush=True,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
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
