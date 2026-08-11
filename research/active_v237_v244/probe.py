#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import requests

ASSETS = ("BTC", "ETH", "SOL")
SYMBOLS = {asset: f"{asset}USDT" for asset in ASSETS}
SAMPLE_MONTHS = (
    "2021-01",
    "2021-10",
    "2022-07",
    "2023-10",
    "2024-07",
    "2025-07",
    "2026-04",
    "2026-06",
)
DATASETS = {
    "premium_index_5m": ("premiumIndexKlines", "5m"),
    "mark_price_5m": ("markPriceKlines", "5m"),
    "index_price_5m": ("indexPriceKlines", "5m"),
    "perpetual_5m": ("klines", "5m"),
    "funding": ("fundingRate", None),
}
BASE = "https://data.binance.vision/data/futures/um/monthly"


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def session() -> requests.Session:
    client = requests.Session()
    client.headers.update(
        {
            "User-Agent": "fin-research-v237/1.0 (+https://github.com/balkhaev/fin)",
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


def url_for(dataset: str, symbol: str, month: str) -> str:
    path_type, interval = DATASETS[dataset]
    if dataset == "funding":
        filename = f"{symbol}-fundingRate-{month}.zip"
        return f"{BASE}/{path_type}/{symbol}/{filename}"
    filename = f"{symbol}-{interval}-{month}.zip"
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


def self_test() -> None:
    assert url_for("premium_index_5m", "BTCUSDT", "2025-07").endswith(
        "/premiumIndexKlines/BTCUSDT/5m/BTCUSDT-5m-2025-07.zip"
    )
    assert url_for("funding", "BTCUSDT", "2025-07").endswith(
        "/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2025-07.zip"
    )
    print("V237 probe self-test passed")


def run(root: Path) -> int:
    results = root / "probe_results"
    results.mkdir(parents=True, exist_ok=True)
    client = session()
    raw_rows: list[dict[str, Any]] = []
    for asset in ASSETS:
        symbol = SYMBOLS[asset]
        for dataset in DATASETS:
            for month in SAMPLE_MONTHS:
                url = url_for(dataset, symbol, month)
                row: dict[str, Any] = {
                    "asset": asset,
                    "symbol": symbol,
                    "dataset": dataset,
                    "month": month,
                    "url": url,
                }
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
                        row["valid"] = bool(
                            row["checksum_valid"] and row["parsed"]["rows"] > 0
                        )
                    else:
                        row["valid"] = False
                except Exception as exc:  # noqa: BLE001
                    row["valid"] = False
                    row["error"] = f"{type(exc).__name__}: {exc}"
                raw_rows.append(row)
                time.sleep(0.025)

    details = []
    passed = True
    for asset in ASSETS:
        for dataset in DATASETS:
            rows = [
                row
                for row in raw_rows
                if row["asset"] == asset and row["dataset"] == dataset
            ]
            valid = [row for row in rows if row.get("valid")]
            widths = sorted(
                {int(row["parsed"]["width"]) for row in valid if row.get("parsed")}
            )
            latest = any(row["month"] == "2026-06" for row in valid)
            availability = len(valid) / len(rows) if rows else 0.0
            gate = availability >= 0.90 and len(widths) == 1 and latest
            passed = passed and gate
            details.append(
                {
                    "asset": asset,
                    "dataset": dataset,
                    "attempted": len(rows),
                    "valid": len(valid),
                    "availability": availability,
                    "schema_widths": widths,
                    "latest_2026_06_present": latest,
                    "gate_passed": gate,
                }
            )
    gate = {
        "candidate": "V237_PREMIUM_SETTLEMENT_DATA_GATE",
        "sample_months": list(SAMPLE_MONTHS),
        "details": details,
        "passed": bool(passed),
    }
    summary = {
        "candidate": "ACTIVE_V237_FUNDING_SETTLEMENT_PREMIUM_COMPRESSION",
        "status": "data_gate_passed_needs_full_research" if passed else "data_access_insufficient",
        "coverage_gate": gate,
        "selection_run": False,
        "full_backtest_run": False,
        "integration_permitted": False,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    (results / "raw_probe.json").write_text(json.dumps(raw_rows, indent=2) + "\n")
    (results / "coverage_gate.json").write_text(json.dumps(gate, indent=2) + "\n")
    (results / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    pd.DataFrame(details).to_csv(results / "coverage.csv", index=False)
    (results / "REPORT_RU.md").write_text(
        "# Active V237–V244 — funding-settlement premium compression\n\n"
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
