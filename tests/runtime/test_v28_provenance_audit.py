from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from scripts.verify_runtime import load_source_registry, provenance_completeness_issues

ROOT = Path(__file__).resolve().parents[2]
AUDIT_PATH = ROOT / "docs/checkpoints/runtime-v1/V28_PROVENANCE_CLOSURE_AUDIT.json"


class V28ProvenanceClosureAuditTests(unittest.TestCase):
    def test_direct_archive_hashes_match_registry_and_repository_bytes(self) -> None:
        registry = load_source_registry()
        profile = registry["profiles"]["v28_growth_control"]
        audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))

        self.assertTrue(profile["direct_archive_integrity_passed"])
        self.assertFalse(profile["provenance_complete"])
        self.assertIn(
            "provenance_complete=false",
            provenance_completeness_issues(profile),
        )
        self.assertTrue(audit["direct_archive"]["verify_archive_passed"])

        for relative, expected_sha256 in profile["source_paths"].items():
            path = ROOT / relative
            self.assertTrue(path.is_file(), relative)
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(actual, expected_sha256, relative)

        direct_files = audit["direct_archive"]["files"]
        for name, evidence in direct_files.items():
            path = ROOT / "research/active_v26_v28" / name
            self.assertEqual(path.stat().st_size, evidence["bytes"], name)
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                evidence["sha256"],
                name,
            )

    def test_missing_dependency_and_fixture_closure_stays_fail_closed(self) -> None:
        registry = load_source_registry()
        profile = registry["profiles"]["v28_growth_control"]
        audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))

        self.assertEqual(
            profile["provenance_audit"]["path"],
            "docs/checkpoints/runtime-v1/V28_PROVENANCE_CLOSURE_AUDIT.json",
        )
        self.assertEqual(
            profile["provenance_audit"]["conclusion"],
            "direct_source_integrity_passes_but_executable_dependency_and_fixture_closure_incomplete",
        )
        self.assertEqual(
            audit["conclusion"],
            "direct_source_integrity_passes_but_executable_dependency_and_fixture_closure_incomplete",
        )
        self.assertFalse(audit["provenance_complete"])
        self.assertFalse(audit["source_dependency_closure"]["closure_complete"])

        missing = audit["source_dependency_closure"]["missing_unique_modules"]
        self.assertEqual(
            set(missing),
            {
                "execution_policy.py",
                "v35_funding_carry.py",
                "v36_cash_carry.py",
                "v50_exact8h_audit.py",
                "v43_exact8h_fast.py",
            },
        )
        for evidence in missing.values():
            self.assertEqual(evidence["reachable_candidates"], 0)
            self.assertEqual(
                evidence["status"],
                "absent_from_current_repository_history",
            )

        fixtures = audit["regression_fixture_audit"]
        self.assertFalse(fixtures["daily_target_fixture_present"])
        self.assertFalse(fixtures["equity_curve_fixture_present"])
        self.assertFalse(fixtures["position_or_weight_fixture_present"])
        self.assertFalse(
            audit["local_environment_dependencies"][
                "raw_inputs_materialized_in_repository"
            ]
        )

        requirements = profile["unmaterialized_requirements"]
        self.assertEqual(
            requirements["exact_daily_target_fixture"]["status"],
            "absent_from_current_repository_history",
        )
        self.assertEqual(
            requirements["raw_market_inputs"]["status"],
            "not_materialized_in_repository",
        )

    def test_audit_does_not_authorize_live_or_parameter_changes(self) -> None:
        audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
        safety = audit["safety"]

        self.assertFalse(safety["strategy_parameters_changed"])
        self.assertFalse(safety["live_execution_available"])
        self.assertFalse(safety["live_ready"])
        self.assertFalse(safety["real_leverage_authorized"])


if __name__ == "__main__":
    unittest.main()
