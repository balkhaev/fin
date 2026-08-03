#!/usr/bin/env python3
"""Make the realtime Strategy Hub the default static frontend entrypoint."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
INDEX = FRONTEND / "index.html"
ARCHIVE = FRONTEND / "archive.html"
LIVE = FRONTEND / "live.html"
TEST = ROOT / "tests" / "frontend" / "test_frontend_root_entry.py"
ASSET_VERSION = "frontend-root-live-20260804"


def main() -> None:
    legacy = INDEX.read_text(encoding="utf-8")
    if "FIN Strategy Hub — realtime entrypoint" not in legacy:
        if ARCHIVE.exists():
            existing = ARCHIVE.read_text(encoding="utf-8")
            if "FIN Control Room" not in existing:
                raise RuntimeError("frontend/archive.html exists but is not the legacy dashboard")
        else:
            ARCHIVE.write_text(legacy, encoding="utf-8")

    redirect = f'''<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex">
  <meta http-equiv="refresh" content="0; url=./live.html?v={ASSET_VERSION}">
  <title>FIN Strategy Hub</title>
  <script>
    window.location.replace("./live.html?v={ASSET_VERSION}");
  </script>
</head>
<body>
  <!-- FIN Strategy Hub — realtime entrypoint -->
  <p>Открываем realtime FIN Strategy Hub. <a href="./live.html?v={ASSET_VERSION}">Перейти вручную</a>.</p>
  <p><a href="./archive.html">Архивный research dashboard</a></p>
</body>
</html>
'''
    INDEX.write_text(redirect, encoding="utf-8")

    live = LIVE.read_text(encoding="utf-8")
    replacements = {
        'href="./live.css"': f'href="./live.css?v={ASSET_VERSION}"',
        'href="./backtest-profiles.css"': f'href="./backtest-profiles.css?v={ASSET_VERSION}"',
        'src="./backtest-profiles.js"': f'src="./backtest-profiles.js?v={ASSET_VERSION}"',
        'src="./live.js"': f'src="./live.js?v={ASSET_VERSION}"',
    }
    for old, new in replacements.items():
        if new in live:
            continue
        if old not in live:
            raise RuntimeError(f"expected live frontend asset reference is missing: {old}")
        live = live.replace(old, new, 1)
    if 'name="fin-frontend-build"' not in live:
        marker = '  <meta name="description" content="FIN Strategy Hub — единый realtime-интерфейс paper-стратегий.">\n'
        replacement = marker + f'  <meta name="fin-frontend-build" content="{ASSET_VERSION}">\n'
        if marker not in live:
            raise RuntimeError("live frontend description marker is missing")
        live = live.replace(marker, replacement, 1)
    LIVE.write_text(live, encoding="utf-8")

    TEST.write_text(
        '''from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSET_VERSION = "frontend-root-live-20260804"


class FrontendRootEntryTests(unittest.TestCase):
    def test_root_redirects_to_realtime_strategy_hub(self) -> None:
        index = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
        self.assertIn("FIN Strategy Hub — realtime entrypoint", index)
        self.assertIn(f"./live.html?v={ASSET_VERSION}", index)
        self.assertIn("window.location.replace", index)

    def test_legacy_dashboard_remains_available_as_archive(self) -> None:
        archive = (ROOT / "frontend/archive.html").read_text(encoding="utf-8")
        self.assertIn("FIN Control Room", archive)
        self.assertIn('src="./app.js"', archive)

    def test_live_assets_are_cache_busted_and_ab_panel_remains_present(self) -> None:
        live = (ROOT / "frontend/live.html").read_text(encoding="utf-8")
        for asset in (
            "live.css",
            "backtest-profiles.css",
            "backtest-profiles.js",
            "live.js",
        ):
            self.assertIn(f"{asset}?v={ASSET_VERSION}", live)
        self.assertIn(f'name="fin-frontend-build" content="{ASSET_VERSION}"', live)
        self.assertIn('id="strategy-ab-panel"', live)


if __name__ == "__main__":
    unittest.main()
''',
        encoding="utf-8",
    )
    print("frontend root now targets live Strategy Hub")


if __name__ == "__main__":
    main()
