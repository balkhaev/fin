#!/usr/bin/env python3
from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "cboe_probe_results"
SUMMARY = ROOT / "summary.json"
AGGREGATE = ROOT / "raw" / "aggregate_volume_open_interest.csv"


def parse_date(value: str) -> str | None:
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def main() -> None:
    value = json.loads(SUMMARY.read_text())
    lines = AGGREGATE.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("Date,"))
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    rows = [row for row in reader if row.get("Date")]
    columns = [str(column).strip() for column in (reader.fieldnames or [])]
    dates = [parsed for row in rows if (parsed := parse_date(str(row.get("Date", ""))))]
    numeric_coverage = {}
    for column in columns:
        if "Volume" not in column and "OI" not in column and "Open Interest" not in column:
            continue
        count = 0
        for row in rows:
            raw = str(row.get(column, "")).replace(",", "").strip()
            try:
                float(raw)
                count += 1
            except ValueError:
                pass
        numeric_coverage[column] = count
    source = value["sources"]["aggregate_volume_open_interest"]
    source["parsed"] = {
        "header_index": start,
        "columns": columns,
        "row_count": len(rows),
        "date_min": min(dates) if dates else None,
        "date_max": max(dates) if dates else None,
        "numeric_coverage": numeric_coverage,
        "sample_rows": rows[:3],
        "text_prefix": "\n".join(lines[: min(len(lines), start + 2)])[:500],
    }
    source["valid"] = bool(
        rows
        and dates
        and any("Volume" in column for column in columns)
        and any(("OI" in column or "Open Interest" in column) for column in columns)
    )
    valid_sources = [name for name, item in value["sources"].items() if item.get("valid")]
    value["valid_sources"] = valid_sources
    confirmed = set(value["required_sources"]).issubset(valid_sources)
    value["status"] = (
        "official_cboe_dated_contract_access_confirmed"
        if confirmed
        else "official_cboe_dated_contract_access_blocked"
    )
    value["full_v149_v154_permitted"] = confirmed
    SUMMARY.write_text(json.dumps(value, indent=2) + "\n")

    report = [
        "# V148 Cboe dated VIX futures access probe",
        "",
        f"Status: `{value['status']}`",
        "",
        "| Dataset | Valid | Rows | Dates | SHA-256 |",
        "|---|---:|---:|---|---|",
    ]
    for name, item in value["sources"].items():
        parsed = item.get("parsed") or {}
        http = item.get("http") or {}
        report.append(
            f"| {name} | {'yes' if item.get('valid') else 'no'} | "
            f"{parsed.get('row_count', 0)} | "
            f"{parsed.get('date_min') or '—'} … {parsed.get('date_max') or '—'} | "
            f"{http.get('sha256') or '—'} |"
        )
    report += [
        "",
        "The probe does not select or test a trading strategy.",
        "Raw official Cboe files and their hashes are stored alongside this report.",
    ]
    (ROOT / "REPORT_RU.md").write_text("\n".join(report) + "\n")
    print(json.dumps(value, indent=2))


if __name__ == "__main__":
    main()
