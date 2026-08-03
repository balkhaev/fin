from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class FrontendRootEntryTests(unittest.TestCase):
    def test_root_redirects_to_realtime_strategy_hub(self) -> None:
        index = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
        self.assertIn("FIN Strategy Hub — realtime entrypoint", index)
        self.assertIn("./live.html?v=frontend-root-live-20260804", index)
        self.assertIn("window.location.replace", index)

    def test_realtime_page_contains_ds40180_ab_panel(self) -> None:
        live = (ROOT / "frontend/live.html").read_text(encoding="utf-8")
        self.assertIn('id="strategy-ab-panel"', live)
        self.assertIn('id="strategy-ab-progress"', live)
        self.assertIn('id="strategy-ab-v2-nav"', live)
        self.assertIn('id="strategy-ab-v1-nav"', live)


if __name__ == "__main__":
    unittest.main()
