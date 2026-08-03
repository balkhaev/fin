#!/usr/bin/env python3
"""Wire the additive backtest profile selector into the static frontend."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    content = target.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one marker, found {count}")
    target.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "frontend/live.html",
    '  <link rel="stylesheet" href="./live.css">\n',
    '  <link rel="stylesheet" href="./live.css">\n'
    '  <link rel="stylesheet" href="./backtest-profiles.css">\n',
)
replace_once(
    "frontend/live.html",
    "        <p>Единая сводка четырёх стратегий. Каждый ledger считается отдельно.</p>",
    "        <p>Единая сводка всех paper-стратегий. Каждый ledger считается отдельно.</p>",
)
replace_once(
    "frontend/live.html",
    "              <small>Запустить сейчас · $10 000 · без настроек</small>",
    "              <small>Baseline, shadow и reference · $10 000</small>",
)
replace_once(
    "frontend/live.html",
    '        <p id="backtest-dialog-summary">Запускаем свежий расчёт…</p>\n      </header>\n',
    '        <p id="backtest-dialog-summary">Запускаем свежий расчёт…</p>\n'
    '      </header>\n\n'
    '      <section id="backtest-profile-section" class="backtest-profile-section" hidden>\n'
    '        <span class="eyebrow">Версия расчёта</span>\n'
    '        <div id="backtest-profiles" class="backtest-profiles" role="tablist" aria-label="Версия бэктеста"></div>\n'
    '        <p id="backtest-profile-note" class="backtest-profile-note"></p>\n'
    '      </section>\n',
)
replace_once(
    "frontend/live.html",
    '  <script src="./live.js" defer></script>\n',
    '  <script src="./backtest-profiles.js" defer></script>\n'
    '  <script src="./live.js" defer></script>\n',
)

replace_once(
    "tests/frontend/test_static_assets.py",
    '            "frontend/live.js",\n',
    '            "frontend/live.js",\n'
    '            "frontend/backtest-profiles.js",\n'
    '            "frontend/backtest-profiles.css",\n',
)
replace_once(
    "tests/frontend/test_static_assets.py",
    '        script = (ROOT / "frontend/live.js").read_text(encoding="utf-8")\n',
    '        script = (ROOT / "frontend/live.js").read_text(encoding="utf-8")\n'
    '        profile_script = (ROOT / "frontend/backtest-profiles.js").read_text(encoding="utf-8")\n'
    '        profile_style = (ROOT / "frontend/backtest-profiles.css").read_text(encoding="utf-8")\n',
)
replace_once(
    "tests/frontend/test_static_assets.py",
    '        self.assertIn(\'id="backtest-trades"\', html)\n',
    '        self.assertIn(\'id="backtest-trades"\', html)\n'
    '        self.assertIn(\'id="backtest-profile-section"\', html)\n'
    '        self.assertIn(\'id="backtest-profiles"\', html)\n'
    '        self.assertIn(\'src="./backtest-profiles.js"\', html)\n'
    '        self.assertIn(\'href="./backtest-profiles.css"\', html)\n',
)
replace_once(
    "tests/frontend/test_static_assets.py",
    '        self.assertNotIn("backtest-settings", html)\n',
    '        self.assertIn("dyn-iv113-risk50", profile_script)\n'
    '        self.assertIn("dyn-iv113-band2", profile_script)\n'
    '        self.assertIn("atlas-v517-reference", profile_script)\n'
    '        self.assertIn("window.fetch = async", profile_script)\n'
    '        self.assertIn("backtest-profile.active", profile_style)\n'
    '        self.assertNotIn("/api/v1/orders", profile_script)\n'
    '        self.assertNotIn("backtest-settings", html)\n',
)
replace_once(
    ".github/workflows/frontend-control-room.yml",
    "          node --check frontend/live.js\n",
    "          node --check frontend/live.js\n"
    "          node --check frontend/backtest-profiles.js\n",
)

print("frontend backtest profiles wired")
