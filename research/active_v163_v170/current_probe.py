#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SERVICE_SRC = ROOT / "services" / "funding_router" / "src"
if str(SERVICE_SRC) not in sys.path:
    sys.path.insert(0, str(SERVICE_SRC))

from funding_router.config import load_settings  # noqa: E402
from funding_router.scanner import FundingScanner  # noqa: E402
from funding_router.service import build_gateways  # noqa: E402


def snapshot_dict(item: Any) -> dict[str, Any]:
    payload = asdict(item)
    payload.update(
        {
            "exchange_id": item.exchange_id,
            "symbol": item.symbol,
            "asset": item.asset,
        }
    )
    return payload


async def close_quietly(gateway: Any) -> str | None:
    try:
        await gateway.close()
        return None
    except Exception as exc:  # noqa: BLE001 - closure evidence is intentional
        return f"{type(exc).__name__}: {exc}"


async def run(config: Path, output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    settings = load_settings(config)
    all_gateways = build_gateways(settings)
    initialized: dict[str, Any] = {}
    initialization_errors: list[str] = []
    close_errors: list[str] = []

    for exchange in settings.enabled_exchanges:
        gateway = all_gateways[exchange.id]
        try:
            await gateway.initialize()
            initialized[exchange.id] = gateway
        except Exception as exc:  # noqa: BLE001 - retain venue blocker evidence
            initialization_errors.append(
                f"{exchange.id}: {type(exc).__name__}: {exc}"
            )
            close_error = await close_quietly(gateway)
            if close_error:
                close_errors.append(f"{exchange.id}: {close_error}")

    scan: dict[str, Any]
    snapshots: list[dict[str, Any]] = []
    scanner: FundingScanner | None = None
    try:
        if initialized:
            active_exchanges = tuple(
                exchange
                for exchange in settings.enabled_exchanges
                if exchange.id in initialized
            )
            active_settings = replace(settings, exchanges=active_exchanges)
            scanner = FundingScanner(active_settings, initialized)
            result = await scanner.scan_once()
            scan = result.to_dict()
            snapshots = [
                snapshot_dict(item)
                for _, item in sorted(
                    scanner.last_snapshots.items(), key=lambda row: row[0]
                )
            ]
        else:
            scan = {
                "observed_at_ms": None,
                "candidates": [],
                "rejections": [],
                "errors": [],
            }
    finally:
        for exchange_id, gateway in initialized.items():
            close_error = await close_quietly(gateway)
            if close_error:
                close_errors.append(f"{exchange_id}: {close_error}")

    expected = [
        {"exchange": exchange.id, "symbol": symbol}
        for exchange in settings.enabled_exchanges
        for symbol in exchange.markets
    ]
    observed = [
        {"exchange": item["exchange_id"], "symbol": item["symbol"]}
        for item in snapshots
    ]
    observed_keys = {(item["exchange"], item["symbol"]) for item in observed}
    missing = [
        item
        for item in expected
        if (item["exchange"], item["symbol"]) not in observed_keys
    ]
    all_errors = [
        *initialization_errors,
        *list(scan.get("errors", [])),
        *close_errors,
    ]
    coverage = {
        "expected": expected,
        "initialized_exchanges": sorted(initialized),
        "initialization_errors": initialization_errors,
        "observed": observed,
        "missing": missing,
        "scan_errors": list(scan.get("errors", [])),
        "close_errors": close_errors,
    }

    candidate_rows = list(scan.get("candidates", []))
    observed_venues = sorted({item["exchange_id"] for item in snapshots})
    if len(observed_venues) < 2:
        status = "insufficient_cross_venue_access"
    elif missing:
        status = "cross_venue_access_partial"
    else:
        status = "cross_venue_access_complete"

    summary = {
        "candidate": "V163_CURRENT_FUNDING_DISLOCATION_PROBE",
        "as_of_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "expected_exchange_count": len(settings.enabled_exchanges),
        "initialized_exchange_count": len(initialized),
        "observed_exchange_count": len(observed_venues),
        "observed_exchanges": observed_venues,
        "expected_snapshot_count": len(expected),
        "observed_snapshot_count": len(snapshots),
        "missing_snapshot_count": len(missing),
        "candidate_count": len(candidate_rows),
        "rejection_count": len(scan.get("rejections", [])),
        "error_count": len(all_errors),
        "initialization_errors": initialization_errors,
        "scan_errors": list(scan.get("errors", [])),
        "top_candidates": candidate_rows[:10],
        "historical_research_permitted": len(observed_venues) >= 2,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }

    payloads = {
        "scan.json": scan,
        "snapshots.json": snapshots,
        "coverage.json": coverage,
        "summary.json": summary,
    }
    hashes: dict[str, dict[str, object]] = {}
    for name, value in payloads.items():
        data = (
            json.dumps(value, indent=2, ensure_ascii=False, default=str).encode("utf-8")
            + b"\n"
        )
        (output / name).write_bytes(data)
        hashes[name] = {
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    (output / "MANIFEST.json").write_text(
        json.dumps({"files": hashes}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    return asyncio.run(run(args.config, args.output))


if __name__ == "__main__":
    raise SystemExit(main())
