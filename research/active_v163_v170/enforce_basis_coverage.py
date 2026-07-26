#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

REQUIRED_START = pd.Timestamp("2021-01-01T00:00:00Z")
REQUIRED_END = pd.Timestamp("2026-06-30T00:00:00Z")
START_TOLERANCE = pd.Timedelta(days=7)
END_TOLERANCE = pd.Timedelta(days=7)
MIN_FULL_ASSETS = 2
MIN_ALIGNED_ROWS = 35_000


def canonical_hash(value: Any) -> str:
    data = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    results = args.root / "results"

    quality = pd.read_csv(results / "data_quality.csv")
    full_assets: list[str] = []
    evidence: list[dict[str, Any]] = []
    for row in quality.to_dict(orient="records"):
        start = pd.to_datetime(row.get("timestamp_min"), utc=True, errors="coerce")
        end = pd.to_datetime(row.get("timestamp_max"), utc=True, errors="coerce")
        rows = int(row.get("aligned_rows") or 0)
        starts_on_time = bool(
            not pd.isna(start) and start <= REQUIRED_START + START_TOLERANCE
        )
        ends_on_time = bool(
            not pd.isna(end) and end >= REQUIRED_END - END_TOLERANCE
        )
        enough_rows = rows >= MIN_ALIGNED_ROWS
        full = bool(starts_on_time and ends_on_time and enough_rows)
        if full:
            full_assets.append(str(row["asset"]))
        evidence.append(
            {
                "asset": str(row["asset"]),
                "timestamp_min": None if pd.isna(start) else start.isoformat(),
                "timestamp_max": None if pd.isna(end) else end.isoformat(),
                "aligned_rows": rows,
                "starts_on_time": starts_on_time,
                "ends_on_time": ends_on_time,
                "enough_rows": enough_rows,
                "full_coverage": full,
            }
        )

    passed = len(full_assets) >= MIN_FULL_ASSETS
    gate = {
        "required_start": REQUIRED_START.isoformat(),
        "required_end": REQUIRED_END.isoformat(),
        "start_tolerance_days": START_TOLERANCE.days,
        "end_tolerance_days": END_TOLERANCE.days,
        "min_aligned_rows": MIN_ALIGNED_ROWS,
        "min_full_assets": MIN_FULL_ASSETS,
        "full_assets": sorted(full_assets),
        "passed": passed,
        "assets": evidence,
    }
    (results / "coverage_gate.json").write_text(
        json.dumps(gate, indent=2) + "\n", encoding="utf-8"
    )

    proof_path = results / "selection_proof_before_final.json"
    proof = json.loads(proof_path.read_text())
    proof["data_coverage_gate"] = gate
    proof.pop("selection_proof_sha256", None)
    proof["selection_proof_sha256"] = canonical_hash(proof)
    proof_path.write_text(json.dumps(proof, indent=2) + "\n")

    summary_path = results / "summary.json"
    summary = json.loads(summary_path.read_text())
    summary["selection"] = proof
    summary.setdefault("checks", {})["full_data_coverage"] = passed
    summary["data_coverage_gate"] = gate
    if not passed:
        summary["status"] = "rejected_data_coverage"
        summary["standalone_selection_passed"] = False
        summary["integration"] = {
            "permitted": False,
            "tested": False,
            "reason": "fewer than two assets have complete synchronized price history",
        }
        summary["checks"]["eligible_before_final"] = False
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")

    decision_path = results / "FROZEN_DECISION.json"
    decision = json.loads(decision_path.read_text())
    if not passed:
        decision.update(
            {
                "status": "rejected_data_coverage",
                "standalone_selection_passed": False,
                "integration_permitted": False,
                "promoted_candidates": [],
            }
        )
    decision["data_coverage_gate"] = gate
    decision_path.write_text(json.dumps(decision, indent=2) + "\n")

    report_path = results / "REPORT_RU.md"
    with report_path.open("a", encoding="utf-8") as handle:
        handle.write("\n## Data coverage gate\n\n")
        handle.write(f"Full synchronized assets: `{', '.join(sorted(full_assets)) or 'none'}`.\n\n")
        handle.write(f"Gate passed: `{passed}`.\n")
        if not passed:
            handle.write(
                "The strategy is rejected before integration regardless of its simulated return.\n"
            )

    print(json.dumps(gate, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
