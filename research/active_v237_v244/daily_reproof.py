#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd
import requests

ASSETS = ("BTC", "ETH", "SOL")
SYMBOLS = {asset: f"{asset}USDT" for asset in ASSETS}
DATASETS = {
    "premium_index_5m": ("premiumIndexKlines", "5m"),
    "mark_price_5m": ("markPriceKlines", "5m"),
    "index_price_5m": ("indexPriceKlines", "5m"),
    "perpetual_5m": ("klines", "5m"),
    "funding": ("fundingRate", None),
}
MISSING_MONTHS = {
    "BTC": ("2026-06",),
    "ETH": ("2026-06",),
    "SOL": ("2021-01", "2026-06"),
}
BASE = "https://data.binance.vision/data/futures/um/daily"


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def session() -> requests.Session:
    client = requests.Session()
    client.headers.update(
        {
            "User-Agent": "fin-research-v238/1.0 (+https://github.com/balkhaev/fin)",
            "Accept": "application/zip,text/plain,*/*",
        }
    )
    return client


def get(client: requests.Session, url: str) -> requests.Response:
    for attempt in range(5):
        try:
            response = client.get(url, timeout=60)
            if response.status_code in {429, 500, 502, 503, 504}:
                time.sleep(1.0 + attempt)
                continue
            return response
        except requests.RequestException:
            if attempt == 4:
                raise
            time.sleep(1.0 + attempt)
    raise RuntimeError(url)


def daily_url(dataset: str, symbol: str, day: str) -> str:
    path_type, interval = DATASETS[dataset]
    if dataset == "funding":
        filename = f"{symbol}-fundingRate-{day}.zip"
        return f"{BASE}/{path_type}/{symbol}/{filename}"
    filename = f"{symbol}-{interval}-{day}.zip"
    return f"{BASE}/{path_type}/{symbol}/{interval}/{filename}"


def parse_zip(payload: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise ValueError(names)
        raw = archive.read(names[0])
    frame = pd.read_csv(io.BytesIO(raw), header=None, low_memory=False)
    if frame.empty:
        return {"rows": 0, "width": 0, "first": []}
    first = frame.iloc[0].astype(str).tolist()
    textual_header = not first[0].replace(".", "", 1).isdigit()
    data = frame.iloc[1:].copy() if textual_header else frame
    return {
        "member": names[0],
        "rows": int(len(data)),
        "width": int(frame.shape[1]),
        "textual_header": textual_header,
        "first": first[:12],
        "csv_sha256": sha256(raw),
    }


def probe_day(asset: str, dataset: str, month: str, day: str) -> dict[str, Any]:
    symbol = SYMBOLS[asset]
    url = daily_url(dataset, symbol, day)
    row: dict[str, Any] = {
        "asset": asset,
        "symbol": symbol,
        "dataset": dataset,
        "month": month,
        "day": day,
        "url": url,
    }
    client = session()
    try:
        checksum = get(client, url + ".CHECKSUM")
        archive = get(client, url)
        row["checksum_status"] = checksum.status_code
        row["http_status"] = archive.status_code
        row["bytes"] = len(archive.content)
        if checksum.status_code == 200 and archive.status_code == 200:
            expected = checksum.text.strip().split()[0].lower()
            actual = sha256(archive.content)
            row["archive_sha256"] = actual
            row["checksum_valid"] = expected == actual
            row["parsed"] = parse_zip(archive.content)
            row["valid"] = bool(row["checksum_valid"] and row["parsed"]["rows"] > 0)
        else:
            row["valid"] = False
    except Exception as exc:  # noqa: BLE001
        row["valid"] = False
        row["error"] = f"{type(exc).__name__}: {exc}"
    return row


def self_test() -> None:
    assert daily_url("premium_index_5m", "BTCUSDT", "2026-06-01").endswith(
        "/premiumIndexKlines/BTCUSDT/5m/BTCUSDT-5m-2026-06-01.zip"
    )
    assert daily_url("funding", "BTCUSDT", "2026-06-01").endswith(
        "/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2026-06-01.zip"
    )
    print("V238 daily archive reproof self-test passed")


def run(root: Path) -> int:
    results = root / "daily_reproof_results"
    results.mkdir(parents=True, exist_ok=True)
    tasks: list[tuple[str, str, str, str]] = []
    for asset, months in MISSING_MONTHS.items():
        for month in months:
            period = pd.Period(month, freq="M")
            days = [
                value.strftime("%Y-%m-%d")
                for value in pd.date_range(period.start_time, period.end_time, freq="1D")
            ]
            for dataset in DATASETS:
                for day in days:
                    tasks.append((asset, dataset, month, day))
    raw_rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {
            executor.submit(probe_day, asset, dataset, month, day): (
                asset,
                dataset,
                month,
                day,
            )
            for asset, dataset, month, day in tasks
        }
        for number, future in enumerate(as_completed(futures), start=1):
            raw_rows.append(future.result())
            if number % 100 == 0:
                print(f"probed {number}/{len(tasks)} daily archives", flush=True)
    raw_rows.sort(key=lambda row: (row["asset"], row["dataset"], row["month"], row["day"]))
    (results / "raw_daily_probe.json").write_text(json.dumps(raw_rows, indent=2) + "\n")

    details: list[dict[str, Any]] = []
    passed = True
    for asset, months in MISSING_MONTHS.items():
        for dataset in DATASETS:
            for month in months:
                rows = [
                    row
                    for row in raw_rows
                    if row["asset"] == asset
                    and row["dataset"] == dataset
                    and row["month"] == month
                ]
                valid = [row for row in rows if row.get("valid")]
                widths = sorted(
                    {int(row["parsed"]["width"]) for row in valid if row.get("parsed")}
                )
                availability = len(valid) / len(rows) if rows else 0.0
                gate = availability >= 0.95 and len(widths) == 1
                passed = passed and gate
                details.append(
                    {
                        "asset": asset,
                        "dataset": dataset,
                        "month": month,
                        "attempted_days": len(rows),
                        "valid_days": len(valid),
                        "availability": availability,
                        "schema_widths": widths,
                        "gate_passed": gate,
                    }
                )
    gate = {
        "candidate": "V238_DAILY_PREMIUM_SETTLEMENT_REPROOF",
        "reconstructed_months": MISSING_MONTHS,
        "minimum_daily_availability": 0.95,
        "details": details,
        "passed": bool(passed),
    }
    summary = {
        "candidate": "ACTIVE_V237_FUNDING_SETTLEMENT_PREMIUM_COMPRESSION",
        "status": "daily_reproof_passed_needs_full_research" if passed else "daily_reproof_failed",
        "daily_reproof_gate": gate,
        "selection_run": False,
        "full_backtest_run": False,
        "integration_permitted": False,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    write = lambda path, value: path.write_text(json.dumps(value, indent=2) + "\n")
    write(results / "coverage_gate.json", gate)
    write(results / "summary.json", summary)
    pd.DataFrame(details).to_csv(results / "coverage.csv", index=False)
    (results / "REPORT_RU.md").write_text(
        "# Active V238 — daily archive reproof\n\n"
        f"Status: `{summary['status']}`. No P&L or selection was run.\n"
    )
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
