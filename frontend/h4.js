(() => {
  "use strict";

  const SUMMARY_URL = "./data/h4-summary.json";
  const TRADE_URLS = ["./data/h4-trades-1.json", "./data/h4-trades-2.json"];
  const state = { data: null, scenario: "severe" };
  const $ = (selector) => document.querySelector(selector);
  const create = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const number = (value, fallback = 0) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  };
  const pct = (value, digits = 2, signed = false) => {
    const numeric = number(value, Number.NaN);
    if (!Number.isFinite(numeric)) return "—";
    return `${signed && numeric > 0 ? "+" : ""}${(numeric * 100).toLocaleString("ru-RU", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    })}%`;
  };
  const fixed = (value, digits = 2) => {
    const numeric = number(value, Number.NaN);
    return Number.isFinite(numeric)
      ? numeric.toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits })
      : "—";
  };
  const tone = (value) => number(value) > 0 ? "positive" : number(value) < 0 ? "negative" : "neutral";

  const expandRows = (columns, rows) =>
    (rows || []).map((row) => Object.fromEntries(columns.map((column, index) => [column, row[index]])));

  const decodeBundle = (raw, tradeParts) => ({
    schema_version: raw.v,
    generated_at: raw.generated,
    exchange_submission_available: raw.orders,
    live_ready: raw.live,
    scenarios: expandRows(raw.scenario_columns, raw.scenarios),
    curves: Object.fromEntries(
      Object.entries(raw.curves || {}).map(([name, rows]) => [name, expandRows(raw.curve_columns, rows)])
    ),
    leverage_sensitivity: expandRows(raw.leverage_columns, raw.leverage),
    periodic_returns: expandRows(raw.period_columns, raw.periods).map((item) => ({ ...item, scenario: "severe" })),
    bootstrap: expandRows(raw.bootstrap_columns, raw.bootstrap),
    trades: tradeParts.flatMap((part) => expandRows(part.trade_columns, part.trades)).map((item) => ({
      ...item,
      side: item.side === "S" ? "SHORT" : "LONG",
      tier: item.tier === "C" ? "CORE" : "SATELLITE",
    })),
    attribution: expandRows(raw.attribution_columns, raw.attribution),
    frequency: raw.frequency || {},
    leverage_distribution: raw.leverage_distribution || {},
    profile: {
      profile: raw.profile || {},
      risk: {
        core_group_stop_risk_cap: raw.profile?.group_risk_cap,
        total_committed_stop_risk_cap: raw.profile?.total_risk_cap,
        gross_leverage_cap: raw.profile?.gross_leverage_cap,
      },
      signal_architecture: {
        entry: "next hourly open after completed-bar signal",
      },
    },
    forward_test: {
      gross_leverage_cap: raw.forward_test?.gross_leverage_cap,
      core_group_stop_risk_cap: raw.forward_test?.group_stop_risk_cap,
      satellite_group_stop_risk_cap: raw.forward_test?.satellite_group_stop_risk_cap,
      total_committed_stop_risk_cap: raw.forward_test?.total_committed_stop_risk_cap,
      parameters_frozen: raw.forward_test?.parameters_frozen === true,
    },
  });

  const scenario = (name = state.scenario) =>
    state.data?.scenarios?.find((item) => item.scenario === name) || null;

  const metricCard = (label, value, note, className = "") => {
    const card = create("article", `metric-card ${className}`.trim());
    card.append(create("span", "", label), create("strong", "", value), create("small", "", note));
    return card;
  };

  const renderMetrics = () => {
    const current = scenario();
    const container = $("#headline-metrics");
    container.replaceChildren(
      metricCard(`${state.scenario} CAGR`, pct(current?.cagr), "research OOS 2025", "positive"),
      metricCard("Profit Factor", fixed(current?.profit_factor, 3), "после modeled costs", "positive"),
      metricCard("Win rate", pct(current?.win_rate), `${current?.opened_sleeves || 0} позиций`),
      metricCard("Max drawdown", pct(-number(current?.max_drawdown)), "по часовой equity", "risk"),
      metricCard("Сделок в день", fixed(current?.independent_entries_per_day, 3), "независимые входы"),
      metricCard("Пиковое плечо", `${fixed(current?.peak_gross_leverage, 2)}×`, "cap 10×", "risk")
    );
  };

  const renderScenarioTabs = () => {
    const tabs = $("#scenario-tabs");
    tabs.replaceChildren();
    const order = ["stress", "severe", "extreme", "catastrophic"];
    for (const name of order) {
      if (!scenario(name)) continue;
      const button = create("button", name === state.scenario ? "active" : "", name);
      button.type = "button";
      button.setAttribute("role", "tab");
      button.setAttribute("aria-selected", name === state.scenario ? "true" : "false");
      button.addEventListener("click", () => {
        state.scenario = name;
        render();
      });
      tabs.append(button);
    }
  };

  const svg = (name, attrs = {}) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    return node;
  };

  const pathFor = (points, x, y) => points.map((point, index) => `${index ? "L" : "M"}${x(index).toFixed(2)} ${y(point).toFixed(2)}`).join(" ");

  const renderChart = () => {
    const points = state.data?.curves?.[state.scenario] || [];
    const chart = $("#equity-chart");
    chart.replaceChildren();
    if (points.length < 2) return;
    const width = 960;
    const height = 390;
    const left = 54;
    const right = 22;
    const top = 18;
    const split = 270;
    const bottom = 28;
    const plotWidth = width - left - right;
    const equityValues = points.map((item) => number(item.equity));
    const drawdownValues = points.map((item) => number(item.drawdown));
    const minEquity = Math.min(...equityValues);
    const maxEquity = Math.max(...equityValues);
    const equityRange = maxEquity - minEquity || 1;
    const maxDrawdown = Math.max(...drawdownValues, 0.01);
    const x = (index) => left + (index / (points.length - 1)) * plotWidth;
    const yEquity = (value) => top + ((maxEquity - value) / equityRange) * (split - top - 16);
    const yDrawdown = (value) => split + 18 + (value / maxDrawdown) * (height - bottom - split - 22);

    for (let i = 0; i <= 4; i += 1) {
      const y = top + ((split - top - 16) / 4) * i;
      chart.append(svg("line", { x1: left, y1: y, x2: width - right, y2: y, class: "chart-grid" }));
      const label = svg("text", { x: 8, y: y + 4, class: "chart-axis" });
      label.textContent = `${fixed(maxEquity - (equityRange / 4) * i, 2)}×`;
      chart.append(label);
    }
    chart.append(svg("line", { x1: left, y1: split, x2: width - right, y2: split, class: "chart-grid" }));

    const equityPath = pathFor(equityValues, x, yEquity);
    const equityArea = `${equityPath} L${x(points.length - 1)} ${split - 16} L${x(0)} ${split - 16} Z`;
    chart.append(svg("path", { d: equityArea, class: "equity-area" }), svg("path", { d: equityPath, class: "equity-line" }));

    const drawdownPath = pathFor(drawdownValues, x, yDrawdown);
    const drawdownArea = `M${x(0)} ${split + 18} ${drawdownPath.slice(1)} L${x(points.length - 1)} ${split + 18} Z`;
    chart.append(svg("path", { d: drawdownArea, class: "drawdown-area" }), svg("path", { d: drawdownPath, class: "drawdown-line" }));

    for (const index of [0, Math.floor((points.length - 1) / 2), points.length - 1]) {
      const label = svg("text", { x: x(index), y: height - 6, class: "chart-axis", "text-anchor": index === 0 ? "start" : index === points.length - 1 ? "end" : "middle" });
      label.textContent = points[index].date;
      chart.append(label);
    }
    const ddLabel = svg("text", { x: 8, y: split + 30, class: "chart-axis" });
    ddLabel.textContent = "DD";
    chart.append(ddLabel);
    $("#chart-window").textContent = `${points[0].date} → ${points.at(-1).date}`;
    $("#chart-ending").textContent = `Финал ${fixed(points.at(-1).equity, 3)}× · DD ${pct(-points.at(-1).drawdown)}`;
  };

  const addFact = (container, label, value) => {
    const row = create("div");
    row.append(create("dt", "", label), create("dd", "", value));
    container.append(row);
  };

  const renderProfile = () => {
    const profile = state.data?.profile || {};
    const config = profile.profile || {};
    const risk = profile.risk || {};
    const architecture = profile.signal_architecture || {};
    const facts = $("#profile-facts");
    facts.replaceChildren();
    addFact(facts, "Meta core", `p ≥ ${fixed(config.core_threshold, 2)}`);
    addFact(facts, "Satellite", `${fixed(config.short_floor, 2)} ≤ p < ${fixed(config.core_threshold, 2)}`);
    addFact(facts, "Router", "top‑1 на группу");
    addFact(facts, "Long reversal", "EMA200 · ret24 · RSI(5)");
    addFact(facts, "Core risk cap", pct(risk.core_group_stop_risk_cap));
    addFact(facts, "Total risk cap", pct(risk.total_committed_stop_risk_cap));
    addFact(facts, "Gross leverage cap", `${fixed(risk.gross_leverage_cap, 0)}×`);
    addFact(facts, "Вход", architecture.entry || "next open");

    const forward = state.data?.forward_test || {};
    const forwardFacts = $("#forward-facts");
    forwardFacts.replaceChildren();
    addFact(forwardFacts, "Gross cap", `${fixed(forward.gross_leverage_cap, 0)}×`);
    addFact(forwardFacts, "Core risk", pct(forward.core_group_stop_risk_cap));
    addFact(forwardFacts, "Satellite risk", pct(forward.satellite_group_stop_risk_cap, 3));
    addFact(forwardFacts, "Total risk", pct(forward.total_committed_stop_risk_cap));
    addFact(forwardFacts, "Параметры", forward.parameters_frozen ? "заморожены" : "не заморожены");
  };

  const renderScenarioRows = () => {
    const body = $("#scenario-rows");
    body.replaceChildren();
    for (const item of state.data?.scenarios || []) {
      const row = create("tr", item.scenario === state.scenario ? "active-scenario" : "");
      const values = [
        item.scenario,
        pct(item.cagr),
        fixed(item.profit_factor, 3),
        pct(item.win_rate),
        pct(-item.max_drawdown),
        `${fixed(item.peak_gross_leverage, 2)}×`,
        fixed(item.independent_entries_per_day, 3),
      ];
      values.forEach((value, index) => {
        const cell = create("td", index === 1 ? tone(item.cagr) : index === 4 ? "negative" : "", value);
        row.append(cell);
      });
      row.addEventListener("click", () => {
        if (state.data.curves?.[item.scenario]) {
          state.scenario = item.scenario;
          render();
        }
      });
      body.append(row);
    }
  };

  const renderMonthly = () => {
    const months = (state.data?.periodic_returns || []).filter((item) => item.scenario === "severe" && item.frequency === "month");
    const max = Math.max(...months.map((item) => Math.abs(number(item.return))), 0.01);
    const container = $("#monthly-bars");
    container.replaceChildren();
    for (const item of months) {
      const value = number(item.return);
      const row = create("div", `month-row ${tone(value)}`);
      const track = create("div", "month-track");
      const bar = create("i");
      bar.style.width = `${Math.max(2, Math.abs(value) / max * 100)}%`;
      track.append(bar);
      row.append(create("span", "", item.period.slice(0, 7)), track, create("strong", "", pct(value, 2, true)));
      container.append(row);
    }
    const positive = months.filter((item) => number(item.return) > 0).length;
    $("#positive-months").textContent = `${positive}/${months.length} положительных`;
  };

  const renderAttribution = () => {
    const rows = (state.data?.attribution || []).filter((item) => item.dimension === "tier");
    const container = $("#attribution-cards");
    container.replaceChildren();
    for (const item of rows) {
      const card = create("article", "attribution-card");
      const list = create("dl");
      for (const [label, value] of [
        ["Сделок", String(item.trades)],
        ["Win rate", pct(item.win_rate)],
        ["Profit factor", fixed(item.profit_factor, 3)],
        ["Net contribution", pct(item.net_pnl_sum, 2, true)],
      ]) {
        const row = create("div");
        row.append(create("dt", "", label), create("dd", "", value));
        list.append(row);
      }
      card.append(create("span", "", item.value), create("strong", tone(item.net_pnl_sum), pct(item.net_pnl_sum, 2, true)), list);
      container.append(card);
    }
  };

  const renderTrades = () => {
    const rows = [...(state.data?.trades || [])].reverse();
    const body = $("#trade-rows");
    body.replaceChildren();
    for (const item of rows) {
      const row = create("tr");
      const values = [
        item.entry_time,
        item.side,
        item.tier,
        fixed(item.meta_probability, 3),
        `${fixed(item.notional_multiple, 2)}×`,
        item.exit_reason,
        String(item.holding_hours),
        pct(item.net_pnl_fraction, 3, true),
      ];
      values.forEach((value, index) => {
        const cell = create("td", index === 7 ? tone(item.net_pnl_fraction) : "");
        if (index === 2) cell.append(create("span", `tier ${String(item.tier).toLowerCase()}`, value));
        else cell.textContent = value;
        row.append(cell);
      });
      body.append(row);
    }
    $("#trade-count").textContent = `${rows.length} позиций · ${fixed(state.data?.frequency?.positions_per_day, 3)}/день`;
  };

  const render = () => {
    renderMetrics();
    renderScenarioTabs();
    renderChart();
    renderProfile();
    renderScenarioRows();
    renderMonthly();
    renderAttribution();
    renderTrades();
  };

  const load = async () => {
    try {
      const responses = await Promise.all([SUMMARY_URL, ...TRADE_URLS].map((url) =>
        fetch(url, { cache: "no-store" })
      ));
      const failed = responses.find((response) => !response.ok);
      if (failed) throw new Error(`HTTP ${failed.status}`);
      const [raw, ...tradeParts] = await Promise.all(responses.map((response) => response.json()));
      if (raw.v !== 1 || raw.orders !== false || raw.live !== false) {
        throw new Error("unsafe or unsupported H4 evidence");
      }
      if (tradeParts.some((part) => part.v !== 1)) throw new Error("unsupported H4 trade evidence");
      state.data = decodeBundle(raw, tradeParts);
      $("#data-status").className = "status ready";
      $("#data-status").lastChild.textContent = "Evidence загружен";
      $("#generated-at").textContent = `Собрано ${state.data.generated_at || "—"}`;
      render();
    } catch (error) {
      $("#data-status").className = "status error";
      $("#data-status").lastChild.textContent = "Ошибка загрузки";
      $("#generated-at").textContent = String(error);
    }
  };

  load();
})();
