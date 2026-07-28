from __future__ import annotations

import csv
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from finruntime.operations.cycle import TELEMETRY_FIELDS

BOOLEAN_FIELDS = {
    "reconciliation_ok",
    "source_hash_match",
    "data_stale",
    "execution_complete",
}
NUMERIC_FIELDS = {
    "gross_target",
    "gross_realized",
    "turnover",
    "modelled_slippage_bps",
    "paper_slippage_bps",
    "net_return",
    "equity",
    "drawdown",
}


def parse_timestamp(value: object) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_utc(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no", ""}:
        return False
    raise ValueError(f"invalid telemetry boolean: {value!r}")


def parse_number(value: object) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"non-finite telemetry number: {value!r}")
    return number


def parse_row(raw: Mapping[str, str]) -> dict[str, object]:
    if set(raw) != set(TELEMETRY_FIELDS):
        raise ValueError("forward telemetry row has an unexpected schema")
    row: dict[str, object] = {}
    for field in TELEMETRY_FIELDS:
        value = raw[field]
        if field in BOOLEAN_FIELDS:
            row[field] = parse_bool(value)
        elif field in NUMERIC_FIELDS:
            row[field] = parse_number(value)
        else:
            row[field] = value
    parse_timestamp(row["timestamp"])
    if not row["strategy_id"]:
        raise ValueError("telemetry strategy_id cannot be empty")
    return row


def read_telemetry(path: str | Path) -> list[dict[str, object]]:
    source = Path(path)
    if not source.exists():
        return []
    with source.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != TELEMETRY_FIELDS:
            raise ValueError("forward telemetry CSV schema mismatch")
        rows = [parse_row(row) for row in reader]
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (str(row["timestamp"]), str(row["strategy_id"]))
        if key in seen:
            raise ValueError(f"duplicate telemetry primary key: {key}")
        seen.add(key)
    rows.sort(key=lambda row: (parse_timestamp(row["timestamp"]), str(row["strategy_id"])))
    return rows


def telemetry_incidents(rows: list[dict[str, object]], strategy_id: str) -> list[dict[str, object]]:
    incidents: list[dict[str, object]] = []

    def add(row: Mapping[str, object], severity: str, category: str, title: str, detail: str) -> None:
        incidents.append(
            {
                "timestamp": iso_utc(parse_timestamp(row["timestamp"])),
                "strategy_id": strategy_id,
                "severity": severity,
                "category": category,
                "title": title,
                "detail": detail,
                "cycle_id": None,
            }
        )

    for row in rows:
        if not bool(row["reconciliation_ok"]):
            add(row, "halt", "reconciliation_failure", "Reconciliation failed", "Realized paper positions do not match committed execution evidence.")
        if not bool(row["source_hash_match"]):
            add(row, "halt", "source_hash_mismatch", "Source hash mismatch", "The market/model input bundle does not match its sealed source hash.")
        if bool(row["data_stale"]):
            add(row, "halt", "stale_data", "Stale market data", "This observation cannot count as clean forward evidence.")
        if not bool(row["execution_complete"]):
            add(row, "warn", "incomplete_execution", "Execution incomplete", "At least one planned paper intent was not completely filled.")
        modelled = float(row["modelled_slippage_bps"])
        paper = float(row["paper_slippage_bps"])
        ratio = paper / modelled if modelled > 0 else (math.inf if paper > 0 else 0.0)
        if ratio > 1.5:
            add(row, "warn", "slippage_overrun", "Slippage exceeded model", f"Paper/model slippage ratio is {ratio:.2f}x; frozen limit is 1.50x.")
    return incidents
