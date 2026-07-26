#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests

ROOT_URL = "https://data.binance.vision/data/futures/cm/daily"
SYMBOLS = ("BTCUSD_PERP", "ETHUSD_PERP")
SAMPLE_DATES = tuple(
    f"{year}-{month:02d}-15"
    for year in range(2021, 2026)
    for month in (1, 4, 7, 10)
) + ("2026-01-15", "2026-04-15")


@dataclass(frozen=True, slots=True)
class Dataset:
    name: str
    path_type: str
    interval: str | None = None

    def url(self, symbol: str, date: str) -> str:
        if self.interval:
            filename = f"{symbol}-{self.interval}-{date}.zip"
            return (
                f"{ROOT_URL}/{self.path_type}/{symbol}/{self.interval}/{filename}"
            )
        filename = f"{symbol}-{self.path_type}-{date}.zip"
        return f"{ROOT_URL}/{self.path_type}/{symbol}/{filename}"


DATASETS = (
    Dataset("liquidations", "liquidationSnapshot"),
    Dataset("metrics", "metrics"),
    Dataset("mark_price_1m", "markPriceKlines", "1m"),
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def session() -> requests.Session:
    client = requests.Session()
    client.headers.update(
        {
            "User-Agent": "fin-research-v181/1.0 (+https://github.com/balkhaev/fin)",
            "Accept": "application/zip,text/plain,*/*",
        }
    )
    return client


def request(client: requests.Session, url: str) -> requests.Response:
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
    raise RuntimeError(f"request failed: {url}")


def parse_archive(payload: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise ValueError(f"expected exactly one CSV member, got {names}")
        raw = archive.read(names[0])
    text = raw.decode("utf-8-sig", errors="replace")
    rows = [row for row in csv.reader(io.StringIO(text)) if row]
    if not rows:
        return {
            "member": names[0],
            "rows": 0,
            "columns": [],
            "has_header": False,
            "first_rows": [],
            "last_rows": [],
            "csv_sha256": sha256_bytes(raw),
        }

    first_value = rows[0][0].strip() if rows[0] else ""
    try:
        float(first_value)
        has_header = False
    except ValueError:
        has_header = True

    columns = rows[0] if has_header else [f"column_{i}" for i in range(len(rows[0]))]
    data_rows = rows[1:] if has_header else rows
    widths = sorted({len(row) for row in data_rows})
    return {
        "member": names[0],
        "rows": len(data_rows),
        "columns": columns,
        "column_widths": widths,
        "has_header": has_header,
        "first_rows": data_rows[:3],
        "last_rows": data_rows[-3:] if data_rows else [],
        "csv_sha256": sha256_bytes(raw),
    }


def probe_one(
    client: requests.Session,
    dataset: Dataset,
    symbol: str,
    date: str,
) -> dict[str, Any]:
    url = dataset.url(symbol, date)
    result: dict[str, Any] = {
        "dataset": dataset.name,
        "path_type": dataset.path_type,
        "interval": dataset.interval,
        "symbol": symbol,
        "date": date,
        "url": url,
        "valid": False,
    }
    try:
        checksum_response = request(client, url + ".CHECKSUM")
        archive_response = request(client, url)
        result["checksum_status"] = checksum_response.status_code
        result["http_status"] = archive_response.status_code
        result["bytes"] = len(archive_response.content)
        if checksum_response.status_code != 200 or archive_response.status_code != 200:
            result["reason"] = "missing_archive_or_checksum"
            return result
        expected = checksum_response.text.strip().split()[0].lower()
        actual = sha256_bytes(archive_response.content)
        result["expected_sha256"] = expected
        result["archive_sha256"] = actual
        result["checksum_valid"] = expected == actual
        if not result["checksum_valid"]:
            result["reason"] = "checksum_mismatch"
            return result
        parsed = parse_archive(archive_response.content)
        result["parsed"] = parsed
        result["valid"] = bool(parsed["columns"] and parsed["column_widths"])
        result["reason"] = "ok" if result["valid"] else "empty_or_invalid_csv"
        return result
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"{type(exc).__name__}: {exc}"
        return result


def coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    passed = True
    for symbol in SYMBOLS:
        for dataset in DATASETS:
            values = [
                row
                for row in rows
                if row["symbol"] == symbol and row["dataset"] == dataset.name
            ]
            valid = [row for row in values if row.get("valid")]
            availability = len(valid) / len(values) if values else 0.0
            row_counts = [int(row["parsed"]["rows"]) for row in valid]
            schemas = sorted(
                {
                    json.dumps(row["parsed"]["columns"], ensure_ascii=False)
                    for row in valid
                }
            )
            if dataset.name in {"metrics", "mark_price_1m"}:
                gate = availability >= 0.90 and len(schemas) == 1
            else:
                # A liquidation archive can be absent on quiet dates. Require a
                # broad, parseable sample rather than silently treating every 404 as zero.
                gate = availability >= 0.50 and len(valid) >= 8 and len(schemas) == 1
            passed = passed and gate
            details.append(
                {
                    "symbol": symbol,
                    "dataset": dataset.name,
                    "attempted": len(values),
                    "valid": len(valid),
                    "availability": availability,
                    "row_count_min": min(row_counts) if row_counts else 0,
                    "row_count_median": sorted(row_counts)[len(row_counts) // 2]
                    if row_counts
                    else 0,
                    "row_count_max": max(row_counts) if row_counts else 0,
                    "schema_count": len(schemas),
                    "gate_passed": gate,
                }
            )
    return {
        "candidate": "V181_DATA_SCHEMA_COVERAGE",
        "sample_date_rule": "15th day of each calendar quarter, 2021 through 2025, plus 2026-01-15 and 2026-04-15",
        "sample_dates": list(SAMPLE_DATES),
        "details": details,
        "passed": passed,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = [
        "symbol",
        "dataset",
        "date",
        "valid",
        "http_status",
        "checksum_status",
        "checksum_valid",
        "bytes",
        "row_count",
        "reason",
        "archive_sha256",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "symbol": row.get("symbol"),
                    "dataset": row.get("dataset"),
                    "date": row.get("date"),
                    "valid": row.get("valid"),
                    "http_status": row.get("http_status"),
                    "checksum_status": row.get("checksum_status"),
                    "checksum_valid": row.get("checksum_valid"),
                    "bytes": row.get("bytes"),
                    "row_count": (row.get("parsed") or {}).get("rows"),
                    "reason": row.get("reason"),
                    "archive_sha256": row.get("archive_sha256"),
                }
            )


def write_manifest(root: Path) -> None:
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "MANIFEST.json" or "__pycache__" in path.parts:
            continue
        rel = str(path.relative_to(root))
        files[rel] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    (root / "MANIFEST.json").write_text(
        json.dumps(
            {
                "candidate": "ACTIVE_V181_COINM_LIQUIDATION_FLOW",
                "files": files,
            },
            indent=2,
        )
        + "\n"
    )


def self_test() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("sample.csv", "time,side,qty\n1,SELL,2\n2,BUY,3\n")
    parsed = parse_archive(buffer.getvalue())
    assert parsed["rows"] == 2
    assert parsed["columns"] == ["time", "side", "qty"]
    assert parsed["column_widths"] == [3]
    assert len(SAMPLE_DATES) == 22
    print("V181 probe self-test passed")


def run(root: Path) -> int:
    results = root / "probe_results"
    results.mkdir(parents=True, exist_ok=True)
    client = session()
    rows: list[dict[str, Any]] = []
    total = len(SYMBOLS) * len(DATASETS) * len(SAMPLE_DATES)
    number = 0
    for symbol in SYMBOLS:
        for dataset in DATASETS:
            for date in SAMPLE_DATES:
                number += 1
                print(f"probe {number}/{total}: {symbol} {dataset.name} {date}", flush=True)
                rows.append(probe_one(client, dataset, symbol, date))
                time.sleep(0.03)

    gate = coverage(rows)
    design = root / "V181_V188_DESIGN.json"
    summary = {
        "candidate": "ACTIVE_V181_COINM_LIQUIDATION_FLOW",
        "status": "coverage_passed_needs_full_panel"
        if gate["passed"]
        else "data_access_insufficient",
        "coverage_gate": gate,
        "full_backtest_run": False,
        "selection_run": False,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
        "design_sha256": sha256_file(design),
        "limitations": [
            "The probe samples deterministic calendar dates; it does not establish continuous daily coverage.",
            "Liquidation snapshots are exchange observations, not guaranteed complete liquidation-engine internals.",
            "No strategy return or threshold selection is computed in V181.",
        ],
    }
    (results / "raw_probe.json").write_text(json.dumps(rows, indent=2) + "\n")
    (results / "coverage_gate.json").write_text(json.dumps(gate, indent=2) + "\n")
    (results / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    write_csv(results / "coverage.csv", rows)

    report = [
        "# V181 COIN-M liquidation archive probe",
        "",
        f"Status: `{summary['status']}`.",
        "",
        "| Symbol | Dataset | Valid / attempted | Availability | Schema | Gate |",
        "|---|---|---:|---:|---:|---|",
    ]
    for item in gate["details"]:
        report.append(
            f"| {item['symbol']} | {item['dataset']} | "
            f"{item['valid']} / {item['attempted']} | {item['availability']:.1%} | "
            f"{item['schema_count']} | {'PASS' if item['gate_passed'] else 'FAIL'} |"
        )
    report += [
        "",
        "This is a data/schema gate only. No P&L was calculated and no candidate was promoted.",
        "",
        "`live_ready=false`; `real_leverage_authorized=false`; `profitability_proven=false`.",
    ]
    (results / "REPORT_RU.md").write_text("\n".join(report) + "\n")
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
