from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from finruntime.journal import AppendOnlyJournal
from finruntime.observability.control_room import build_runtime_snapshot
from finruntime.observability.telemetry import read_telemetry
from finruntime.operations.cycle import TELEMETRY_FIELDS


def row(*, timestamp: str = "2026-07-28T12:00:00Z", strategy_id: str = "v75_atlas_nx", reconciliation_ok: bool = True, source_hash_match: bool = True, data_stale: bool = False, execution_complete: bool = True) -> dict[str, object]:
    return {
        "timestamp": timestamp,
        "strategy_id": strategy_id,
        "source_bundle_sha256": "sha256:" + "1" * 64,
        "target_hash": "sha256:" + "2" * 64,
        "realized_position_hash": "sha256:" + "3" * 64,
        "gross_target": 0.8,
        "gross_realized": 0.79,
        "turnover": 0.05,
        "modelled_slippage_bps": 4.0,
        "paper_slippage_bps": 4.5,
        "net_return": 0.001,
        "equity": 10010.0,
        "drawdown": -0.01,
        "reconciliation_ok": reconciliation_ok,
        "source_hash_match": source_hash_match,
        "data_stale": data_stale,
        "execution_complete": execution_complete,
    }


def write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TELEMETRY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


class TelemetryTests(unittest.TestCase):
    def test_strict_schema_and_duplicate_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "forward_telemetry.csv"
            write_rows(path, [row(), row()])
            with self.assertRaisesRegex(ValueError, "duplicate telemetry primary key"):
                read_telemetry(path)

    def test_clean_runtime_is_healthy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            strategy = root / "v75_atlas_nx"
            write_rows(strategy / "forward_telemetry.csv", [row()])
            AppendOnlyJournal(strategy / "events.jsonl").append(
                event_type="SNAPSHOT_ACCEPTED",
                event_time_utc="2026-07-28T12:00:00Z",
                strategy_id="v75_atlas_nx",
                sequence=1,
                payload={"ok": True},
            )
            snapshot = build_runtime_snapshot(root, now=datetime(2026, 7, 28, 12, 30, tzinfo=timezone.utc))
            self.assertEqual(snapshot["status"], "healthy")
            self.assertEqual(snapshot["aggregate"]["strategy_count"], 1)
            self.assertEqual(snapshot["aggregate"]["observation_count"], 1)
            self.assertEqual(snapshot["strategies"][0]["journal_events"], 1)
            self.assertFalse(snapshot["live_ready"])
            self.assertFalse(snapshot["exchange_submission_available"])

    def test_integrity_failure_halts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            strategy = root / "v517_tristate_guard_shadow"
            write_rows(strategy / "forward_telemetry.csv", [row(strategy_id=strategy.name, source_hash_match=False)])
            (strategy / "events.jsonl").write_text("not-json\n", encoding="utf-8")
            snapshot = build_runtime_snapshot(root, now=datetime(2026, 7, 28, 12, 30, tzinfo=timezone.utc))
            self.assertEqual(snapshot["status"], "halt")
            categories = {item["category"] for item in snapshot["incidents"]}
            self.assertIn("source_hash_mismatch", categories)
            self.assertIn("journal_corruption", categories)

    def test_optional_runtime_context_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "v517_state.json").write_text(json.dumps({"market_state": "high", "target_leverage": 1.1}), encoding="utf-8")
            (root / "market_state.json").write_text(json.dumps({"state_label": "transition", "confidence": 0.52}), encoding="utf-8")
            snapshot = build_runtime_snapshot(root)
            self.assertEqual(snapshot["v517"]["state"]["market_state"], "high")
            self.assertEqual(snapshot["market_state"]["state_label"], "transition")
            self.assertEqual(snapshot["status"], "idle")


if __name__ == "__main__":
    unittest.main()
