from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from finruntime.strategies._ds40180_ab import (
    AB_STUDY_ID,
    build_ab_snapshot,
    verify_ab_journal,
)
from finruntime.strategies._ds40180_v1_reference import (
    V1_PAPER_ASSET_CAP,
    V1_PAPER_GROSS_CAP,
    V1_REFERENCE_SOURCE_COMMIT,
    V1_REFERENCE_STRATEGY_ID,
    V1_REFERENCE_VERSION,
    build_v1_reference_engine,
)
from finruntime.strategies.ds40180_t50c3_paper import compute_forward_state, run_once
from tests.runtime.test_ds40180_t50c3_paper import synthetic_histories


class Ds40180ForwardAbTests(unittest.TestCase):
    def test_v1_reference_is_pinned_and_uses_legacy_limits(self) -> None:
        engine = build_v1_reference_engine(synthetic_histories(), [])
        target = list(engine["target"][engine["executionIndex"]])

        self.assertEqual(
            engine["reference"]["strategyId"], V1_REFERENCE_STRATEGY_ID
        )
        self.assertEqual(
            engine["reference"]["strategyVersion"], V1_REFERENCE_VERSION
        )
        self.assertEqual(
            engine["reference"]["sourceCommit"], V1_REFERENCE_SOURCE_COMMIT
        )
        self.assertLessEqual(
            sum(abs(value) for value in target), V1_PAPER_GROSS_CAP + 1e-12
        )
        self.assertTrue(
            all(abs(value) <= V1_PAPER_ASSET_CAP + 1e-12 for value in target)
        )

    def test_ab_snapshot_appends_only_one_observation_per_market_day(self) -> None:
        histories = synthetic_histories()
        reset_date = sorted(histories[0]["bars"])[-15]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            v2_path = root / "ds40180_t50c3_paper_snapshot.json"
            ab_path = root / "ds40180_t50c3_ab_snapshot.json"
            journal_path = root / "ds40180_t50c3_ab_events.jsonl"
            v2_snapshot = compute_forward_state(
                histories,
                [],
                snapshot_path=v2_path,
                reset_date=reset_date,
                initial_nav_usd=10_000.0,
            )
            first = build_ab_snapshot(
                histories,
                [],
                v2_snapshot=v2_snapshot,
                snapshot_path=ab_path,
                journal_path=journal_path,
                reset_date=reset_date,
                initial_nav_usd=10_000.0,
            )
            second = build_ab_snapshot(
                histories,
                [],
                v2_snapshot=v2_snapshot,
                snapshot_path=ab_path,
                journal_path=journal_path,
                reset_date=reset_date,
                initial_nav_usd=10_000.0,
            )

            self.assertEqual(first["studyId"], AB_STUDY_ID)
            self.assertEqual(first["status"], "collecting")
            self.assertTrue(first["quality"]["matched"])
            self.assertEqual(first["forwardObservationDays"], 1)
            self.assertEqual(second["forwardObservationDays"], 1)
            self.assertEqual(
                first["arms"]["legacyV1Reference"]["sourceCommit"],
                V1_REFERENCE_SOURCE_COMMIT,
            )
            self.assertEqual(
                first["arms"]["forwardV2"]["strategyVersion"], "okx-paper-v2"
            )
            self.assertFalse(first["interpretation"]["winnerDeclared"])
            self.assertFalse(first["exchange_submission_available"])
            self.assertFalse(first["live_ready"])
            self.assertFalse(first["real_leverage_authorized"])
            self.assertTrue(ab_path.is_file())
            self.assertEqual(json.loads(ab_path.read_text())["studyId"], AB_STUDY_ID)
            self.assertEqual(verify_ab_journal(journal_path)["events"], 1)

    def test_worker_writes_default_ab_artifacts_without_affecting_safety(self) -> None:
        histories = synthetic_histories()
        reset_date = sorted(histories[0]["bars"])[-15]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot_path = root / "ds40180_t50c3_paper_snapshot.json"
            with patch(
                "finruntime.strategies.ds40180_t50c3_paper.load_market_data",
                return_value=(histories, []),
            ):
                snapshot = run_once(
                    snapshot_path,
                    reset_date=reset_date,
                    initial_nav_usd=10_000.0,
                )

            comparison = snapshot["comparison"]
            self.assertEqual(comparison["studyId"], AB_STUDY_ID)
            self.assertEqual(comparison["forwardObservationDays"], 1)
            self.assertTrue((root / "ds40180_t50c3_ab_snapshot.json").is_file())
            self.assertTrue((root / "ds40180_t50c3_ab_events.jsonl").is_file())
            self.assertFalse(snapshot["exchange_submission_available"])
            self.assertFalse(snapshot["live_ready"])
            self.assertFalse(snapshot["real_leverage_authorized"])

    def test_ab_journal_detects_tampering(self) -> None:
        histories = synthetic_histories()
        reset_date = sorted(histories[0]["bars"])[-15]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            v2_path = root / "ds40180_t50c3_paper_snapshot.json"
            ab_path = root / "ds40180_t50c3_ab_snapshot.json"
            journal_path = root / "ds40180_t50c3_ab_events.jsonl"
            v2_snapshot = compute_forward_state(
                histories,
                [],
                snapshot_path=v2_path,
                reset_date=reset_date,
                initial_nav_usd=10_000.0,
            )
            build_ab_snapshot(
                histories,
                [],
                v2_snapshot=v2_snapshot,
                snapshot_path=ab_path,
                journal_path=journal_path,
                reset_date=reset_date,
                initial_nav_usd=10_000.0,
            )
            event = json.loads(journal_path.read_text())
            event["deltasV2MinusV1"]["navUsd"] += 1.0
            journal_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "event hash mismatch"):
                verify_ab_journal(journal_path)


if __name__ == "__main__":
    unittest.main()
