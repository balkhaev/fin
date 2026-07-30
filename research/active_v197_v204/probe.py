#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

SYMBOLS = ("BTCUSD_PERP", "ETHUSD_PERP")
SAMPLE_DATES = ("2023-01-15", "2024-07-15", "2025-07-15", "2026-04-15")
ROOT_URL = "https://data.binance.vision/data/futures/cm/daily"


@dataclass(frozen=True, slots=True)
class Dataset:
    name: str
    path_type: str
    interval: str | None = None

    def url(self, symbol: str, day: str) -> str:
        if self.interval:
            filename = f"{symbol}-{self.interval}-{day}.zip"
            return f"{ROOT_URL}/{self.path_type}/{symbol}/{self.interval}/{filename}"
        filename = f"{symbol}-{self.path_type}-{day}.zip"
        return f"{ROOT_URL}/{self.path_type}/{symbol}/{filename}"


DATASETS = (
    Dataset("book_depth", "bookDepth"),
    Dataset("book_ticker", "bookTicker"),
    Dataset("mark_price_1m", "markPriceKlines", "1m"),
    Dataset("metrics", "metrics"),
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())


def find_column(columns: list[str], aliases: tuple[str, ...]) -> str | None:
    mapping = {normalize(column): column for column in columns}
    for alias in aliases:
        key = normalize(alias)
        if key in mapping:
            return mapping[key]
    for key, original in mapping.items():
        if any(normalize(alias) in key for alias in aliases):
            return original
    return None


def session() -> requests.Session:
    client = requests.Session()
    client.headers.update(
        {
            "User-Agent": "fin-research-v197/1.0 (+https://github.com/balkhaev/fin)",
            "Accept": "application/zip,text/plain,*/*",
        }
    )
    return client


def request(client: requests.Session, target: str) -> requests.Response:
    last: Exception | None = None
    for attempt in range(5):
        try:
            response = client.get(target, timeout=120)
            if response.status_code in {429, 500, 502, 503, 504}:
                time.sleep(1.0 + attempt)
                continue
            return response
        except requests.RequestException as exc:
            last = exc
            time.sleep(1.0 + attempt)
    if last is not None:
        raise last
    raise RuntimeError(f"request failed: {target}")


def header_detect(first: list[str]) -> bool:
    known = {
        "timestamp",
        "transactiontime",
        "eventtime",
        "time",
        "symbol",
        "percentage",
        "depth",
        "notional",
        "bidprice",
        "askprice",
        "open",
        "createtime",
    }
    return bool({normalize(value) for value in first} & known)


def parse_timestamp(value: str) -> int | None:
    stripped = value.strip()
    try:
        number = int(float(stripped))
        if number > 10**15:
            number //= 1000
        if number < 10**11:
            number *= 1000
        return number
    except ValueError:
        try:
            import datetime as dt

            parsed = dt.datetime.fromisoformat(stripped.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return int(parsed.timestamp() * 1000)
        except ValueError:
            return None


def infer_semantics(columns: list[str], dataset: str) -> dict[str, str | None]:
    common = {
        "timestamp": find_column(
            columns,
            ("timestamp", "transaction_time", "event_time", "time", "create_time", "open_time"),
        ),
        "symbol": find_column(columns, ("symbol",)),
    }
    if dataset == "book_depth":
        return common | {
            "percentage": find_column(columns, ("percentage", "percent", "price_level")),
            "depth": find_column(columns, ("depth", "quantity", "qty")),
            "notional": find_column(columns, ("notional", "value")),
            "side": find_column(columns, ("side",)),
            "price": find_column(columns, ("price",)),
            "level": find_column(columns, ("level",)),
        }
    if dataset == "book_ticker":
        return common | {
            "bid_price": find_column(columns, ("best_bid_price", "bid_price", "bidprice")),
            "bid_qty": find_column(columns, ("best_bid_qty", "bid_qty", "bidqty")),
            "ask_price": find_column(columns, ("best_ask_price", "ask_price", "askprice")),
            "ask_qty": find_column(columns, ("best_ask_qty", "ask_qty", "askqty")),
        }
    if dataset == "mark_price_1m":
        return common | {
            "open": find_column(columns, ("open",)),
            "high": find_column(columns, ("high",)),
            "low": find_column(columns, ("low",)),
            "close": find_column(columns, ("close",)),
        }
    return common | {
        "open_interest": find_column(columns, ("sum_open_interest", "open_interest")),
        "open_interest_value": find_column(
            columns, ("sum_open_interest_value", "open_interest_value")
        ),
        "taker_ratio": find_column(
            columns, ("sum_taker_long_short_vol_ratio", "taker_long_short_vol_ratio")
        ),
    }


def parse_archive(payload: bytes, dataset: str) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise ValueError(f"expected one CSV member, got {names}")
        raw = archive.read(names[0])
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows: list[list[str]] = []
    first = next(reader, None)
    if first is None:
        return {
            "member": names[0],
            "rows": 0,
            "columns": [],
            "semantics": {},
            "csv_sha256": sha256_bytes(raw),
        }
    has_header = header_detect(first)
    columns = first if has_header else [f"column_{i}" for i in range(len(first))]
    if not has_header:
        rows.append(first)
    for row in reader:
        if row:
            rows.append(row)
    widths = sorted({len(row) for row in rows})
    semantics = infer_semantics(columns, dataset)

    timestamp_column = semantics.get("timestamp")
    timestamp_index = columns.index(timestamp_column) if timestamp_column in columns else None
    timestamps: list[int] = []
    if timestamp_index is not None:
        for row in rows:
            if len(row) > timestamp_index:
                parsed = parse_timestamp(row[timestamp_index])
                if parsed is not None:
                    timestamps.append(parsed)
    deltas = [later - earlier for earlier, later in zip(timestamps, timestamps[1:]) if later > earlier]
    native_median_ms = sorted(deltas)[len(deltas) // 2] if deltas else None

    executable = False
    if dataset == "book_depth":
        executable = bool(
            timestamp_column
            and (
                semantics.get("percentage")
                and semantics.get("depth")
                and semantics.get("notional")
                or semantics.get("side")
                and semantics.get("price")
                and semantics.get("depth")
            )
        )
    elif dataset == "book_ticker":
        executable = bool(
            timestamp_column
            and semantics.get("bid_price")
            and semantics.get("ask_price")
            and semantics.get("bid_qty")
            and semantics.get("ask_qty")
        )
    elif dataset == "mark_price_1m":
        executable = bool(timestamp_column and semantics.get("open") and semantics.get("close"))
    else:
        executable = bool(timestamp_column and semantics.get("open_interest"))

    return {
        "member": names[0],
        "rows": len(rows),
        "columns": columns,
        "column_widths": widths,
        "has_header": has_header,
        "semantics": semantics,
        "native_median_interval_ms": native_median_ms,
        "timestamp_min_ms": min(timestamps) if timestamps else None,
        "timestamp_max_ms": max(timestamps) if timestamps else None,
        "schema_executable": executable,
        "first_rows": rows[:3],
        "last_rows": rows[-3:] if rows else [],
        "csv_sha256": sha256_bytes(raw),
    }


def probe_one(
    client: requests.Session,
    dataset: Dataset,
    symbol: str,
    day: str,
) -> dict[str, Any]:
    target = dataset.url(symbol, day)
    result: dict[str, Any] = {
        "dataset": dataset.name,
        "path_type": dataset.path_type,
        "interval": dataset.interval,
        "symbol": symbol,
        "date": day,
        "url": target,
        "valid": False,
    }
    try:
        checksum_response = request(client, target + ".CHECKSUM")
        archive_response = request(client, target)
        result["checksum_status"] = checksum_response.status_code
        result["http_status"] = archive_response.status_code
        result["bytes"] = len(archive_response.content)
        if checksum_response.status_code != 200 or archive_response.status_code != 200:
            result["reason"] = "missing_archive_or_checksum"
            return result
        expected = checksum_response.text.strip().split()[0].lower()
        actual = sha256_bytes(archive_response.content)
        result["checksum_valid"] = expected == actual
        result["archive_sha256"] = actual
        if expected != actual:
            result["reason"] = "checksum_mismatch"
            return result
        parsed = parse_archive(archive_response.content, dataset.name)
        result["parsed"] = parsed
        result["valid"] = bool(parsed["rows"] > 0 and parsed["columns"])
        result["reason"] = "ok" if result["valid"] else "empty_or_invalid_csv"
        return result
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"{type(exc).__name__}: {exc}"
        return result


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
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
            executable = [
                row for row in valid if row["parsed"].get("schema_executable")
            ]
            schemas = sorted(
                {
                    json.dumps(row["parsed"]["columns"], ensure_ascii=False)
                    for row in valid
                }
            )
            gate = bool(
                availability >= 0.75
                and len(executable) == len(valid)
                and len(schemas) <= 2
            )
            passed = passed and gate
            details.append(
                {
                    "symbol": symbol,
                    "dataset": dataset.name,
                    "attempted": len(values),
                    "valid": len(valid),
                    "availability": availability,
                    "schema_executable_files": len(executable),
                    "schema_count": len(schemas),
                    "row_count_min": min(
                        [int(row["parsed"]["rows"]) for row in valid], default=0
                    ),
                    "row_count_max": max(
                        [int(row["parsed"]["rows"]) for row in valid], default=0
                    ),
                    "gate_passed": gate,
                }
            )
    return {
        "candidate": "V197_DEPTH_SCHEMA_COVERAGE",
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
        "schema_executable",
        "native_median_interval_ms",
        "reason",
        "archive_sha256",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            parsed = row.get("parsed") or {}
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
                    "row_count": parsed.get("rows"),
                    "schema_executable": parsed.get("schema_executable"),
                    "native_median_interval_ms": parsed.get(
                        "native_median_interval_ms"
                    ),
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
            {"candidate": "ACTIVE_V197_DEPTH_REPLENISHMENT", "files": files},
            indent=2,
        )
        + "\n"
    )


def self_test() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "sample.csv",
            "timestamp,percentage,depth,notional\n"
            "1704067200000,-1,100,1000\n"
            "1704067200000,1,120,1200\n",
        )
    parsed = parse_archive(buffer.getvalue(), "book_depth")
    assert parsed["rows"] == 2
    assert parsed["schema_executable"] is True
    assert parsed["semantics"]["percentage"] == "percentage"
    print("V197 depth probe self-test passed")


def run(root: Path) -> int:
    results = root / "probe_results"
    results.mkdir(parents=True, exist_ok=True)
    client = session()
    rows: list[dict[str, Any]] = []
    combinations = [
        (dataset, symbol, day)
        for symbol in SYMBOLS
        for dataset in DATASETS
        for day in SAMPLE_DATES
    ]
    for number, (dataset, symbol, day) in enumerate(combinations, start=1):
        print(f"probe {number}/{len(combinations)}: {symbol} {dataset.name} {day}", flush=True)
        rows.append(probe_one(client, dataset, symbol, day))
        time.sleep(0.04)

    gate = summarize(rows)
    decision = {
        "candidate": "ACTIVE_V197_DEPTH_REPLENISHMENT",
        "status": (
            "schema_coverage_passed_needs_normalized_panel"
            if gate["passed"]
            else "data_or_schema_insufficient"
        ),
        "schema_coverage_passed": gate["passed"],
        "selection_run": False,
        "full_backtest_run": False,
        "integration_permitted": False,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
        "design_sha256": sha256_file(root / "V197_V204_DESIGN.json"),
    }
    (results / "raw_probe.json").write_text(json.dumps(rows, indent=2) + "\n")
    (results / "schema_gate.json").write_text(json.dumps(gate, indent=2) + "\n")
    (results / "FROZEN_DECISION.json").write_text(json.dumps(decision, indent=2) + "\n")
    write_csv(results / "coverage.csv", rows)

    report = [
        "# V197 COIN-M actual depth schema probe",
        "",
        f"Status: `{decision['status']}`.",
        "",
        "| Symbol | Dataset | Valid / attempted | Availability | Executable schema | Gate |",
        "|---|---|---:|---:|---:|---|",
    ]
    for item in gate["details"]:
        report.append(
            f"| {item['symbol']} | {item['dataset']} | "
            f"{item['valid']} / {item['attempted']} | {item['availability']:.1%} | "
            f"{item['schema_executable_files']} / {item['valid']} | "
            f"{'PASS' if item['gate_passed'] else 'FAIL'} |"
        )
    report += [
        "",
        "No signal thresholds, returns or V75 integration were calculated.",
        "",
        "`live_ready=false`; `real_leverage_authorized=false`; `profitability_proven=false`.",
    ]
    (results / "REPORT_RU.md").write_text("\n".join(report) + "\n")
    write_manifest(root)
    print(json.dumps({"decision": decision, "gate": gate}, indent=2))
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
