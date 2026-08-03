#!/usr/bin/env python3
"""Add a first-class DS-40/180 forward A/B panel to the Strategy Hub UI."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, marker: str, replacement: str, *, label: str) -> str:
    if replacement in text:
        return text
    if marker not in text:
        raise RuntimeError(f"{label}: marker not found")
    return text.replace(marker, replacement, 1)


def patch_html() -> None:
    path = ROOT / "frontend/live.html"
    text = path.read_text(encoding="utf-8")
    marker = """        <div id=\"strategy-facts\" class=\"strategy-facts\"></div>\n        <div id=\"position-body\" class=\"position-body\"></div>\n"""
    replacement = """        <div id=\"strategy-facts\" class=\"strategy-facts\"></div>\n        <section\n          id=\"strategy-ab-panel\"\n          class=\"strategy-ab-panel\"\n          aria-labelledby=\"strategy-ab-title\"\n          hidden\n        >\n          <header class=\"strategy-ab-head\">\n            <div>\n              <span class=\"eyebrow\">Forward A/B</span>\n              <h3 id=\"strategy-ab-title\">v2 против frozen v1</h3>\n            </div>\n            <span id=\"strategy-ab-status\" class=\"strategy-ab-status\">—</span>\n          </header>\n          <div\n            id=\"strategy-ab-progress\"\n            class=\"strategy-ab-progress\"\n            role=\"progressbar\"\n            aria-label=\"Накопление forward A/B наблюдений\"\n            aria-valuemin=\"0\"\n            aria-valuemax=\"90\"\n            aria-valuenow=\"0\"\n          >\n            <div class=\"strategy-ab-progress-track\"><i id=\"strategy-ab-progress-fill\"></i></div>\n            <span id=\"strategy-ab-progress-label\">0 / 90 дней</span>\n          </div>\n          <dl class=\"strategy-ab-metrics\">\n            <div><dt>V2 NAV</dt><dd id=\"strategy-ab-v2-nav\">—</dd></div>\n            <div><dt>Frozen V1 NAV</dt><dd id=\"strategy-ab-v1-nav\">—</dd></div>\n            <div><dt>Δ NAV</dt><dd id=\"strategy-ab-nav-delta\">—</dd></div>\n            <div><dt>Δ доходности</dt><dd id=\"strategy-ab-return-delta\">—</dd></div>\n            <div><dt>Δ просадки</dt><dd id=\"strategy-ab-drawdown-delta\">—</dd></div>\n            <div><dt>Δ turnover</dt><dd id=\"strategy-ab-turnover-delta\">—</dd></div>\n            <div><dt>Δ funding</dt><dd id=\"strategy-ab-funding-delta\">—</dd></div>\n            <div><dt>Gross v2 / v1</dt><dd id=\"strategy-ab-gross\">—</dd></div>\n          </dl>\n          <p id=\"strategy-ab-note\" class=\"strategy-ab-note\">Победитель автоматически не назначается.</p>\n        </section>\n        <div id=\"position-body\" class=\"position-body\"></div>\n"""
    text = replace_once(text, marker, replacement, label="live.html")
    path.write_text(text, encoding="utf-8")


def patch_js() -> None:
    path = ROOT / "frontend/live.js"
    text = path.read_text(encoding="utf-8")
    function_marker = """  const renderSelected = () => {\n"""
    function_block = r'''  const forwardAbStatusMeta = (value) => {
    const states = {
      collecting: ["Сбор данных", "collecting"],
      initial_review: ["Первичный review", "review"],
      intermediate_review: ["Промежуточный review", "review"],
      eligible_for_decision: ["Готово к разбору", "ready"],
      invalid_pair: ["Пара не совпала", "error"],
      unavailable: ["Нет данных", "error"],
    };
    return states[value] || [String(value || "Нет данных"), "error"];
  };

  const formatSignedBps = (value) => {
    const numeric = asNumber(value, Number.NaN);
    if (!Number.isFinite(numeric)) return "—";
    const bps = numeric * 10_000;
    return `${bps > 0 ? "+" : ""}${formatNumber(bps, 2)} bps`;
  };

  const formatSignedMultiple = (value) => {
    const numeric = asNumber(value, Number.NaN);
    if (!Number.isFinite(numeric)) return "—";
    return `${numeric > 0 ? "+" : ""}${formatNumber(numeric, 3)}×`;
  };

  const setForwardAbMetric = (selector, value, className = "") => {
    const element = $(selector);
    element.textContent = value;
    element.className = className;
  };

  const renderForwardAb = (strategy) => {
    const panel = $("#strategy-ab-panel");
    const comparison = strategy?.detail?.forward_ab;
    const available =
      strategy?.id === "ds40180-t50c3" &&
      comparison &&
      typeof comparison === "object";
    panel.hidden = !available;
    if (!available) return;

    const statusValue = String(comparison.status || "unavailable");
    const [statusLabel, statusClass] = forwardAbStatusMeta(statusValue);
    const status = $("#strategy-ab-status");
    status.textContent = statusLabel;
    status.className = `strategy-ab-status ${statusClass}`;

    const observations = Math.max(
      0,
      Math.round(asNumber(comparison.forwardObservationDays)),
    );
    const preferredDays = Math.max(
      1,
      Math.round(asNumber(comparison.preferredReviewDays, 90)),
    );
    const progressValue = Math.min(observations, preferredDays);
    const progressPercent = Math.min(100, (observations / preferredDays) * 100);
    const progress = $("#strategy-ab-progress");
    progress.setAttribute("aria-valuemax", String(preferredDays));
    progress.setAttribute("aria-valuenow", String(progressValue));
    $("#strategy-ab-progress-fill").style.width = `${progressPercent}%`;
    $("#strategy-ab-progress-label").textContent = `${observations} / ${preferredDays} дней`;

    const arms = comparison.arms || {};
    const v1 = arms.legacyV1Reference || {};
    const v2 = arms.forwardV2 || {};
    const deltas = comparison.deltasV2MinusV1 || {};
    const navDelta = asNumber(deltas.navUsd, Number.NaN);
    const returnDelta = asNumber(deltas.returnSinceReset, Number.NaN);
    const drawdownDelta = asNumber(deltas.maximumDrawdown, Number.NaN);
    const turnoverDelta = asNumber(deltas.turnoverToNav, Number.NaN);
    const fundingDelta = asNumber(deltas.fundingPnlUsd, Number.NaN);

    setForwardAbMetric("#strategy-ab-v2-nav", formatUsd(v2.navUsd));
    setForwardAbMetric("#strategy-ab-v1-nav", formatUsd(v1.navUsd));
    setForwardAbMetric(
      "#strategy-ab-nav-delta",
      formatUsd(navDelta, true),
      tone(navDelta),
    );
    setForwardAbMetric(
      "#strategy-ab-return-delta",
      formatSignedBps(returnDelta),
      tone(returnDelta),
    );
    setForwardAbMetric(
      "#strategy-ab-drawdown-delta",
      formatSignedBps(drawdownDelta),
      tone(drawdownDelta),
    );
    setForwardAbMetric(
      "#strategy-ab-turnover-delta",
      formatSignedMultiple(turnoverDelta),
      tone(-turnoverDelta),
    );
    setForwardAbMetric(
      "#strategy-ab-funding-delta",
      formatUsd(fundingDelta, true),
      tone(fundingDelta),
    );
    setForwardAbMetric(
      "#strategy-ab-gross",
      `${formatNumber(v2.targetGross, 3)}× / ${formatNumber(v1.targetGross, 3)}×`,
    );

    const matched = comparison.quality?.matched === true;
    const initialDays = Math.max(
      1,
      Math.round(asNumber(comparison.minimumReviewDays, 30)),
    );
    let note;
    if (!matched) {
      note = "Наблюдение исключено: даты, reset или стартовый капитал двух arms не совпали.";
    } else if (statusValue === "eligible_for_decision") {
      note = "Предпочтительное окно накоплено. Победитель не назначается автоматически — нужен ручной разбор риска и исполнения.";
    } else if (observations >= initialDays) {
      note = `Первичный review доступен; до предпочтительного окна осталось ${Math.max(0, preferredDays - observations)} дней.`;
    } else {
      note = `До первичного review осталось ${Math.max(0, initialDays - observations)} дней. Победитель автоматически не назначается.`;
    }
    $("#strategy-ab-note").textContent = note;
  };

'''
    if "const renderForwardAb = (strategy) =>" not in text:
        if function_marker not in text:
            raise RuntimeError("live.js: renderSelected marker not found")
        text = text.replace(function_marker, function_block + function_marker, 1)

    call_marker = """    const body = $(\"#position-body\");\n"""
    call_replacement = """    renderForwardAb(strategy);\n\n    const body = $(\"#position-body\");\n"""
    text = replace_once(text, call_marker, call_replacement, label="live.js call")
    path.write_text(text, encoding="utf-8")


def patch_css() -> None:
    path = ROOT / "frontend/live.css"
    text = path.read_text(encoding="utf-8")
    if ".strategy-ab-panel {" in text:
        return
    block = r'''

/* DS-40/180 forward A/B observatory */
.strategy-ab-panel {
  padding: 17px 20px 18px;
  border-bottom: 1px solid var(--border);
  background:
    linear-gradient(145deg, rgba(106, 168, 255, 0.08), transparent 55%),
    var(--panel-soft);
}
.strategy-ab-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
}
.strategy-ab-head h3 { margin: 3px 0 0; font-size: 13px; letter-spacing: -0.015em; }
.strategy-ab-status {
  flex: 0 0 auto;
  padding: 5px 8px;
  border: 1px solid var(--border-bright);
  border-radius: 999px;
  color: var(--muted);
  font: 700 8px var(--mono);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.strategy-ab-status.collecting { color: var(--blue); border-color: rgba(106, 168, 255, 0.3); background: rgba(106, 168, 255, 0.08); }
.strategy-ab-status.review { color: var(--amber); border-color: rgba(234, 184, 95, 0.3); background: rgba(234, 184, 95, 0.08); }
.strategy-ab-status.ready { color: var(--green); border-color: rgba(66, 214, 155, 0.3); background: var(--green-soft); }
.strategy-ab-status.error { color: var(--red); border-color: rgba(240, 111, 126, 0.3); background: var(--red-soft); }
.strategy-ab-progress { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 10px; margin-top: 13px; }
.strategy-ab-progress-track { height: 6px; overflow: hidden; border-radius: 999px; background: rgba(255, 255, 255, 0.06); }
.strategy-ab-progress-track i { display: block; width: 0; height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--blue), var(--green)); transition: width 220ms ease; }
.strategy-ab-progress > span { color: var(--muted); font: 700 9px var(--mono); white-space: nowrap; }
.strategy-ab-metrics { display: grid; grid-template-columns: 1fr 1fr; margin: 14px 0 0; border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
.strategy-ab-metrics div { min-width: 0; padding: 10px 11px; border-right: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.strategy-ab-metrics div:nth-child(even) { border-right: 0; }
.strategy-ab-metrics div:nth-last-child(-n + 2) { border-bottom: 0; }
.strategy-ab-metrics dt { color: var(--muted); font-size: 8px; }
.strategy-ab-metrics dd { overflow: hidden; margin: 4px 0 0; font: 650 10px var(--mono); text-overflow: ellipsis; white-space: nowrap; }
.strategy-ab-note { margin: 12px 0 0; color: #aeb9c0; font-size: 9px; line-height: 1.5; }

@media (max-width: 560px) {
  .strategy-ab-panel { padding-inline: 15px; }
  .strategy-ab-head { align-items: flex-start; }
  .strategy-ab-metrics { grid-template-columns: 1fr; }
  .strategy-ab-metrics div,
  .strategy-ab-metrics div:nth-child(even),
  .strategy-ab-metrics div:nth-last-child(-n + 2) {
    border-right: 0;
    border-bottom: 1px solid var(--border);
  }
  .strategy-ab-metrics div:last-child { border-bottom: 0; }
}
'''
    path.write_text(text.rstrip() + block + "\n", encoding="utf-8")


def write_tests() -> None:
    path = ROOT / "tests/frontend/test_ds40180_ab_panel.py"
    path.write_text(
        '''from __future__ import annotations

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
''',
        encoding="utf-8",
    )


def main() -> None:
    patch_html()
    patch_js()
    patch_css()
    write_tests()
    print("DS-40/180 frontend A/B panel applied")


if __name__ == "__main__":
    main()
