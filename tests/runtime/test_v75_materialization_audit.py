from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.verify_runtime import load_source_registry, provenance_completeness_issues

ROOT = Path(__file__).resolve().parents[2]
AUDIT_PATH = ROOT / "docs/checkpoints/runtime-v1/V75_MATERIALIZATION_AUDIT.json"


class V75MaterializationAuditTests(unittest.TestCase):
    def test_registry_remains_fail_closed_after_recovery_audit(self) -> None:
        registry = load_source_registry()
        profile = registry["profiles"]["v75_atlas_nx"]

        self.assertFalse(profile["provenance_complete"])
        self.assertIn(
            "provenance_complete=false",
            provenance_completeness_issues(profile),
        )
        self.assertEqual(
            profile["recovery_audit"]["path"],
            "docs/checkpoints/runtime-v1/V75_MATERIALIZATION_AUDIT.json",
        )
        self.assertEqual(
            profile["recovery_audit"]["conclusion"],
            "exact_v75_evidence_not_recoverable_from_current_repository_history",
        )

        requirements = profile["unmaterialized_requirements"]
        self.assertEqual(
            set(requirements),
            {
                "canonical_engine",
                "full_regression_equity",
                "annual_returns_fixture",
            },
        )
        for requirement in requirements.values():
            self.assertEqual(
                requirement["status"],
                "not_recoverable_from_current_repository_history",
            )

    def test_audit_evidence_matches_pinned_targets_and_diagnostics(self) -> None:
        audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))

        self.assertEqual(audit["schema_version"], "1.0")
        self.assertEqual(
            audit["conclusion"],
            "exact_v75_evidence_not_recoverable_from_current_repository_history",
        )
        self.assertFalse(audit["provenance_complete"])
        self.assertFalse(audit["safety"]["live_execution_available"])
        self.assertFalse(audit["safety"]["live_ready"])
        self.assertFalse(audit["safety"]["real_leverage_authorized"])

        expected = audit["expected_targets"]
        self.assertEqual(
            expected["canonical_engine"]["sha256"],
            "3303cd91511bca0be81ade21272e1e8ba6f76adf826d238e9c4bd7cbe78f69fc",
        )
        self.assertEqual(
            expected["full_regression_equity"]["sha256"],
            "0f578a56132ec9858031cc6ad5cc919f732e66990625c2fdd6ff91143e44956b",
        )
        self.assertEqual(
            expected["annual_returns_fixture"]["sha256"],
            "e3de37108b5d459ad9f8324388a3a34571f29c5c594a77d82477cb812c8e0d25",
        )

        object_scan = audit["reachable_git_object_scan"]
        self.assertEqual(object_scan["candidate_blob_count_by_expected_sizes"], 0)
        self.assertEqual(object_scan["exact_target_matches"], [])
        self.assertFalse(object_scan["all_expected_targets_found"])

        v138 = audit["v138_transport"]
        self.assertEqual(v138["extracted_regular_files"], 152)
        self.assertEqual(v138["exact_target_matches"], [])
        self.assertFalse(v138["contains_all_expected_targets"])

        v87 = audit["v87_transport"]
        self.assertEqual(v87["common_prefix_bytes"], 5600)
        self.assertEqual(v87["decompression_error_compressed_offset"], 9216)
        self.assertEqual(v87["complete_tar_members_detected"], 0)
        self.assertFalse(v87["combined_stream_has_xz_footer_magic"])
        self.assertFalse(v87["transport_complete"])


if __name__ == "__main__":
    unittest.main()
