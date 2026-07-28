from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class StaticAssetTests(unittest.TestCase):
    def test_required_assets_exist(self) -> None:
        for relative in (
            "frontend/index.html",
            "frontend/styles.css",
            "frontend/mobile.css",
            "frontend/app.js",
            "frontend/data/dashboard.json",
        ):
            path = ROOT / relative
            self.assertTrue(path.is_file(), relative)
            self.assertGreater(path.stat().st_size, 100, relative)

    def test_html_has_accessible_landmarks(self) -> None:
        html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
        self.assertIn("<main>", html)
        self.assertIn("aria-label", html)
        self.assertIn("telemetry-file", html)
        self.assertIn("./mobile.css", html)
        self.assertNotIn("http://cdn", html)
        self.assertNotIn("https://cdn", html)

    def test_committed_dashboard_does_not_authorize_live(self) -> None:
        value = json.loads((ROOT / "frontend/data/dashboard.json").read_text(encoding="utf-8"))
        self.assertFalse(value["environment"]["live_ready"])
        self.assertFalse(value["environment"]["real_leverage_authorized"])
        self.assertFalse(value["environment"]["exchange_submission_available"])


if __name__ == "__main__":
    unittest.main()
