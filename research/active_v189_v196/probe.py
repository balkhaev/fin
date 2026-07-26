#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import time
import zipfile
from pathlib import Path
from typing import Any

import requests

UNDERLYINGS = ("BTCUSDT", "ETHUSDT")
SAMPLE_DATES = (
    "2023-07-15",
    "2023-10-15",
    "2024-01-15",
    "2024-04-15",
    "2024-07-15",
    "2024-10-15",
    "2025-01-15",
    "2025-04-15",
    "2025-07-15",
    "2025-10-15",
    "2026-01-15",
    "2026-04-15",
)
BASE = "https://data.binance.vision/data/option/daily/EOHSummary"
INSTRUMENT_PATTERN = re.compile(
    r"^(?P<asset>[A-Z0-9]+)-(?P<expiry>[0-9]{6})-"
    r"(?P<strike>[0-9]+(?:\.[0-9]+)?)-(?P<call_put>[CP])$"
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def url(underlying: str, day: str) -> str:
    filename = f"{underlying}-EOHSummary-{day}.zip"
    return f"{BASE}/{underlying}/{filename}"


def session() -> requests.Session:
    client = requests.Session()
    client.headers.update(
        {
            "User-Agent": "fin-research-v189/1.0 (+https://github.com/balkhaev/fin)",
            "Accept": "application/zip,text/plain,*/*",
        }
    )
    return client


def request(client: requests.Session, target: str) -> requests.Response:
    last: Exception | None = None
    for attempt in range(5):
        try:
            response = client.get(target, timeout=90)
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


def parse_archive(payload: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise ValueError(f"expected one CSV member, got {names}")
        raw = archive.read(names[0])
    text = raw.decode("utf-8-sig", errors="replace")
    rows = [row for row in csv.reader(io.StringIO(text)) if row]
    if not rows:
        return {
            "member": names[0],
            "rows": 0,
            "columns": [],
            "column_widths": [],
            "semantics": {},
            "instrument_parseable_ratio": 0.0,
            "first_rows": [],
            "last_rows": [],
            "csv_sha256": sha256_bytes(raw),
        }

    first = rows[0]
    first_value = first[0].strip() if first else ""
    # Option symbols are textual, so use common column-name tokens rather than
    # numeric detection to distinguish a real header from a data row.
    header_tokens = {
        "symbol",
        "instrument",
        "date",
        "time",
        "bid",
        "ask",
        "markprice",
        "strike",
        "expiry",
    }
    normalized_first = {normalize(value) for value in first}
    has_header = bool(normalized_first & header_tokens) or any(
        value.endswith("price") or value.endswith("volume")
        for value in normalized_first
    )
    columns = first if has_header else [f"column_{i}" for i in range(len(first))]
    data_rows = rows[1:] if has_header else rows
    widths = sorted({len(row) for row in data_rows})

    semantics = {
        "instrument": find_column(
            columns,
            ("symbol", "instrument", "instrument_name", "contract_symbol"),
        ),
        "bid": find_column(columns, ("bid_price", "best_bid", "bid")),
        "ask": find_column(columns, ("ask_price", "best_ask", "ask")),
        "mark": find_column(columns, ("mark_price", "mark", "close")),
        "underlying_price": find_column(
            columns,
            ("underlying_price", "index_price", "spot_price", "exercise_price"),
        ),
        "open_interest": find_column(columns, ("open_interest", "openinterest")),
        "volume": find_column(columns, ("volume", "trade_volume")),
        "delta": find_column(columns, ("delta",)),
        "iv": find_column(
            columns,
            ("mark_iv", "implied_volatility", "impliedvolatility", "bid_iv", "ask_iv"),
        ),
        "expiry": find_column(columns, ("expiry", "expiration", "expiry_date")),
        "strike": find_column(columns, ("strike", "strike_price")),
        "call_put": find_column(columns, ("call_put", "option_type", "type")),
        "timestamp": find_column(columns, ("date", "time", "timestamp", "create_time")),
        "settlement": find_column(
            columns,
            ("settlement_price", "settle_price", "exercise_price", "delivery_price"),
        ),
        "multiplier": find_column(
            columns,
            ("contract_multiplier", "multiplier", "contract_size"),
        ),
    }

    instrument_index = (
        columns.index(semantics["instrument"]) if semantics["instrument"] in columns else None
    )
    instruments: list[str] = []
    if instrument_index is not None:
        for row in data_rows[:1000]:
            if len(row) > instrument_index:
                instruments.append(str(row[instrument_index]).strip())
    parsed = sum(1 for value in instruments if INSTRUMENT_PATTERN.match(value))
    parseable_ratio = parsed / len(instruments) if instruments else 0.0

    executable = bool(
        semantics["instrument"]
        and semantics["bid"]
        and semantics["ask"]
        and semantics["underlying_price"]
        and (semantics["open_interest"] or semantics["volume"])
        and (parseable_ratio >= 0.95 or (
            semantics["expiry"] and semantics["strike"] and semantics["call_put"]
        ))
        and (semantics["delta"] or semantics["iv"])
    )

    return {
        "member": names[0],
        "rows": len(data_rows),
        "columns": columns,
        "column_widths": widths,
        "has_header": has_header,
        "semantics": semantics,
        "instrument_samples": instruments[:10],
        "instrument_parseable_ratio": parseable_ratio,
        "executable_quote_semantics": executable,
        "external_contract_spec_needed": semantics["multiplier"] is None,
        "explicit_settlement_missing": semantics["settlement"] is None,
        "first_rows": data_rows[:3],
        "last_rows": data_rows[-3:] if data_rows else [],
        "csv_sha256": sha256_bytes(raw),
    }


def probe_one(client: requests.Session, underlying: str, day: str) -> dict[str, Any]:
    target = url(underlying, day)
    result: dict[str, Any] = {
        "underlying": underlying,
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
        result["expected_sha256"] = expected
        result["archive_sha256"] = actual
        result["checksum_valid"] = expected == actual
        if expected != actual:
            result["reason"] = "checksum_mismatch"
            return result
        parsed = parse_archive(archive_response.content)
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
    for underlying in UNDERLYINGS:
        values = [row for row in rows if row["underlying"] == underlying]
        valid = [row for row in values if row.get("valid")]
        availability = len(valid) / len(values) if values else 0.0
        executable = [
            row
            for row in valid
            if row["parsed"].get("executable_quote_semantics")
        ]
        executable_ratio = len(executable) / len(valid) if valid else 0.0
        schemas = sorted(
            {
                json.dumps(row["parsed"]["columns"], ensure_ascii=False)
                for row in valid
            }
        )
        gate = bool(
            availability >= 0.90
            and executable_ratio >= 0.90
            and len(schemas) <= 2
        )
        passed = passed and gate
        details.append(
            {
                "underlying": underlying,
                "attempted": len(values),
                "valid": len(valid),
                "availability": availability,
                "executable_files": len(executable),
                "executable_ratio": executable_ratio,
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
        "candidate": "V189_EOH_EXECUTABILITY_GATE",
        "sample_dates": list(SAMPLE_DATES),
        "details": details,
        "passed": passed,
        "settlement_and_multiplier_rule": (
            "Even when quote semantics pass, V190 must add an official deterministic "
            "contract multiplier and expiry-settlement source before any P&L."
        ),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = [
        "underlying",
        "date",
        "valid",
        "http_status",
        "checksum_status",
        "checksum_valid",
        "bytes",
        "row_count",
        "executable_quote_semantics",
        "instrument_parseable_ratio",
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
                    "underlying": row.get("underlying"),
                    "date": row.get("date"),
                    "valid": row.get("valid"),
                    "http_status": row.get("http_status"),
                    "checksum_status": row.get("checksum_status"),
                    "checksum_valid": row.get("checksum_valid"),
                    "bytes": row.get("bytes"),
                    "row_count": parsed.get("rows"),
                    "executable_quote_semantics": parsed.get(
                        "executable_quote_semantics"
                    ),
                    "instrument_parseable_ratio": parsed.get(
                        "instrument_parseable_ratio"
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
            {"candidate": "ACTIVE_V189_DEFINED_RISK_OPTIONS_VRP", "files": files},
            indent=2,
        )
        + "\n"
    )


def self_test() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "sample.csv",
            "symbol,bidPrice,askPrice,underlyingPrice,volume,markIV\n"
            "BTC-240628-50000-C,100,110,60000,42,0.55\n",
        )
    parsed = parse_archive(buffer.getvalue())
    assert parsed["rows"] == 1
    assert parsed["instrument_parseable_ratio"] == 1.0
    assert parsed["executable_quote_semantics"] is True
    print("V189 options probe self-test passed")


def run(root: Path) -> int:
    results = root / "probe_results"
    results.mkdir(parents=True, exist_ok=True)
    client = session()
    rows: list[dict[str, Any]] = []
    total = len(UNDERLYINGS) * len(SAMPLE_DATES)
    for number, (underlying, day) in enumerate(
        ((underlying, day) for underlying in UNDERLYINGS for day in SAMPLE_DATES),
        start=1,
    ):
        print(f"probe {number}/{total}: {underlying} {day}", flush=True)
        rows.append(probe_one(client, underlying, day))
        time.sleep(0.05)

    gate = summarize(rows)
    decision = {
        "candidate": "ACTIVE_V189_DEFINED_RISK_OPTIONS_VRP",
        "status": (
            "eoh_quotes_pass_need_contract_and_settlement_spec"
            if gate["passed"]
            else "eoh_data_not_execution_grade"
        ),
        "executability_gate_passed": gate["passed"],
        "selection_run": False,
        "full_backtest_run": False,
        "integration_permitted": False,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
        "design_sha256": sha256_file(root / "V189_V196_DESIGN.json"),
    }
    (results / "raw_probe.json").write_text(json.dumps(rows, indent=2) + "\n")
    (results / "executability_gate.json").write_text(json.dumps(gate, indent=2) + "\n")
    (results / "FROZEN_DECISION.json").write_text(json.dumps(decision, indent=2) + "\n")
    write_csv(results / "coverage.csv", rows)

    report = [
        "# V189 Binance Options EOH executability probe",
        "",
        f"Status: `{decision['status']}`.",
        "",
        "| Underlying | Valid / attempted | Availability | Executable semantics | Schemas | Gate |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for item in gate["details"]:
        report.append(
            f"| {item['underlying']} | {item['valid']} / {item['attempted']} | "
            f"{item['availability']:.1%} | {item['executable_ratio']:.1%} | "
            f"{item['schema_count']} | {'PASS' if item['gate_passed'] else 'FAIL'} |"
        )
    report += [
        "",
        "No option P&L, volatility threshold selection or V75 integration was calculated.",
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
