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
            "frontend/backtest-profiles.js",
            "frontend/backtest-profiles.css",
            "frontend/data/dashboard.json",
            "scripts/run_control_room.py",
            "src/finruntime/observability/control_room.py",
            "src/finruntime/observability/server.py",
            "src/finruntime/observability/telemetry.py",
        ):
            path = ROOT / relative
            self.assertTrue(path.is_file(), relative)
            self.assertGreater(path.stat().st_size, 100, relative)

    def test_root_entry_opens_realtime_strategy_hub(self) -> None:
        html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
        self.assertIn("FIN Strategy Hub — realtime entrypoint", html)
        self.assertIn("./live.html?v=frontend-root-live-20260804", html)
        self.assertIn("window.location.replace", html)
        self.assertNotIn("http://cdn", html)
        self.assertNotIn("https://cdn", html)

    def test_live_page_uses_realtime_paper_api(self) -> None:
        html = (ROOT / "frontend/live.html").read_text(encoding="utf-8")
        script = (ROOT / "frontend/live.js").read_text(encoding="utf-8")
        profile_script = (ROOT / "frontend/backtest-profiles.js").read_text(encoding="utf-8")
        profile_style = (ROOT / "frontend/backtest-profiles.css").read_text(encoding="utf-8")
        self.assertIn('id="candle-chart"', html)
        self.assertIn('id="chart-window-tabs"', html)
        self.assertIn('id="chart-inspector"', html)
        self.assertIn('id="position-body"', html)
        self.assertIn('id="market-rows"', html)
        self.assertIn('id="strategy-contexts"', html)
        self.assertIn("Почему стратегии ждут", html)
        self.assertIn('id="strategy-dialog"', html)
        self.assertIn('aria-labelledby="strategy-dialog-title"', html)
        self.assertIn('id="strategy-dialog-close"', html)
        self.assertIn('id="backtest-button"', html)
        self.assertIn('id="backtest-dialog"', html)
        self.assertIn('aria-labelledby="backtest-dialog-title"', html)
        self.assertIn('id="backtest-trades"', html)
        self.assertIn('id="backtest-profile-section"', html)
        self.assertIn('id="backtest-profiles"', html)
        self.assertIn('src="./backtest-profiles.js"', html)
        self.assertIn('href="./backtest-profiles.css"', html)
        self.assertIn('id="backtest-trade-eyebrow"', html)
        self.assertIn('id="backtest-trade-title"', html)
        self.assertIn('id="strategy-ab-panel"', html)
        self.assertIn('id="strategy-ab-progress"', html)
        self.assertIn("/api/v1/ws", script)
        self.assertIn("renderChart", script)
        self.assertIn("renderChartInspector", script)
        self.assertIn('addEventListener("pointermove"', script)
        self.assertIn('event.key === "ArrowLeft"', script)
        self.assertIn("renderStrategyContexts", script)
        self.assertIn("context.how_it_works", script)
        self.assertIn("context.full_description", script)
        self.assertIn('setAttribute("aria-haspopup", "dialog")', script)
        self.assertIn("showModal()", script)
        self.assertIn('addEventListener("close"', script)
        self.assertIn('event.key !== "Escape"', script)
        self.assertIn("/api/v1/backtests/", script)
        self.assertIn('method: "POST"', script)
        self.assertIn('cache: "no-store"', script)
        self.assertNotIn("backtestCache", script)
        self.assertIn("openBacktestDialog", script)
        self.assertIn("renderBacktestReport", script)
        self.assertIn("requested_window_metrics", script)
        self.assertIn("account_leverage_episodes", script)
        self.assertIn("renderForwardAb", script)
        self.assertIn("strategy?.detail?.forward_ab", script)
        self.assertIn("dyn-iv113-risk50", profile_script)
        self.assertIn("dyn-iv113-band2", profile_script)
        self.assertIn("atlas-v517-reference", profile_script)
        self.assertIn("window.fetch = async", profile_script)
        self.assertIn("backtest-profile.active", profile_style)
        self.assertNotIn("/api/v1/orders", profile_script)
        self.assertNotIn("backtest-settings", html)
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
