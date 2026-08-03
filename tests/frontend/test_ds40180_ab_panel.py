from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class Ds40180ForwardAbPanelTests(unittest.TestCase):
    def test_live_markup_has_first_class_ab_panel(self) -> None:
        html = (ROOT / "frontend/live.html").read_text(encoding="utf-8")
        for value in (
            'id="strategy-ab-panel"',
            'id="strategy-ab-progress"',
            'id="strategy-ab-v2-nav"',
            'id="strategy-ab-v1-nav"',
            'id="strategy-ab-return-delta"',
            'id="strategy-ab-turnover-delta"',
        ):
            self.assertIn(value, html)

    def test_live_renderer_reads_strategy_hub_ab_payload(self) -> None:
        javascript = (ROOT / "frontend/live.js").read_text(encoding="utf-8")
        self.assertIn('const renderForwardAb = (strategy) =>', javascript)
        self.assertIn('strategy?.detail?.forward_ab', javascript)
        self.assertIn('arms.legacyV1Reference', javascript)
        self.assertIn('arms.forwardV2', javascript)
        self.assertIn('deltasV2MinusV1', javascript)
        self.assertIn('renderForwardAb(strategy);', javascript)
        self.assertIn('Победитель автоматически не назначается', javascript)

    def test_live_styles_include_progress_and_status_states(self) -> None:
        css = (ROOT / "frontend/live.css").read_text(encoding="utf-8")
        for selector in (
            '.strategy-ab-panel {',
            '.strategy-ab-status.collecting',
            '.strategy-ab-status.ready',
            '.strategy-ab-progress-track i',
            '.strategy-ab-metrics {',
        ):
            self.assertIn(selector, css)


if __name__ == "__main__":
    unittest.main()
