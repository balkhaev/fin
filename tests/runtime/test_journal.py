from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from finruntime.canonical import ContractError
from finruntime.journal import AppendOnlyJournal, JournalCorruptionError


class JournalTests(unittest.TestCase):
    def test_hash_chain_and_idempotent_append(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            journal = AppendOnlyJournal(path)
            first = journal.append(
                event_type="SNAPSHOT_ACCEPTED",
                event_time_utc="2026-07-27T00:05:00Z",
                strategy_id="v75_atlas_nx",
                sequence=1,
                payload={"snapshot_id": "sha256:" + "1" * 64},
            )
            duplicate = journal.append(
                event_type="SNAPSHOT_ACCEPTED",
                event_time_utc="2026-07-27T00:05:00Z",
                strategy_id="v75_atlas_nx",
                sequence=1,
                payload={"snapshot_id": "sha256:" + "1" * 64},
            )
            self.assertEqual(first["event_hash"], duplicate["event_hash"])
            self.assertEqual(len(journal.verify()), 1)

            journal.append(
                event_type="TARGET_COMPUTED",
                event_time_utc="2026-07-27T00:05:01Z",
                strategy_id="v75_atlas_nx",
                sequence=1,
                payload={"target_hash": "sha256:" + "2" * 64},
            )
            self.assertEqual(len(journal.verify()), 2)

    def test_batch_append_is_atomic_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = AppendOnlyJournal(Path(directory) / "events.jsonl")
            specs = [
                {
                    "event_type": "SNAPSHOT_ACCEPTED",
                    "event_time_utc": "2026-07-27T00:05:00Z",
                    "strategy_id": "v75_atlas_nx",
                    "sequence": 1,
                    "payload": {"snapshot": 1},
                },
                {
                    "event_type": "TARGET_COMPUTED",
                    "event_time_utc": "2026-07-27T00:05:01Z",
                    "strategy_id": "v75_atlas_nx",
                    "sequence": 1,
                    "payload": {"target": 1},
                },
            ]
            first = journal.append_many(specs)
            second = journal.append_many(specs)
            self.assertEqual(
                [item["event_hash"] for item in first],
                [item["event_hash"] for item in second],
            )
            self.assertEqual(len(journal.verify()), 2)

    def test_conflicting_singleton_event_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = AppendOnlyJournal(Path(directory) / "events.jsonl")
            journal.append(
                event_type="SNAPSHOT_ACCEPTED",
                event_time_utc="2026-07-27T00:04:59Z",
                strategy_id="v75_atlas_nx",
                sequence=7,
                payload={"snapshot": "a"},
            )
            journal.append(
                event_type="TARGET_COMPUTED",
                event_time_utc="2026-07-27T00:05:00Z",
                strategy_id="v75_atlas_nx",
                sequence=7,
                payload={"target": "a"},
            )
            with self.assertRaises(ContractError):
                journal.append(
                    event_type="TARGET_COMPUTED",
                    event_time_utc="2026-07-27T00:05:01Z",
                    strategy_id="v75_atlas_nx",
                    sequence=7,
                    payload={"target": "b"},
                )

    def test_semantic_regressions_fail_before_append(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = AppendOnlyJournal(Path(directory) / "events.jsonl")
            journal.append(
                event_type="SNAPSHOT_ACCEPTED",
                event_time_utc="2026-07-27T00:05:00Z",
                strategy_id="v75_atlas_nx",
                sequence=2,
                payload={"snapshot": 2},
            )
            with self.assertRaises(JournalCorruptionError):
                journal.append(
                    event_type="SNAPSHOT_ACCEPTED",
                    event_time_utc="2026-07-27T00:06:00Z",
                    strategy_id="v75_atlas_nx",
                    sequence=1,
                    payload={"snapshot": 1},
                )
            self.assertEqual(len(journal.verify()), 1)

            with self.assertRaises(JournalCorruptionError):
                journal.append(
                    event_type="TARGET_COMPUTED",
                    event_time_utc="2026-07-27T00:04:00Z",
                    strategy_id="v75_atlas_nx",
                    sequence=2,
                    payload={"target": 2},
                )
            self.assertEqual(len(journal.verify()), 1)

    def test_runtime_phase_requires_predecessors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = AppendOnlyJournal(Path(directory) / "events.jsonl")
            journal.append(
                event_type="SNAPSHOT_ACCEPTED",
                event_time_utc="2026-07-27T00:05:00Z",
                strategy_id="v75_atlas_nx",
                sequence=1,
                payload={"snapshot": 1},
            )
            with self.assertRaises(JournalCorruptionError):
                journal.append(
                    event_type="PLAN_CREATED",
                    event_time_utc="2026-07-27T00:05:01Z",
                    strategy_id="v75_atlas_nx",
                    sequence=1,
                    payload={"plan": 1},
                )
            self.assertEqual(len(journal.verify()), 1)

    def test_corruption_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            journal = AppendOnlyJournal(path)
            journal.append(
                event_type="SNAPSHOT_ACCEPTED",
                event_time_utc="2026-07-27T00:05:00Z",
                strategy_id="v75_atlas_nx",
                sequence=1,
                payload={"ok": True},
            )
            raw = path.read_text(encoding="utf-8").replace('"ok":true', '"ok":false')
            path.write_text(raw, encoding="utf-8")
            with self.assertRaises(JournalCorruptionError):
                journal.verify()


if __name__ == "__main__":
    unittest.main()
