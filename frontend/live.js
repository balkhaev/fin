(() => {
  "use strict";

  const state = {
    data: null,
    selectedAsset: null,
    selectedVenue: null,
    eventSource: null,
    fetching: false,
    fallbackTimer: null,
  };
  const svgNamespace = "http://www.w3.org/2000/svg";
  const $ = (selector) => document.querySelector(selector);
  const asNumber = (value, fallback = 0) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  };
  const formatNumber = (value, digits = 2) =>
    Number.isFinite(Number(value))
      ? asNumber(value).toLocaleString("ru-RU", {
          minimumFractionDigits: digits,
          maximumFractionDigits: digits,
        })
      : "—";
  const formatUsd = (value, signed = false) => {
    if (!Number.isFinite(Number(value))) return "—";
    const numeric = asNumber(value);
    const prefix = signed && numeric > 0 ? "+" : "";
    return `${prefix}${formatNumber(numeric, 2)} USDT`;
  };
  const formatCompact = (value) => {
    const numeric = asNumber(value, Number.NaN);
    if (!Number.isFinite(numeric)) return "—";
    return new Intl.NumberFormat("ru-RU", {
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(numeric);
  };
  const formatPrice = (value) => {
    const numeric = asNumber(value, Number.NaN);
    if (!Number.isFinite(numeric)) return "—";
    const digits = numeric >= 1000 ? 2 : numeric >= 1 ? 3 : 5;
    return `$${formatNumber(numeric, digits)}`;
  };
  const formatTime = (timestamp) => {
    if (!Number.isFinite(Number(timestamp))) return "—";
    return new Date(Number(timestamp)).toLocaleTimeString("ru-RU", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  };
  const formatDuration = (milliseconds) => {
    const seconds = Math.max(0, Math.floor(asNumber(milliseconds) / 1000));
    if (seconds < 60) return `${seconds} сек`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes} мин`;
    return `${formatNumber(minutes / 60, 1)} ч`;
  };
  const tone = (value) => (asNumber(value) > 0 ? "positive" : asNumber(value) < 0 ? "negative" : "neutral");
  const create = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const svg = (tag, attributes = {}) => {
    const element = document.createElementNS(svgNamespace, tag);
    for (const [key, value] of Object.entries(attributes)) {
      element.setAttribute(key, String(value));
    }
    return element;
  };

  const setConnection = (status, label) => {
    const pill = $("#connection-pill");
    pill.className = `connection ${status}`;
    pill.lastChild.textContent = label;
  };

  const renderAccount = (data) => {
    const paper = data.paper || {};
    const equity = asNumber(paper.equity_usdt);
    const starting = asNumber(paper.starting_balance_usdt);
    const realized = asNumber(paper.realized_pnl_usdt);
    const unrealized = asNumber(paper.unrealized_pnl_usdt);
    const total = equity - starting;
    const percent = starting ? (total / starting) * 100 : 0;
    $("#equity").textContent = formatNumber(equity, 2);
    $("#total-pnl").textContent = formatUsd(total, true);
    $("#unrealized-pnl").textContent = formatUsd(unrealized, true);
    $("#realized-pnl").textContent = formatUsd(realized, true);
    $("#closed-trades").textContent = String(asNumber(paper.closed_positions));
    for (const [selector, value] of [
      ["#total-pnl", total],
      ["#unrealized-pnl", unrealized],
      ["#realized-pnl", realized],
    ]) {
      const element = $(selector);
      element.className = tone(value);
    }
    const chip = $("#pnl-chip");
    chip.textContent = `${percent > 0 ? "+" : ""}${formatNumber(percent, 2)}%`;
    chip.className = `pnl-chip ${tone(percent)}`;
  };

  const renderTabs = (container, values, selected, onSelect) => {
    container.replaceChildren();
    for (const value of values) {
      const button = create("button", value === selected ? "active" : "", value.toUpperCase());
      button.type = "button";
      button.addEventListener("click", () => onSelect(value));
      container.append(button);
    }
  };

  const renderChart = (data) => {
    const series = (data.candles || []).filter((item) => Array.isArray(item.items) && item.items.length > 1);
    const assets = [...new Set(series.map((item) => String(item.asset)))];
    if (!assets.includes(state.selectedAsset)) state.selectedAsset = assets[0] || null;
    const assetSeries = series.filter((item) => item.asset === state.selectedAsset);
    const venues = assetSeries.map((item) => String(item.exchange_id));
    if (!venues.includes(state.selectedVenue)) state.selectedVenue = venues[0] || null;

    renderTabs($("#asset-tabs"), assets, state.selectedAsset, (asset) => {
      state.selectedAsset = asset;
      state.selectedVenue = null;
      renderChart(state.data);
    });
    renderTabs($("#venue-tabs"), venues, state.selectedVenue, (venue) => {
      state.selectedVenue = venue;
      renderChart(state.data);
    });

    const selected = assetSeries.find((item) => item.exchange_id === state.selectedVenue);
    const chart = $("#candle-chart");
    chart.replaceChildren();
    if (!selected) {
      $("#chart-empty").hidden = false;
      $("#chart-title").textContent = "Рынок";
      return;
    }
    $("#chart-empty").hidden = true;
    const candles = selected.items.slice(-120);
    const lows = candles.map((item) => asNumber(item.low));
    const highs = candles.map((item) => asNumber(item.high));
    const rawMin = Math.min(...lows);
    const rawMax = Math.max(...highs);
    const padding = Math.max((rawMax - rawMin) * 0.08, rawMax * 0.0005);
    const minimum = rawMin - padding;
    const maximum = rawMax + padding;
    const width = 960;
    const height = 360;
    const left = 10;
    const right = 82;
    const top = 14;
    const bottom = 30;
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;
    const xStep = plotWidth / candles.length;
    const bodyWidth = Math.max(1.5, Math.min(7, xStep * 0.62));
    const y = (price) => top + ((maximum - price) / (maximum - minimum)) * plotHeight;

    for (let index = 0; index <= 4; index += 1) {
      const gridY = top + (plotHeight / 4) * index;
      const price = maximum - ((maximum - minimum) / 4) * index;
      chart.append(svg("line", { x1: left, y1: gridY, x2: width - right, y2: gridY, class: "chart-grid" }));
      const label = svg("text", { x: width - right + 10, y: gridY + 4, class: "chart-axis" });
      label.textContent = formatPrice(price);
      chart.append(label);
    }

    for (const [index, candle] of candles.entries()) {
      const center = left + xStep * index + xStep / 2;
      const candleClass = asNumber(candle.close) >= asNumber(candle.open) ? "candle-up" : "candle-down";
      chart.append(svg("line", {
        x1: center,
        y1: y(asNumber(candle.high)),
        x2: center,
        y2: y(asNumber(candle.low)),
        class: candleClass,
      }));
      const openY = y(asNumber(candle.open));
      const closeY = y(asNumber(candle.close));
      chart.append(svg("rect", {
        x: center - bodyWidth / 2,
        y: Math.min(openY, closeY),
        width: bodyWidth,
        height: Math.max(1, Math.abs(closeY - openY)),
        rx: 0.7,
        class: candleClass,
      }));
    }

    const timeIndexes = [0, Math.floor((candles.length - 1) / 2), candles.length - 1];
    for (const index of timeIndexes) {
      const label = svg("text", {
        x: left + xStep * index + xStep / 2,
        y: height - 7,
        class: "chart-axis",
        "text-anchor": index === 0 ? "start" : index === candles.length - 1 ? "end" : "middle",
      });
      label.textContent = new Date(candles[index].timestamp_ms).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
      chart.append(label);
    }

    const first = asNumber(candles[0].open);
    const last = asNumber(candles.at(-1).close);
    const change = first ? ((last / first) - 1) * 100 : 0;
    const lastY = y(last);
    chart.append(svg("line", { x1: left, y1: lastY, x2: width - right, y2: lastY, class: "last-line" }));
    const lastLabel = svg("text", { x: width - right + 10, y: lastY - 6, class: "last-label" });
    lastLabel.textContent = formatPrice(last);
    chart.append(lastLabel);

    $("#chart-title").textContent = `${selected.asset} / USDT · ${selected.exchange_id}`;
    $("#chart-last").textContent = formatPrice(last);
    $("#chart-change").textContent = `${change > 0 ? "+" : ""}${formatNumber(change, 2)}%`;
    $("#chart-change").className = tone(change);
    $("#chart-low").textContent = formatPrice(rawMin);
    $("#chart-high").textContent = formatPrice(rawMax);
    $("#chart-range").textContent = `${selected.timeframe} · ${candles.length} реальных свечей`;
  };

  const renderPosition = (data) => {
    const container = $("#position-body");
    const status = $("#position-status");
    container.replaceChildren();
    const position = data.paper?.open_position;
    if (!position) {
      status.textContent = "Ожидание";
      status.className = "position-status waiting";
      const card = create("div", "waiting-card");
      const inner = create("div");
      inner.append(create("span", "waiting-icon", "⌁"));
      inner.append(create("strong", "", "Нет открытой позиции"));
      inner.append(create("p", "", "Стратегия наблюдает за funding-спредами и войдёт только при прохождении всех risk-фильтров."));
      inner.append(create("div", "threshold", `Порог входа: ${formatNumber(data.risk?.min_current_spread_bps_8h, 2)} bps / 8ч`));
      card.append(inner);
      container.append(card);
      return;
    }
    status.textContent = "Открыта";
    status.className = "position-status open";
    const candidate = position.candidate || {};
    const pair = create("div", "position-pair");
    const long = create("div", "leg long");
    long.append(create("span", "", "Long"), create("strong", "", `${candidate.long_exchange} · ${candidate.asset}`));
    const short = create("div", "leg short");
    short.append(create("span", "", "Short"), create("strong", "", `${candidate.short_exchange} · ${candidate.asset}`));
    pair.append(long, create("span", "pair-arrow", "↔"), short);
    container.append(pair);
    const kpis = create("div", "position-kpis");
    const values = [
      ["Paper PnL", formatUsd(position.funding_pnl_usdt + position.mark_pnl_usdt - position.charged_costs_usdt, true)],
      ["Mark PnL", formatUsd(position.mark_pnl_usdt, true)],
      ["Funding PnL", formatUsd(position.funding_pnl_usdt, true)],
      ["Издержки", formatUsd(position.charged_costs_usdt)],
      ["Номинал", formatUsd(candidate.matched_notional_usdt)],
      ["В позиции", formatDuration(Date.now() - asNumber(position.opened_at_ms))],
    ];
    for (const [label, value] of values) {
      const item = create("div");
      item.append(create("span", "", label), create("strong", "", value));
      kpis.append(item);
    }
    container.append(kpis);
  };

  const renderMarkets = (data) => {
    const body = $("#market-rows");
    body.replaceChildren();
    for (const market of data.markets || []) {
      const row = document.createElement("tr");
      const nameCell = document.createElement("td");
      const name = create("div", "market-name");
      name.append(create("span", "asset-icon", String(market.asset).slice(0, 3)));
      const labels = create("span");
      labels.append(create("strong", "", `${market.asset} / USDT`), create("small", "", market.exchange_id));
      name.append(labels);
      nameCell.append(name);
      row.append(nameCell);
      const cells = [
        formatPrice(market.mark_price),
        `${formatNumber(market.funding_bps_8h, 3)} bps`,
        `${formatNumber(market.predicted_funding_bps_8h, 3)} bps`,
        `${formatNumber(market.book_spread_bps, 3)} bps`,
        `$${formatCompact(market.open_interest_usdt)}`,
      ];
      for (const value of cells) row.append(create("td", "", value));
      const freshness = document.createElement("td");
      freshness.append(create("span", "fresh", "live"));
      row.append(freshness);
      body.append(row);
    }
    const scan = data.scan || {};
    $("#scan-meta").textContent = `${(data.markets || []).length} рынков · ${asNumber(scan.candidates?.length)} сигналов`;
  };

  const rejectionText = (item) => {
    const spread = item.details?.current_spread_bps_8h;
    const translations = {
      current_spread_below_threshold: "Funding-спред пока ниже порога входа",
      predicted_spread_below_threshold: "Прогноз funding не подтверждает вход",
      entry_basis_too_wide: "Basis между биржами слишком широкий",
      expected_net_below_threshold: "Ожидаемый результат не покрывает издержки",
      insufficient_order_book_depth: "Недостаточная глубина книги ордеров",
      mark_divergence_too_wide: "Расхождение mark price выше лимита",
    };
    const base = translations[item.reason] || String(item.reason || "Сигнал отклонён risk-фильтром");
    return Number.isFinite(Number(spread)) ? `${base}: ${formatNumber(spread, 3)} bps / 8ч.` : `${base}.`;
  };

  const renderDecisions = (data) => {
    const container = $("#decision-list");
    container.replaceChildren();
    const scan = data.scan || {};
    const now = data.updated_at_ms;
    const entries = [];
    if (data.paper?.open_position) {
      const candidate = data.paper.open_position.candidate || {};
      entries.push({ title: `${candidate.asset} · позиция открыта`, text: `${candidate.long_exchange} long / ${candidate.short_exchange} short`, time: now });
    }
    for (const candidate of scan.candidates || []) {
      entries.push({ title: `${candidate.asset} · найден вход`, text: `Ожидаемый net: ${formatNumber(candidate.expected_net_bps, 2)} bps`, time: scan.observed_at_ms });
    }
    const seen = new Set();
    for (const rejection of scan.rejections || []) {
      if (seen.has(rejection.asset)) continue;
      seen.add(rejection.asset);
      entries.push({ title: `${rejection.asset} · ждём`, text: rejectionText(rejection), time: scan.observed_at_ms });
    }
    for (const entry of entries.slice(0, 3)) {
      const card = create("article", "decision");
      const top = create("div", "decision-top");
      top.append(create("strong", "", entry.title), create("time", "", formatTime(entry.time)));
      card.append(top, create("p", "", entry.text));
      container.append(card);
    }
    if (!container.childElementCount) {
      container.append(create("article", "decision", "Ждём первый market scan…"));
    }
  };

  const render = (data) => {
    state.data = data;
    renderAccount(data);
    renderChart(data);
    renderPosition(data);
    renderMarkets(data);
    renderDecisions(data);
    $("#updated-at").textContent = `обновлено ${formatTime(data.updated_at_ms)}`;
    const errors = [...(data.scan?.errors || [])];
    if (!(data.candles || []).length) errors.push(...(data.candle_errors || []));
    const banner = $("#error-banner");
    banner.hidden = errors.length === 0;
    banner.textContent = errors.length ? `Market data: ${errors.join(" · ")}` : "";
    setConnection(data.health === "healthy" ? "live" : "error", data.health === "healthy" ? "Realtime" : "Данные устарели");
  };

  const fetchPaper = async () => {
    if (state.fetching) return;
    state.fetching = true;
    try {
      const response = await fetch("/api/v1/paper", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      render(await response.json());
    } catch (error) {
      setConnection("error", "Нет связи");
      const banner = $("#error-banner");
      banner.hidden = false;
      banner.textContent = `Не удалось получить paper-данные: ${error.message}`;
    } finally {
      state.fetching = false;
    }
  };

  const connect = () => {
    state.eventSource?.close();
    const source = new EventSource("/api/v1/events?seconds=300");
    state.eventSource = source;
    source.addEventListener("snapshot", fetchPaper);
    source.onopen = () => setConnection("live", "Realtime");
    source.onerror = () => {
      if (!state.data) setConnection("error", "Переподключение");
    };
    clearInterval(state.fallbackTimer);
    state.fallbackTimer = setInterval(fetchPaper, 10_000);
  };

  $("#refresh").addEventListener("click", fetchPaper);
  window.addEventListener("beforeunload", () => {
    state.eventSource?.close();
    clearInterval(state.fallbackTimer);
  });
  fetchPaper();
  connect();
})();
