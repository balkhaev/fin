from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_frontend_data.py"

spec = importlib.util.spec_from_file_location("build_frontend_data", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class DashboardBuildTests(unittest.TestCase):
    def test_repository_dashboard_is_fail_closed(self) -> None:
        value = module.build_dashboard(ROOT)
        module.validate_dashboard(value)
        self.assertFalse(value["environment"]["live_ready"])
        self.assertFalse(value["environment"]["exchange_submission_available"])
        self.assertTrue(any(item["status"] == "block" for item in value["readiness"]))
        self.assertEqual(value["hero"]["strategy"], "V517 Tri-state Guard")

    def test_v517_target_is_labeled_non_pristine(self) -> None:
        value = module.build_dashboard(ROOT)
        self.assertTrue(value["governance"]["historical_target_met"])
        self.assertTrue(value["governance"]["parameters_informed_by_history"])
        self.assertFalse(value["governance"]["pristine_holdout"])
        self.assertFalse(value["governance"]["promotion_permitted"])

    def test_builder_writes_valid_json(self) -> None:
        value = module.build_dashboard(ROOT)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "dashboard.json"
            output.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            restored = json.loads(output.read_text(encoding="utf-8"))
            module.validate_dashboard(restored)
            self.assertGreater(len(restored["equity_curve"]), 0)
            self.assertEqual(restored["schema_version"], 1)

    def test_market_state_has_six_axes(self) -> None:
        value = module.build_dashboard(ROOT)
        self.assertEqual([axis["name"] for axis in value["market"]["axes"]], [
            "Trend", "Breadth", "Stress", "Rotation", "Liquidity", "Leverage"
        ])


if __name__ == "__main__":
    unittest.main()
