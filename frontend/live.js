(() => {
  "use strict";

  const state = {
    funding: null,
    hub: null,
    selectedStrategyId: "funding-neutral",
    selectedAsset: null,
    selectedVenue: null,
    chartCandleCount: 60,
    chartKeyboardIndex: null,
    chartInteractionController: null,
    resizeFrame: null,
    socket: null,
    reconnectTimer: null,
    reconnectAttempts: 0,
    unloading: false,
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
    return `${signed && numeric > 0 ? "+" : ""}${formatNumber(numeric, 2)} USDT`;
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
  const tone = (value) =>
    asNumber(value) > 0 ? "positive" : asNumber(value) < 0 ? "negative" : "neutral";
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

  const selectedStrategy = () => {
    const strategies = state.hub?.strategies || [];
    return strategies.find((item) => item.id === state.selectedStrategyId) || strategies[0];
  };

  const renderAccount = () => {
    const summary = state.hub?.summary || {};
    const equity = asNumber(summary.paper_equity_usdt);
    const starting = asNumber(summary.paper_starting_balance_usdt);
    const pnl = asNumber(summary.paper_pnl_usdt);
    const percent = starting ? (pnl / starting) * 100 : 0;
    $("#equity").textContent = formatNumber(equity, 2);
    $("#total-pnl").textContent = formatUsd(pnl, true);
    $("#total-pnl").className = tone(pnl);
    $("#strategy-count").textContent = String(asNumber(summary.strategy_count));
    $("#running-count").textContent = String(asNumber(summary.running_count));
    $("#open-positions").textContent = String(asNumber(summary.open_positions));
    const chip = $("#pnl-chip");
    chip.textContent = `${percent > 0 ? "+" : ""}${formatNumber(percent, 2)}%`;
    chip.className = `pnl-chip ${tone(percent)}`;
  };

  const renderStrategies = () => {
    const container = $("#strategy-tabs");
    container.replaceChildren();
    for (const strategy of state.hub?.strategies || []) {
      const button = create(
        "button",
        `strategy-card${strategy.id === state.selectedStrategyId ? " active" : ""}`
      );
      button.type = "button";
      button.setAttribute(
        "aria-pressed",
        strategy.id === state.selectedStrategyId ? "true" : "false"
      );
      const top = create("span", "strategy-card-top");
      top.append(
        create("span", "strategy-card-repo", strategy.repository),
        create("i", `strategy-dot ${strategy.status}`)
      );
      const value = create("span", "strategy-card-value");
      const pnl = asNumber(strategy.pnl_usdt);
      value.append(
        create("strong", "", formatUsd(strategy.equity_usdt)),
        create("small", tone(pnl), formatUsd(pnl, true))
      );
      button.append(
        top,
        create("h3", "", strategy.name),
        create("p", "", strategy.description),
        value,
        create("span", "strategy-card-state", strategy.status_label)
      );
      button.addEventListener("click", () => {
        state.selectedStrategyId = strategy.id;
        state.selectedAsset = null;
        state.selectedVenue = null;
        render();
      });
      container.append(button);
    }
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

  const chartSeries = () => {
    const strategy = selectedStrategy();
    const strategyCandles = strategy?.candles || [];
    const source = strategyCandles.length ? strategyCandles : state.funding?.candles || [];
    return source.filter((item) => Array.isArray(item.items) && item.items.length > 1);
  };

  const renderChartInspector = (candle) => {
    const values = {
      "#chart-open": candle?.open,
      "#chart-candle-high": candle?.high,
      "#chart-candle-low": candle?.low,
      "#chart-close": candle?.close,
    };
    for (const [selector, value] of Object.entries(values)) {
      $(selector).textContent = candle ? formatPrice(value) : "—";
    }
    $("#chart-candle-time").textContent = candle
      ? new Date(candle.timestamp_ms).toLocaleString("ru-RU", {
          day: "2-digit",
          month: "short",
          hour: "2-digit",
          minute: "2-digit",
        })
      : "—";
  };

  const renderChart = () => {
    const series = chartSeries();
    const assets = [...new Set(series.map((item) => String(item.asset)))];
    if (!assets.includes(state.selectedAsset)) state.selectedAsset = assets[0] || null;
    const assetSeries = series.filter((item) => item.asset === state.selectedAsset);
    const venues = assetSeries.map((item) => String(item.exchange_id));
    if (!venues.includes(state.selectedVenue)) state.selectedVenue = venues[0] || null;
    renderTabs($("#asset-tabs"), assets, state.selectedAsset, (asset) => {
      state.selectedAsset = asset;
      state.selectedVenue = null;
      renderChart();
    });
    renderTabs($("#venue-tabs"), venues, state.selectedVenue, (venue) => {
      state.selectedVenue = venue;
      renderChart();
    });
    renderTabs(
      $("#chart-window-tabs"),
      ["30", "60", "120"],
      String(state.chartCandleCount),
      (value) => {
        state.chartCandleCount = asNumber(value, 60);
        state.chartKeyboardIndex = null;
        renderChart();
      }
    );

    const selected = assetSeries.find((item) => item.exchange_id === state.selectedVenue);
    const chart = $("#candle-chart");
    state.chartInteractionController?.abort();
    state.chartInteractionController = null;
    chart.replaceChildren();
    if (!selected) {
      $("#chart-empty").hidden = false;
      $("#chart-title").textContent = "Рынок";
      $("#chart-last").textContent = "—";
      $("#chart-change").textContent = "—";
      $("#chart-change").className = "neutral";
      $("#chart-change").title = "";
      $("#chart-low").textContent = "—";
      $("#chart-high").textContent = "—";
      $("#chart-range").textContent = "—";
      chart.setAttribute("aria-label", "Для выбранной стратегии пока нет свечей");
      renderChartInspector(null);
      return;
    }
    $("#chart-empty").hidden = true;
    const candles = selected.items.slice(-state.chartCandleCount);
    const rawMin = Math.min(...candles.map((item) => asNumber(item.low)));
    const rawMax = Math.max(...candles.map((item) => asNumber(item.high)));
    const padding = Math.max((rawMax - rawMin) * 0.08, rawMax * 0.0005);
    const minimum = rawMin - padding;
    const maximum = rawMax + padding;
    const height = 360;
    const chartBounds = chart.getBoundingClientRect();
    const width = Math.max(
      440,
      Math.round(height * ((chartBounds.width || 960) / (chartBounds.height || height)))
    );
    chart.setAttribute("viewBox", `0 0 ${width} ${height}`);
    const left = 10;
    const right = 78;
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
      chart.append(
        svg("line", {
          x1: left,
          y1: gridY,
          x2: width - right,
          y2: gridY,
          class: "chart-grid",
        })
      );
      const label = svg("text", { x: width - right + 10, y: gridY + 4, class: "chart-axis" });
      label.textContent = formatPrice(price);
      chart.append(label);
    }

    const timeIndexes = [...new Set([0, Math.floor((candles.length - 1) / 2), candles.length - 1])];
    for (const index of timeIndexes) {
      const center = left + xStep * index + xStep / 2;
      chart.append(
        svg("line", {
          x1: center,
          y1: top,
          x2: center,
          y2: height - bottom,
          class: "chart-grid chart-grid-vertical",
        })
      );
    }

    for (const [index, candle] of candles.entries()) {
      const center = left + xStep * index + xStep / 2;
      const candleClass =
        asNumber(candle.close) >= asNumber(candle.open) ? "candle-up" : "candle-down";
      chart.append(
        svg("line", {
          x1: center,
          y1: y(asNumber(candle.high)),
          x2: center,
          y2: y(asNumber(candle.low)),
          class: candleClass,
        })
      );
      const openY = y(asNumber(candle.open));
      const closeY = y(asNumber(candle.close));
      chart.append(
        svg("rect", {
          x: center - bodyWidth / 2,
          y: Math.min(openY, closeY),
          width: bodyWidth,
          height: Math.max(1, Math.abs(closeY - openY)),
          rx: 0.7,
          class: candleClass,
        })
      );
    }

    for (const index of timeIndexes) {
      const label = svg("text", {
        x: left + xStep * index + xStep / 2,
        y: height - 7,
        class: "chart-axis",
        "text-anchor": index === 0 ? "start" : index === candles.length - 1 ? "end" : "middle",
      });
      label.textContent = new Date(candles[index].timestamp_ms).toLocaleTimeString("ru-RU", {
        hour: "2-digit",
        minute: "2-digit",
      });
      chart.append(label);
    }

    const first = asNumber(candles[0].open);
    const last = asNumber(candles.at(-1).close);
    const change = first ? (last / first - 1) * 100 : 0;
    const lastY = y(last);
    chart.append(
      svg("line", {
        x1: left,
        y1: lastY,
        x2: width - right,
        y2: lastY,
        class: "last-line",
      })
    );
    const lastLabel = svg("text", { x: width - right + 10, y: lastY - 6, class: "last-label" });
    lastLabel.textContent = formatPrice(last);
    chart.append(lastLabel);

    const verticalCrosshair = svg("line", {
      y1: top,
      y2: height - bottom,
      class: "chart-crosshair",
      visibility: "hidden",
    });
    const horizontalCrosshair = svg("line", {
      x1: left,
      x2: width - right,
      class: "chart-crosshair",
      visibility: "hidden",
    });
    const crosshairPoint = svg("circle", {
      r: 4,
      class: "chart-crosshair-point",
      visibility: "hidden",
    });
    chart.append(verticalCrosshair, horizontalCrosshair, crosshairPoint);

    const inspectCandle = (index) => {
      const safeIndex = Math.max(0, Math.min(candles.length - 1, index));
      const candle = candles[safeIndex];
      const center = left + xStep * safeIndex + xStep / 2;
      const closeY = y(asNumber(candle.close));
      verticalCrosshair.setAttribute("x1", String(center));
      verticalCrosshair.setAttribute("x2", String(center));
      horizontalCrosshair.setAttribute("y1", String(closeY));
      horizontalCrosshair.setAttribute("y2", String(closeY));
      crosshairPoint.setAttribute("cx", String(center));
      crosshairPoint.setAttribute("cy", String(closeY));
      verticalCrosshair.setAttribute("visibility", "visible");
      horizontalCrosshair.setAttribute("visibility", "visible");
      crosshairPoint.setAttribute("visibility", "visible");
      renderChartInspector(candle);
      state.chartKeyboardIndex = safeIndex;
    };
    const resetInspection = () => {
      verticalCrosshair.setAttribute("visibility", "hidden");
      horizontalCrosshair.setAttribute("visibility", "hidden");
      crosshairPoint.setAttribute("visibility", "hidden");
      renderChartInspector(candles.at(-1));
      state.chartKeyboardIndex = null;
    };
    const inspectPointer = (event) => {
      const bounds = chart.getBoundingClientRect();
      const viewX = ((event.clientX - bounds.left) / bounds.width) * width;
      inspectCandle(Math.floor((viewX - left) / xStep));
    };
    const interactionController = new AbortController();
    const interactionOptions = { signal: interactionController.signal };
    state.chartInteractionController = interactionController;
    chart.addEventListener("pointermove", inspectPointer, interactionOptions);
    chart.addEventListener("pointerdown", inspectPointer, interactionOptions);
    chart.addEventListener("pointerleave", resetInspection, interactionOptions);
    chart.addEventListener(
      "focus",
      () => inspectCandle(candles.length - 1),
      interactionOptions
    );
    chart.addEventListener("blur", resetInspection, interactionOptions);
    chart.addEventListener(
      "keydown",
      (event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        const current = state.chartKeyboardIndex ?? candles.length - 1;
        inspectCandle(current + (event.key === "ArrowLeft" ? -1 : 1));
      },
      interactionOptions
    );

    renderChartInspector(candles.at(-1));
    $("#chart-title").textContent = `${selected.asset} / USDT · ${selected.exchange_id}`;
    chart.setAttribute(
      "aria-label",
      `${selected.asset} / USDT, ${selected.exchange_id}, ${candles.length} свечей, последняя цена ${formatPrice(last)}`
    );
    $("#chart-last").textContent = formatPrice(last);
    $("#chart-change").textContent = `${change > 0 ? "+" : ""}${formatNumber(change, 2)}%`;
    $("#chart-change").className = tone(change);
    $("#chart-change").title = `Изменение за последние ${candles.length} свечей`;
    $("#chart-low").textContent = formatPrice(rawMin);
    $("#chart-high").textContent = formatPrice(rawMax);
    $("#chart-range").textContent = `${selected.timeframe} · последние ${candles.length} из ${selected.items.length} свечей`;
  };

  const positionTitle = (position) => {
    const candidate = position.candidate || {};
    return position.asset || candidate.asset || position.symbol || "Позиция";
  };

  const positionDescription = (position) => {
    const candidate = position.candidate || {};
    if (candidate.long_exchange && candidate.short_exchange) {
      return `${candidate.long_exchange} long ↔ ${candidate.short_exchange} short`;
    }
    const mark = position.mark_price ?? position.currentPrice ?? position.markPrice;
    const pnl =
      position.unrealized_pnl_usdt ?? position.unrealizedPnlUsd ?? position.netPnlUsd;
    const parts = [position.side || "long"];
    if (Number.isFinite(Number(mark))) parts.push(`mark ${formatPrice(mark)}`);
    if (Number.isFinite(Number(pnl))) parts.push(formatUsd(pnl, true));
    return parts.join(" · ");
  };

  const renderSelected = () => {
    const strategy = selectedStrategy();
    if (!strategy) return;
    $("#selected-repo").textContent = `${strategy.repository} · ${strategy.mode}`;
    $("#position-title").textContent = strategy.name;
    $("#selected-equity").textContent = formatUsd(strategy.equity_usdt);
    const selectedPnl = $("#selected-pnl");
    selectedPnl.textContent = `${formatUsd(strategy.pnl_usdt, true)} · ${formatNumber(strategy.return_percent, 2)}%`;
    selectedPnl.className = tone(strategy.pnl_usdt);
    const status = $("#position-status");
    status.textContent = strategy.status_label;
    const statusTone =
      strategy.status === "running" || strategy.status === "healthy"
        ? "open"
        : strategy.status === "waiting"
          ? "waiting"
          : "degraded";
    status.className = `position-status ${statusTone}`;

    const facts = $("#strategy-facts");
    facts.replaceChildren();
    for (const [label, value] of [
      ["Рынок", strategy.market],
      ["Частота", strategy.timeframe],
      ["Открыто", String(asNumber(strategy.open_positions))],
      ["Сделок / циклов", String(asNumber(strategy.closed_positions))],
    ]) {
      const fact = create("div", "strategy-fact");
      fact.append(create("span", "", label), create("strong", "", value));
      facts.append(fact);
    }

    const body = $("#position-body");
    body.replaceChildren();
    const positions = strategy.positions || [];
    if (!positions.length) {
      const card = create("div", "waiting-card");
      const inner = create("div");
      inner.append(
        create("span", "waiting-icon", "⌁"),
        create("strong", "", "Сейчас без позиции"),
        create("p", "", strategy.status_label),
        create("div", "threshold", strategy.description)
      );
      card.append(inner);
      body.append(card);
      return;
    }
    body.append(create("h3", "", "Открытые paper-позиции"));
    for (const position of positions) {
      const card = create("article", "paper-position");
      const head = create("div", "paper-position-head");
      head.append(create("strong", "", positionTitle(position)), create("span", "", "paper"));
      card.append(head, create("p", "", positionDescription(position)));
      body.append(card);
    }
  };

  const renderStrategyContexts = () => {
    const container = $("#strategy-contexts");
    container.replaceChildren();
    for (const strategy of state.hub?.strategies || []) {
      const context = strategy.context || {};
      const card = create("article", "strategy-context-card");
      card.setAttribute("aria-label", `Контекст стратегии ${strategy.name}`);

      const head = create("header", "strategy-context-head");
      const title = create("div");
      title.append(
        create("span", "strategy-card-repo", `${strategy.repository} · paper`),
        create("h3", "", strategy.name)
      );
      head.append(
        title,
        create("span", `context-state ${strategy.status}`, strategy.status_label)
      );

      const copy = create("div", "strategy-context-copy");
      for (const [label, value, className] of [
        ["Как работает", context.how_it_works || strategy.description, ""],
        ["Почему сейчас", context.why_now || strategy.status_label, "context-current"],
        ["Чего ждём", context.waiting_for || "Следующего подтверждённого сигнала.", ""],
      ]) {
        const row = create("div", `context-copy-row ${className}`.trim());
        row.append(create("span", "", label), create("p", "", value));
        copy.append(row);
      }

      const metrics = create("dl", "context-metrics");
      for (const metric of context.metrics || []) {
        const item = create("div");
        item.append(
          create("dt", "", String(metric.label || "Показатель")),
          create("dd", "", String(metric.value ?? "—"))
        );
        metrics.append(item);
      }
      card.append(head, copy, metrics);
      container.append(card);
    }
  };

  const renderMarkets = () => {
    const data = state.funding || {};
    const body = $("#market-rows");
    body.replaceChildren();
    for (const market of data.markets || []) {
      const row = document.createElement("tr");
      const nameCell = document.createElement("td");
      const name = create("div", "market-name");
      name.append(create("span", "asset-icon", String(market.asset).slice(0, 3)));
      const labels = create("span");
      labels.append(
        create("strong", "", `${market.asset} / USDT`),
        create("small", "", market.exchange_id)
      );
      name.append(labels);
      nameCell.append(name);
      row.append(nameCell);
      for (const value of [
        formatPrice(market.mark_price),
        `${formatNumber(market.funding_bps_8h, 3)} bps`,
        `${formatNumber(market.predicted_funding_bps_8h, 3)} bps`,
        `${formatNumber(market.book_spread_bps, 3)} bps`,
        `$${formatCompact(market.open_interest_usdt)}`,
      ]) {
        row.append(create("td", "", value));
      }
      const freshness = document.createElement("td");
      freshness.append(create("span", "fresh", "live"));
      row.append(freshness);
      body.append(row);
    }
    $("#scan-meta").textContent = `${(data.markets || []).length} рынков · ${asNumber(
      data.scan?.candidates?.length
    )} сигналов`;
  };

  const render = () => {
    renderAccount();
    renderStrategies();
    renderChart();
    renderSelected();
    renderMarkets();
    renderStrategyContexts();
    const updated = state.hub?.generated_at_ms || state.funding?.updated_at_ms;
    $("#updated-at").textContent = `обновлено ${formatTime(updated)}`;
    const errors = [...(state.funding?.scan?.errors || [])];
    const unavailable = (state.hub?.strategies || []).filter(
      (item) => item.status === "degraded"
    );
    if (!(state.funding?.candles || []).length) {
      errors.push(...(state.funding?.candle_errors || []));
    }
    if (unavailable.length) {
      errors.push(`Ограничены данные: ${unavailable.map((item) => item.name).join(", ")}`);
    }
    const banner = $("#error-banner");
    banner.hidden = errors.length === 0;
    banner.textContent = errors.join(" · ");
    const healthy = state.funding?.health === "healthy";
    setConnection(healthy ? "live" : "error", healthy ? "WebSocket" : "Данные устарели");
  };

  const showConnectionError = (message) => {
    setConnection("error", "Переподключение");
    if (state.hub) return;
    const banner = $("#error-banner");
    banner.hidden = false;
    banner.textContent = message;
  };

  const applySocketMessage = (event) => {
    try {
      const message = JSON.parse(event.data);
      if (message.type === "heartbeat") return;
      if (message.type !== "snapshot" || !message.paper || !message.strategies) {
        throw new Error("неверный формат snapshot");
      }
      state.funding = message.paper;
      state.hub = message.strategies;
      render();
    } catch (error) {
      showConnectionError(`Ошибка WebSocket: ${error.message}`);
    }
  };

  const scheduleReconnect = () => {
    if (state.unloading || state.reconnectTimer) return;
    const delay = Math.min(15_000, 1000 * 2 ** Math.min(state.reconnectAttempts, 4));
    state.reconnectAttempts += 1;
    state.reconnectTimer = setTimeout(() => {
      state.reconnectTimer = null;
      connect();
    }, delay);
  };

  const connect = () => {
    if (state.unloading) return;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${window.location.host}/api/v1/ws`);
    state.socket = socket;
    socket.onopen = () => {
      state.reconnectAttempts = 0;
      setConnection("live", "WebSocket");
    };
    socket.onmessage = applySocketMessage;
    socket.onerror = () => {
      if (state.socket === socket) {
        showConnectionError("WebSocket недоступен");
      }
    };
    socket.onclose = () => {
      if (state.socket !== socket) return;
      state.socket = null;
      showConnectionError("WebSocket переподключается");
      scheduleReconnect();
    };
  };

  const reconnectNow = () => {
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = null;
    state.reconnectAttempts = 0;
    const socket = state.socket;
    state.socket = null;
    socket?.close(1000, "manual reconnect");
    connect();
  };

  $("#refresh").addEventListener("click", reconnectNow);
  window.addEventListener("resize", () => {
    if (state.resizeFrame) cancelAnimationFrame(state.resizeFrame);
    state.resizeFrame = requestAnimationFrame(() => {
      state.resizeFrame = null;
      if (state.hub) renderChart();
    });
  });
  window.addEventListener("beforeunload", () => {
    state.unloading = true;
    state.chartInteractionController?.abort();
    if (state.resizeFrame) cancelAnimationFrame(state.resizeFrame);
    clearTimeout(state.reconnectTimer);
    state.socket?.close(1000, "page unload");
  });
  connect();
})();
