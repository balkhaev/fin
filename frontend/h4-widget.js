(() => {
  "use strict";
  const root = document.querySelector("#h4-research-widget");
  if (!root) return;
  const number = (value, fallback = 0) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  };
  const pct = (value, digits = 2) => `${(number(value) * 100).toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`;
  const fixed = (value, digits = 2) => number(value).toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const metric = (label, value, primary = false) => `<div class="h4-widget-metric${primary ? " primary" : ""}"><span>${label}</span><strong>${value}</strong></div>`;

  fetch("./data/h4-cagr50.json", { cache: "no-store" })
    .then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then((data) => {
      if (data.v !== 1 || data.orders !== false || data.live !== false) {
        throw new Error("unsafe or unsupported H4 evidence");
      }
      const columns = data.scenario_columns || [];
      const severeRow = (data.scenarios || []).find((item) => item[0] === "severe");
      const severe = Object.fromEntries(columns.map((column, index) => [column, severeRow?.[index]]));
      root.innerHTML = `
        <div class="h4-widget-head">
          <div>
            <span class="h4-widget-kicker">New research profile · paper only</span>
            <h2 class="h4-widget-title">H4 CAGR50 Frequency</h2>
            <p class="h4-widget-copy">BTCUSDT USD‑M, top‑1 router и severe execution. Карточка показывает committed research evidence; биржевые ордера не включены.</p>
          </div>
          <a class="h4-widget-link" href="./h4.html">Открыть H4 dashboard <b aria-hidden="true">↗</b></a>
        </div>
        <div class="h4-widget-metrics">
          ${metric("Severe CAGR", pct(severe.cagr), true)}
          ${metric("Profit Factor", fixed(severe.profit_factor, 3))}
          ${metric("Win rate", pct(severe.win_rate))}
          ${metric("Max DD", pct(-severe.max_drawdown))}
          ${metric("Сделок/день", fixed(severe.independent_entries_per_day, 3))}
          ${metric("Плечо max", `${fixed(severe.peak_gross_leverage, 2)}×`)}
        </div>
        <div class="h4-widget-foot">
          <span>Research OOS: 2025 · 4/4 положительных квартала</span>
          <span>Не pristine holdout · live_ready=false · exchange submission unavailable</span>
        </div>`;
    })
    .catch((error) => {
      root.innerHTML = `<div class="h4-widget-loading">H4 evidence недоступен: ${String(error)}</div>`;
    });
})();
