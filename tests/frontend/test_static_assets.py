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
            "frontend/live.html",
            "frontend/live.css",
            "frontend/live.js",
            "frontend/data/dashboard.json",
            "scripts/run_control_room.py",
            "src/finruntime/observability/control_room.py",
            "src/finruntime/observability/server.py",
            "src/finruntime/observability/telemetry.py",
        ):
            path = ROOT / relative
            self.assertTrue(path.is_file(), relative)
            self.assertGreater(path.stat().st_size, 100, relative)

    def test_historical_html_remains_accessible(self) -> None:
        html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
        self.assertIn("<main>", html)
        self.assertIn("aria-label", html)
        self.assertIn('id="telemetry-file"', html)
        self.assertNotIn("http://cdn", html)
        self.assertNotIn("https://cdn", html)

    def test_live_page_uses_realtime_paper_api(self) -> None:
        html = (ROOT / "frontend/live.html").read_text(encoding="utf-8")
        script = (ROOT / "frontend/live.js").read_text(encoding="utf-8")
        self.assertIn('id="candle-chart"', html)
        self.assertIn('id="position-body"', html)
        self.assertIn('id="market-rows"', html)
        self.assertIn('id="strategy-contexts"', html)
        self.assertIn("Почему стратегии ждут", html)
        self.assertIn("/api/v1/ws", script)
        self.assertIn("renderChart", script)
        self.assertIn("renderStrategyContexts", script)
        self.assertIn("context.how_it_works", script)
        self.assertIn("WebSocket", script)
        self.assertNotIn("EventSource", script)
        self.assertNotIn("setInterval", script)
        self.assertNotIn("submit_order", script)
        self.assertNotIn("/api/v1/orders", script)

    def test_committed_dashboard_does_not_authorize_live(self) -> None:
        value = json.loads(
            (ROOT / "frontend/data/dashboard.json").read_text(encoding="utf-8")
        )
        self.assertFalse(value["environment"]["live_ready"])
        self.assertFalse(value["environment"]["real_leverage_authorized"])
        self.assertFalse(value["environment"]["exchange_submission_available"])


if __name__ == "__main__":
    unittest.main()
