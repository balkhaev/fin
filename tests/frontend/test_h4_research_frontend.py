from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"


class H4ResearchFrontendTests(unittest.TestCase):
    def test_h4_static_bundle_is_complete_and_fail_closed(self) -> None:
        for name in (
            "h4.html",
            "h4.css",
            "h4.js",
            "h4-widget.css",
            "h4-widget.js",
            "data/h4-cagr50.json",
        ):
            self.assertTrue((FRONTEND / name).is_file(), name)

        html = (FRONTEND / "h4.html").read_text(encoding="utf-8")
        widget = (FRONTEND / "h4-widget.js").read_text(encoding="utf-8")
        self.assertIn("RESEARCH · PAPER ONLY", html)
        self.assertIn("exchange submission unavailable", widget)
        self.assertNotIn("/api/v1/orders", html + widget)
        self.assertNotIn("create_order", html + widget)
        self.assertNotIn("submit_order", html + widget)

    def test_h4_evidence_passes_committed_contract(self) -> None:
        payload = json.loads(
            (FRONTEND / "data" / "h4-cagr50.json").read_text(encoding="utf-8")
        )
        columns = payload["scenario_columns"]
        severe_row = next(item for item in payload["scenarios"] if item[0] == "severe")
        severe = dict(zip(columns, severe_row, strict=True))
        self.assertEqual(payload["v"], 1)
        self.assertFalse(payload["orders"])
        self.assertFalse(payload["live"])
        self.assertGreater(severe["cagr"], 0.50)
        self.assertGreater(severe["profit_factor"], 1.0)
        self.assertEqual(severe["opened_sleeves"], 120)
        self.assertAlmostEqual(severe["independent_entries_per_day"], 120 / 365, places=6)
        self.assertEqual(len(payload["trades"]), 120)
        self.assertEqual(len(payload["curves"]["severe"]), 53)

    def test_strategy_hub_profile_script_mounts_h4_widget(self) -> None:
        profiles = (FRONTEND / "backtest-profiles.js").read_text(encoding="utf-8")
        self.assertIn("h4-research-widget", profiles)
        self.assertIn("./h4-widget.css", profiles)
        self.assertIn("./h4-widget.js", profiles)

    def test_h4_javascript_syntax(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is unavailable")
        for name in ("h4.js", "h4-widget.js"):
            result = subprocess.run(
                [node, "--check", str(FRONTEND / name)],
                check=False, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
