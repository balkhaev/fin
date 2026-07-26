#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE_SRC = ROOT / "services" / "funding_router" / "src"
if str(SERVICE_SRC) not in sys.path:
    sys.path.insert(0, str(SERVICE_SRC))

from funding_router.config import load_settings  # noqa: E402
from funding_router.scanner import FundingScanner  # noqa: E402
from funding_router.service import build_gateways  # noqa: E402


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    ).encode("utf-8")


async def run(config: Path, output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    settings = load_settings(config)
    scanner = FundingScanner(settings, build_gateways(settings))
    try:
        await scanner.initialize()
        result = await scanner.scan_once()
    finally:
        await scanner.close()

    scan = result.to_dict()
    snapshots = [
        item.to_dict()
        for _, item in sorted(scanner.last_snapshots.items(), key=lambda row: row[0])
    ]
    coverage = {
        "expected": [
            {"exchange": exchange.id, "symbol": symbol}
            for exchange in settings.enabled_exchanges
            for symbol in exchange.markets
        ],
        "observed": [
            {"exchange": item["exchange_id"], "symbol": item["symbol"]}
            for item in snapshots
        ],
        "errors": list(scan.get("errors", [])),
    }
    observed_keys = {(item["exchange"], item["symbol"]) for item in coverage["observed"]}
    coverage["missing"] = [
        item
        for item in coverage["expected"]
        if (item["exchange"], item["symbol"]) not in observed_keys
    ]

    candidate_rows = list(scan.get("candidates", []))
    summary = {
        "candidate": "V163_CURRENT_FUNDING_DISLOCATION_PROBE",
        "as_of_utc": datetime.now(timezone.utc).isoformat(),
        "expected_snapshot_count": len(coverage["expected"]),
        "observed_snapshot_count": len(snapshots),
        "missing_snapshot_count": len(coverage["missing"]),
        "candidate_count": len(candidate_rows),
        "rejection_count": len(scan.get("rejections", [])),
        "error_count": len(scan.get("errors", [])),
        "top_candidates": candidate_rows[:10],
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
        data = json.dumps(value, indent=2, ensure_ascii=False, default=str).encode("utf-8") + b"\n"
        (output / name).write_bytes(data)
        hashes[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
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
