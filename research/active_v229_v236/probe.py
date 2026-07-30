#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests

ASSETS = ("BTC", "ETH")
SCAN_START = pd.Timestamp("2021-01-01", tz="UTC")
SCAN_END = pd.Timestamp("2026-06-30", tz="UTC")
LATEST_REQUIRED_EXPIRY = date(2026, 6, 26)
MIN_CONTRACT_SHARE = 0.80
MIN_VALID_MONTHS = 2
BASE = "https://data.binance.vision/data/futures/um/monthly/klines"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def session() -> requests.Session:
    client = requests.Session()
    client.headers.update(
        {
            "User-Agent": "fin-research-v229/1.0 (+https://github.com/balkhaev/fin)",
            "Accept": "application/zip,text/plain,*/*",
        }
    )
    return client


def get(client: requests.Session, url: str) -> requests.Response:
    last: Exception | None = None
    for attempt in range(5):
        try:
            response = client.get(url, timeout=60)
            if response.status_code in {429, 500, 502, 503, 504}:
                time.sleep(1.0 + attempt)
                continue
            return response
        except requests.RequestException as exc:
            last = exc
            time.sleep(1.0 + attempt)
    if last is not None:
        raise last
    raise RuntimeError(url)


def last_friday(year: int, month: int) -> date:
    if month == 12:
        cursor = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        cursor = date(year, month + 1, 1) - timedelta(days=1)
    while cursor.weekday() != 4:
        cursor -= timedelta(days=1)
    return cursor


def expiries() -> list[date]:
    result = []
    for year in range(2021, 2027):
        for month in (3, 6, 9, 12):
            value = last_friday(year, month)
            if value <= SCAN_END.date():
                result.append(value)
    return result


def contract_months(expiry: date) -> list[str]:
    start = pd.Timestamp(expiry) - pd.Timedelta(days=185)
    return [
        str(value)
        for value in pd.period_range(start.to_period("M"), pd.Timestamp(expiry).to_period("M"), freq="M")
    ]


def parse_zip(payload: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise ValueError(f"expected one CSV, got {names}")
        raw = archive.read(names[0])
    rows = [row for row in csv.reader(io.StringIO(raw.decode("utf-8-sig", errors="replace"))) if row]
    if not rows:
        return {"rows": 0, "widths": [], "member": names[0]}
    first = str(rows[0][0]).lower()
    has_header = "time" in first or "open" in first
    data = rows[1:] if has_header else rows
    widths = sorted({len(row) for row in data[:1000]})
    timestamps = []
    for row in data[:1000]:
        try:
            timestamps.append(int(float(row[0])))
        except (ValueError, TypeError, IndexError):
            continue
    return {
        "member": names[0],
        "rows": len(data),
        "widths": widths,
        "has_header": has_header,
        "timestamp_min_sample": min(timestamps) if timestamps else None,
        "timestamp_max_sample": max(timestamps) if timestamps else None,
        "csv_sha256": sha256_bytes(raw),
    }


def probe_archive(asset: str, expiry: date, month: str) -> dict[str, Any]:
    symbol = f"{asset}USDT_{expiry.strftime('%y%m%d')}"
    filename = f"{symbol}-1h-{month}.zip"
    url = f"{BASE}/{symbol}/1h/{filename}"
    row: dict[str, Any] = {
        "asset": asset,
        "expiry": expiry.isoformat(),
        "symbol": symbol,
        "month": month,
        "url": url,
    }
    client = session()
    try:
        checksum = get(client, url + ".CHECKSUM")
        archive = get(client, url)
        row["checksum_status"] = checksum.status_code
        row["http_status"] = archive.status_code
        row["bytes"] = len(archive.content)
        if checksum.status_code != 200 or archive.status_code != 200:
            row["valid"] = False
            row["reason"] = "missing_archive_or_checksum"
            return row
        expected = checksum.text.strip().split()[0].lower()
        actual = sha256_bytes(archive.content)
        row["expected_sha256"] = expected
        row["archive_sha256"] = actual
        row["checksum_valid"] = expected == actual
        row["parsed"] = parse_zip(archive.content)
        row["valid"] = bool(row["checksum_valid"] and row["parsed"]["rows"] > 0)
        row["reason"] = "ok" if row["valid"] else "checksum_or_parse_failure"
    except Exception as exc:  # noqa: BLE001
        row["valid"] = False
        row["reason"] = f"{type(exc).__name__}: {exc}"
    return row


def self_test() -> None:
    assert last_friday(2021, 3) == date(2021, 3, 26)
    assert last_friday(2024, 6) == date(2024, 6, 28)
    assert LATEST_REQUIRED_EXPIRY in expiries()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("x.csv", "open_time,open,high,low,close\n1,1,1,1,1\n")
    parsed = parse_zip(buffer.getvalue())
    assert parsed["rows"] == 1 and parsed["has_header"] is True
    print("V229 calendar archive probe self-test passed")


def write_manifest(root: Path) -> None:
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            rel = str(path.relative_to(root))
            if rel not in {"MANIFEST.json", "probe.log"}:
                files[rel] = {
                    "bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
    (root / "MANIFEST.json").write_text(
        json.dumps({"candidate": "ACTIVE_V229_USDM_PERP_QUARTERLY_SPREAD", "files": files}, indent=2) + "\n"
    )


def run(root: Path) -> int:
    results = root / "probe_results"
    results.mkdir(parents=True, exist_ok=True)
    tasks = [
        (asset, expiry, month)
        for asset in ASSETS
        for expiry in expiries()
        for month in contract_months(expiry)
    ]
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(probe_archive, asset, expiry, month): (asset, expiry, month)
            for asset, expiry, month in tasks
        }
        for number, future in enumerate(as_completed(futures), start=1):
            rows.append(future.result())
            if number % 50 == 0:
                print(f"probed {number}/{len(tasks)} archives", flush=True)
    rows.sort(key=lambda item: (item["asset"], item["expiry"], item["month"]))
    (results / "raw_probe.json").write_text(json.dumps(rows, indent=2) + "\n")

    contract_rows = []
    assets_summary: dict[str, Any] = {}
    passed = True
    for asset in ASSETS:
        valid_contracts = 0
        per_contract = []
        for expiry in expiries():
            selected = [
                row for row in rows if row["asset"] == asset and row["expiry"] == expiry.isoformat()
            ]
            valid_months = sum(bool(row.get("valid")) for row in selected)
            row = {
                "asset": asset,
                "expiry": expiry.isoformat(),
                "symbol": f"{asset}USDT_{expiry.strftime('%y%m%d')}",
                "attempted_months": len(selected),
                "valid_months": valid_months,
                "first_valid_month": next((item["month"] for item in selected if item.get("valid")), None),
                "last_valid_month": next((item["month"] for item in reversed(selected) if item.get("valid")), None),
                "contract_passed": valid_months >= MIN_VALID_MONTHS,
            }
            valid_contracts += int(row["contract_passed"])
            per_contract.append(row)
            contract_rows.append(row)
        total = len(per_contract)
        share = valid_contracts / total if total else 0.0
        latest = next(
            (row for row in per_contract if row["expiry"] == LATEST_REQUIRED_EXPIRY.isoformat()),
            None,
        )
        asset_passed = bool(
            share >= MIN_CONTRACT_SHARE
            and latest is not None
            and latest["contract_passed"]
        )
        assets_summary[asset] = {
            "expected_contracts": total,
            "valid_contracts": valid_contracts,
            "valid_contract_share": share,
            "minimum_contract_share": MIN_CONTRACT_SHARE,
            "minimum_valid_months_per_contract": MIN_VALID_MONTHS,
            "latest_required_expiry": LATEST_REQUIRED_EXPIRY.isoformat(),
            "latest_required_contract_passed": bool(latest and latest["contract_passed"]),
            "passed": asset_passed,
        }
        passed = passed and asset_passed
    pd.DataFrame(contract_rows).to_csv(results / "contract_coverage.csv", index=False)
    gate = {
        "candidate": "V229_USDM_QUARTERLY_ARCHIVE_COVERAGE",
        "symbol_rule": "<ASSET>USDT_YYMMDD",
        "expiry_rule": "last Friday of quarter",
        "scan_start": SCAN_START.isoformat(),
        "scan_end": SCAN_END.isoformat(),
        "assets": assets_summary,
        "passed": bool(passed),
        "coin_m_fallback_permitted": False,
    }
    (results / "coverage_gate.json").write_text(json.dumps(gate, indent=2) + "\n")
    summary = {
        "candidate": "ACTIVE_V229_USDM_PERP_QUARTERLY_SPREAD",
        "status": "data_gate_passed_needs_full_research" if passed else "usd_m_quarterly_data_insufficient",
        "coverage_gate": gate,
        "selection_run": False,
        "full_backtest_run": False,
        "continuous_research_permitted": bool(passed),
        "integration_permitted": False,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    (results / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (results / "FROZEN_DECISION.json").write_text(json.dumps(summary, indent=2) + "\n")
    lines = [
        "# Active V229 — USD-M perpetual/quarterly archive gate",
        "",
        f"Status: `{summary['status']}`.",
        "",
        "No strategy P&L or policy selection was computed.",
    ]
    for asset, value in assets_summary.items():
        lines.append(
            f"- {asset}: {value['valid_contracts']}/{value['expected_contracts']} valid contracts; "
            f"latest required passed: {value['latest_required_contract_passed']}."
        )
    (results / "REPORT_RU.md").write_text("\n".join(lines) + "\n")
    write_manifest(root)
    print(json.dumps(summary, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    return run(args.root)


if __name__ == "__main__":
    raise SystemExit(main())
