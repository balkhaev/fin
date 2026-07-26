#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import requests

CBOE_BASE = "https://cdn.cboe.com"

SOURCES: dict[str, dict[str, Any]] = {
    "aggregate_volume_open_interest": {
        "kind": "aggregate",
        "url": CBOE_BASE + "/data/us/futures/market_statistics/historical_data/cfevoloi.csv",
    },
    "vix_spot_history": {
        "kind": "spot",
        "url": CBOE_BASE + "/api/global/us_indices/daily_prices/VIX_History.csv",
    },
    "vx_archive_dec_2012": {
        "kind": "contract",
        "url": CBOE_BASE + "/resources/futures/archive/volume-and-price/CFE_Z12_VX.csv",
        "expected_expiry": "2012-12-19",
    },
    "vx_monthly_jan_2023": {
        "kind": "contract",
        "url": CBOE_BASE + "/data/us/futures/market_statistics/historical_data/VX/VX_2023-01-18.csv",
        "expected_expiry": "2023-01-18",
    },
    "vx_monthly_aug_2026": {
        "kind": "contract",
        "url": CBOE_BASE + "/data/us/futures/market_statistics/historical_data/VX/VX_2026-08-19.csv",
        "expected_expiry": "2026-08-19",
    },
}

DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d")


@dataclass
class HttpEvidence:
    url: str
    status_code: int | None
    content_type: str | None
    bytes: int
    sha256: str | None
    error: str | None = None


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def session() -> requests.Session:
    value = requests.Session()
    value.headers.update(
        {
            "User-Agent": "fin-research-v148/1.0 (+https://github.com/balkhaev/fin)",
            "Accept": "text/csv,text/plain;q=0.9,*/*;q=0.5",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return value


def get(session_: requests.Session, url: str) -> requests.Response:
    last: Exception | None = None
    for attempt in range(3):
        try:
            response = session_.get(url, timeout=30)
            if response.status_code in {429, 500, 502, 503, 504}:
                time.sleep(1.0 + attempt)
                continue
            return response
        except requests.RequestException as exc:
            last = exc
            time.sleep(1.0 + attempt)
    if last:
        raise last
    raise RuntimeError(f"unable to fetch {url}")


def decode_csv(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def find_header(lines: list[str], kind: str) -> int:
    patterns = {
        "contract": ("Trade Date", "Futures", "Settle"),
        "spot": ("DATE", "CLOSE"),
        "aggregate": ("Trade Date",),
    }
    wanted = patterns[kind]
    for index, line in enumerate(lines[:80]):
        normalized = line.replace("\ufeff", "").strip().lower()
        if all(token.lower() in normalized for token in wanted):
            return index
    return 0


def normalize_header(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\ufeff", "").strip())


def parse_csv(content: bytes, kind: str) -> dict[str, Any]:
    text = decode_csv(content)
    lines = [line for line in text.splitlines() if line.strip()]
    header_index = find_header(lines, kind)
    body = "\n".join(lines[header_index:])
    reader = csv.DictReader(io.StringIO(body))
    rows = []
    for row in reader:
        cleaned = {
            normalize_header(str(key)): (str(value).strip() if value is not None else "")
            for key, value in row.items()
            if key is not None
        }
        if cleaned and any(value for value in cleaned.values()):
            rows.append(cleaned)
    columns = [normalize_header(value) for value in (reader.fieldnames or [])]

    date_candidates = [
        value
        for value in ("Trade Date", "DATE", "Date", "trade_date")
        if value in columns
    ]
    dates: list[str] = []
    if date_candidates:
        key = date_candidates[0]
        for row in rows:
            raw = row.get(key, "")
            parsed = None
            for fmt in DATE_FORMATS:
                try:
                    from datetime import datetime

                    parsed = datetime.strptime(raw, fmt).date().isoformat()
                    break
                except ValueError:
                    continue
            if parsed:
                dates.append(parsed)

    numeric_coverage: dict[str, int] = {}
    for candidate in (
        "Open",
        "High",
        "Low",
        "Close",
        "Settle",
        "Total Volume",
        "Open Interest",
        "OPEN",
        "HIGH",
        "LOW",
        "CLOSE",
    ):
        if candidate not in columns:
            continue
        count = 0
        for row in rows:
            raw = row.get(candidate, "").replace(",", "").replace("*", "").strip()
            try:
                float(raw)
                count += 1
            except ValueError:
                pass
        numeric_coverage[candidate] = count

    return {
        "header_index": header_index,
        "columns": columns,
        "row_count": len(rows),
        "date_min": min(dates) if dates else None,
        "date_max": max(dates) if dates else None,
        "numeric_coverage": numeric_coverage,
        "sample_rows": rows[:3],
        "text_prefix": text[:300],
    }


def valid_dataset(name: str, spec: dict[str, Any], parsed: dict[str, Any], status: int) -> bool:
    if status != 200 or parsed["row_count"] <= 0:
        return False
    columns = set(parsed["columns"])
    kind = spec["kind"]
    if kind == "contract":
        return {
            "Trade Date",
            "Futures",
            "Settle",
            "Total Volume",
            "Open Interest",
        }.issubset(columns)
    if kind == "spot":
        return {"DATE", "CLOSE"}.issubset(columns)
    if kind == "aggregate":
        return any("Volume" in column for column in columns) and any(
            "Open Interest" in column for column in columns
        )
    raise ValueError((name, kind))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/v148_cboe_probe"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    raw_dir = args.output / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    client = session()
    evidence: dict[str, Any] = {}
    passed: list[str] = []
    for name, spec in SOURCES.items():
        print(f"fetching {name}: {spec['url']}", flush=True)
        try:
            response = get(client, str(spec["url"]))
            http = HttpEvidence(
                url=response.url,
                status_code=response.status_code,
                content_type=response.headers.get("content-type"),
                bytes=len(response.content),
                sha256=sha256_bytes(response.content),
            )
            raw_path = raw_dir / f"{name}.csv"
            raw_path.write_bytes(response.content)
            parsed = parse_csv(response.content, str(spec["kind"]))
            ok = valid_dataset(name, spec, parsed, response.status_code)
            if ok:
                passed.append(name)
            evidence[name] = {
                "spec": spec,
                "http": asdict(http),
                "parsed": parsed,
                "valid": ok,
            }
        except Exception as exc:  # noqa: BLE001
            evidence[name] = {
                "spec": spec,
                "http": asdict(HttpEvidence(str(spec["url"]), None, None, 0, None, repr(exc))),
                "parsed": None,
                "valid": False,
            }

    required = {
        "aggregate_volume_open_interest",
        "vix_spot_history",
        "vx_archive_dec_2012",
        "vx_monthly_jan_2023",
    }
    confirmed = required.issubset(set(passed))
    status = (
        "official_cboe_dated_contract_access_confirmed"
        if confirmed
        else "official_cboe_dated_contract_access_blocked"
    )
    summary = {
        "candidate": "V148_CBOE_DATED_VIX_ACCESS_PROBE",
        "as_of": date.today().isoformat(),
        "status": status,
        "valid_sources": passed,
        "required_sources": sorted(required),
        "full_v149_v154_permitted": confirmed,
        "live_ready": False,
        "real_leverage_authorized": False,
        "sources": evidence,
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    report = [
        "# V148 Cboe dated VIX futures access probe",
        "",
        f"Status: `{status}`",
        "",
        "| Dataset | Valid | Rows | Dates | SHA-256 |",
        "|---|---:|---:|---|---|",
    ]
    for name, item in evidence.items():
        parsed = item.get("parsed") or {}
        http = item.get("http") or {}
        report.append(
            f"| {name} | {'yes' if item['valid'] else 'no'} | "
            f"{parsed.get('row_count', 0)} | "
            f"{parsed.get('date_min') or '—'} … {parsed.get('date_max') or '—'} | "
            f"{http.get('sha256') or '—'} |"
        )
    report += [
        "",
        "The probe does not select or test a trading strategy.",
        "Raw official Cboe files and their hashes are stored alongside this report.",
    ]
    (args.output / "REPORT_RU.md").write_text("\n".join(report) + "\n")
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if confirmed else 2


if __name__ == "__main__":
    raise SystemExit(main())
