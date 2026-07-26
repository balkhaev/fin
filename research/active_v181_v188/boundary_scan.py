#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests

SYMBOLS = ("BTCUSD_PERP", "ETHUSD_PERP")
START = date(2020, 1, 1)
END = date(2026, 6, 30)
MAX_WORKERS = 24
BASE = "https://data.binance.vision/data/futures/cm/daily/liquidationSnapshot"
THREAD_LOCAL = threading.local()

PERIODS = {
    "development": (date(2021, 1, 1), date(2023, 12, 31)),
    "validation": (date(2024, 1, 1), date(2024, 12, 31)),
    "holdout": (date(2025, 1, 1), date(2025, 12, 31)),
    "final": (date(2026, 1, 1), date(2026, 6, 30)),
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dates(start: date, end: date) -> list[date]:
    output: list[date] = []
    current = start
    while current <= end:
        output.append(current)
        current += timedelta(days=1)
    return output


def client() -> requests.Session:
    value = getattr(THREAD_LOCAL, "client", None)
    if value is None:
        value = requests.Session()
        value.headers.update(
            {
                "User-Agent": "fin-research-v182/1.0 (+https://github.com/balkhaev/fin)",
                "Accept": "text/plain,*/*",
            }
        )
        THREAD_LOCAL.client = value
    return value


def checksum_url(symbol: str, day: date) -> str:
    stamp = day.isoformat()
    filename = f"{symbol}-liquidationSnapshot-{stamp}.zip.CHECKSUM"
    return f"{BASE}/{symbol}/{filename}"


def probe(symbol: str, day: date) -> dict[str, Any]:
    url = checksum_url(symbol, day)
    last: Exception | None = None
    for attempt in range(4):
        try:
            response = client().get(url, timeout=30)
            if response.status_code in {429, 500, 502, 503, 504}:
                time.sleep(0.25 * (attempt + 1))
                continue
            checksum = None
            if response.status_code == 200:
                parts = response.text.strip().split()
                checksum = parts[0].lower() if parts else None
            return {
                "symbol": symbol,
                "date": day.isoformat(),
                "status": response.status_code,
                "available": bool(response.status_code == 200 and checksum),
                "checksum": checksum,
            }
        except requests.RequestException as exc:
            last = exc
            time.sleep(0.25 * (attempt + 1))
    return {
        "symbol": symbol,
        "date": day.isoformat(),
        "status": None,
        "available": False,
        "checksum": None,
        "error": f"{type(last).__name__}: {last}" if last else "request_failed",
    }


def month_key(day: str) -> str:
    return day[:7]


def month_range(start: date, end: date) -> list[str]:
    output: list[str] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        output.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            month = 1
            year += 1
    return output


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    symbols: dict[str, Any] = {}
    gate_passed = True
    for symbol in SYMBOLS:
        values = [row for row in rows if row["symbol"] == symbol]
        available = [row for row in values if row["available"]]
        valid_dates = sorted(row["date"] for row in available)
        counts: dict[str, int] = {}
        for row in available:
            counts[month_key(row["date"])] = counts.get(month_key(row["date"]), 0) + 1

        period_rows: dict[str, Any] = {}
        for name, (start, end) in PERIODS.items():
            months = month_range(start, end)
            months_with_data = [month for month in months if counts.get(month, 0) > 0]
            ratio = len(months_with_data) / len(months) if months else 0.0
            period_rows[name] = {
                "months": len(months),
                "months_with_archive": len(months_with_data),
                "month_coverage": ratio,
                "archive_days": sum(counts.get(month, 0) for month in months),
            }

        last = date.fromisoformat(valid_dates[-1]) if valid_dates else None
        live_through_final = bool(last is not None and last >= date(2026, 6, 20))
        holdout_months = period_rows["holdout"]["month_coverage"]
        final_months = period_rows["final"]["month_coverage"]
        symbol_gate = bool(
            live_through_final
            and holdout_months >= 0.80
            and final_months >= 0.80
        )
        gate_passed = gate_passed and symbol_gate
        symbols[symbol] = {
            "attempted_days": len(values),
            "archive_days": len(available),
            "first_archive_date": valid_dates[0] if valid_dates else None,
            "last_archive_date": valid_dates[-1] if valid_dates else None,
            "monthly_counts": counts,
            "periods": period_rows,
            "live_through_final": live_through_final,
            "gate_passed": symbol_gate,
            "request_errors": sum(1 for row in values if row.get("error")),
        }

    last_dates = [
        date.fromisoformat(value["last_archive_date"])
        for value in symbols.values()
        if value["last_archive_date"]
    ]
    last_date_gap = (
        (max(last_dates) - min(last_dates)).days if len(last_dates) == len(SYMBOLS) else None
    )
    aligned_boundary = bool(last_date_gap is not None and last_date_gap <= 7)
    gate_passed = gate_passed and aligned_boundary
    return {
        "candidate": "V182_LIQUIDATION_ARCHIVE_BOUNDARY",
        "scan_start": START.isoformat(),
        "scan_end": END.isoformat(),
        "symbols": symbols,
        "cross_symbol_last_date_gap_days": last_date_gap,
        "aligned_last_boundary": aligned_boundary,
        "continuous_research_permitted": gate_passed,
        "interpretation_rule": (
            "Missing daily archives may be treated as zero-event days only if both symbols "
            "remain published through the final window and at least 80% of holdout/final months "
            "contain one or more archives."
        ),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["symbol", "date", "status", "available", "checksum", "error"],
        )
        writer.writeheader()
        for row in sorted(rows, key=lambda value: (value["symbol"], value["date"])):
            writer.writerow({key: row.get(key) for key in writer.fieldnames})


def write_manifest(root: Path) -> None:
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "MANIFEST.json" or "__pycache__" in path.parts:
            continue
        rel = str(path.relative_to(root))
        files[rel] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    (root / "MANIFEST.json").write_text(
        json.dumps(
            {"candidate": "ACTIVE_V181_COINM_LIQUIDATION_FLOW", "files": files},
            indent=2,
        )
        + "\n"
    )


def self_test() -> None:
    assert len(dates(date(2024, 2, 28), date(2024, 3, 1))) == 3
    assert month_range(date(2025, 11, 1), date(2026, 2, 1)) == [
        "2025-11",
        "2025-12",
        "2026-01",
        "2026-02",
    ]
    print("V182 boundary scan self-test passed")


def run(root: Path) -> int:
    results = root / "boundary_results"
    results.mkdir(parents=True, exist_ok=True)
    jobs = [(symbol, day) for symbol in SYMBOLS for day in dates(START, END)]
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(probe, symbol, day): (symbol, day) for symbol, day in jobs}
        for number, future in enumerate(as_completed(futures), start=1):
            rows.append(future.result())
            if number % 250 == 0 or number == len(jobs):
                print(f"scanned {number}/{len(jobs)}", flush=True)

    summary = summarize(rows)
    decision = {
        "candidate": "ACTIVE_V181_COINM_LIQUIDATION_FLOW",
        "status": (
            "coverage_passed_needs_full_panel"
            if summary["continuous_research_permitted"]
            else "archive_discontinued_or_ambiguous"
        ),
        "continuous_research_permitted": summary["continuous_research_permitted"],
        "selection_run": False,
        "full_backtest_run": False,
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    (results / "boundary_scan.json").write_text(json.dumps(rows, indent=2) + "\n")
    (results / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (results / "FROZEN_DECISION.json").write_text(json.dumps(decision, indent=2) + "\n")
    write_csv(results / "daily_availability.csv", rows)

    report = [
        "# V182 liquidation archive boundary scan",
        "",
        f"Status: `{decision['status']}`.",
        "",
        "| Symbol | First archive | Last archive | Holdout month coverage | Final month coverage | Gate |",
        "|---|---|---|---:|---:|---|",
    ]
    for symbol, value in summary["symbols"].items():
        report.append(
            f"| {symbol} | {value['first_archive_date'] or '—'} | "
            f"{value['last_archive_date'] or '—'} | "
            f"{value['periods']['holdout']['month_coverage']:.1%} | "
            f"{value['periods']['final']['month_coverage']:.1%} | "
            f"{'PASS' if value['gate_passed'] else 'FAIL'} |"
        )
    report += [
        "",
        "No strategy P&L or policy selection was calculated.",
        "",
        "`live_ready=false`; `real_leverage_authorized=false`; `profitability_proven=false`.",
    ]
    (results / "REPORT_RU.md").write_text("\n".join(report) + "\n")
    write_manifest(root)
    print(json.dumps({"decision": decision, "summary": summary}, indent=2))
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
