from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "live_preflight.py"
SPEC = importlib.util.spec_from_file_location("fin_live_preflight", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class LivePreflightTests(unittest.TestCase):
    def args(self, **overrides):
        values = {
            "mode": "shadow",
            "repository_root": str(ROOT),
            "target_producer": None,
            "margin_audit": None,
            "forward_acceptance": None,
            "exchange_adapter_manifest": None,
        }
        values.update(overrides)
        return Namespace(**values)

    def test_shadow_mode_is_ready_and_never_attempts_submission(self) -> None:
        report = MODULE.run_preflight(self.args())
        self.assertTrue(report.shadow_ready)
        self.assertFalse(report.live_ready)
        self.assertFalse(report.exchange_submission_attempted)
        self.assertEqual(report.blockers, ())

    def test_live_mode_fails_closed_without_external_evidence(self) -> None:
        report = MODULE.run_preflight(self.args(mode="live"))
        self.assertTrue(report.shadow_ready)
        self.assertFalse(report.live_ready)
        self.assertFalse(report.exchange_submission_attempted)
        self.assertIn("exact V75 target producer was not supplied", report.blockers)
        self.assertIn("position-level margin/liquidation audit was not supplied", report.blockers)
        self.assertIn("forward acceptance evidence was not supplied", report.blockers)
        self.assertIn("validated exchange adapter manifest was not supplied", report.blockers)

    def test_templates_are_rejected_as_evidence(self) -> None:
        report = MODULE.run_preflight(
            self.args(
                mode="live",
                target_producer="scripts/live_preflight.py",
                margin_audit="config/live/position_margin_audit.template.json",
                forward_acceptance="config/live/forward_acceptance.template.json",
                exchange_adapter_manifest="config/live/exchange_adapter_manifest.template.json",
            )
        )
        self.assertFalse(report.live_ready)
        checks = {item.name: item for item in report.checks}
        self.assertFalse(checks["exact_v75_target_producer"].passed)
        self.assertFalse(checks["position_level_margin_audit"].passed)
        self.assertFalse(checks["forward_acceptance"].passed)
        self.assertFalse(checks["exchange_adapter_manifest"].passed)

    def test_structurally_valid_but_unverified_evidence_still_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            margin = root / "margin.json"
            margin.write_text(
                json.dumps(
                    {
                        "position_level_margin_replay_complete": True,
                        "source_target_hash_match": True,
                        "maximum_tested_leverage": 2.075,
                        "liquidations": 0,
                        "minimum_margin_buffer": 0.20,
                        "passed": True,
                    }
                )
            )
            forward = root / "forward.json"
            forward.write_text(
                json.dumps(
                    {
                        "calendar_days": 180,
                        "checks": {"all": True},
                        "passed": True,
                    }
                )
            )
            adapter = root / "adapter.json"
            adapter.write_text(
                json.dumps(
                    {
                        "exchange_submission_surface": True,
                        "testnet_validated": True,
                        "idempotent_client_order_ids": True,
                        "reduce_only_supported": True,
                        "kill_switch": True,
                        "secrets_from_environment": True,
                        "reconciliation_fail_closed": True,
                        "passed": True,
                    }
                )
            )
            report = MODULE.run_preflight(
                self.args(
                    mode="live",
                    target_producer="scripts/live_preflight.py",
                    margin_audit=str(margin),
                    forward_acceptance=str(forward),
                    exchange_adapter_manifest=str(adapter),
                )
            )
            self.assertFalse(report.live_ready)
            self.assertTrue(any("SHA-256" in blocker for blocker in report.blockers))


if __name__ == "__main__":
    unittest.main()
