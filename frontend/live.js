(() => {
  'use strict';

  const state = { paused: false, eventSource: null, pollTimer: null, data: null };
  const $ = (selector) => document.querySelector(selector);
  const number = (value, fallback = 0) => { const parsed = Number(value); return Number.isFinite(parsed) ? parsed : fallback; };
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#039;', '"': '&quot;' }[char]));
  const num = (value, digits = 2) => Number.isFinite(Number(value)) ? number(value).toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '—';
  const pct = (value, digits = 2, signed = false) => { if (!Number.isFinite(Number(value))) return '—'; const result = number(value) * 100; return `${signed && result > 0 ? '+' : ''}${result.toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`; };
  const money = (value) => Number.isFinite(Number(value)) ? `$${number(value).toLocaleString('en-US', { maximumFractionDigits: 0 })}` : '—';
  const multiple = (value, digits = 2) => Number.isFinite(Number(value)) ? `${num(value, digits)}×` : '—';
  const timestamp = (value) => { if (!value) return '—'; const date = new Date(value); return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString('ru-RU', { timeZone: 'UTC', dateStyle: 'short', timeStyle: 'short' }) + ' UTC'; };

  function toast(message, error = false) {
    const node = $('#toast');
    node.textContent = message;
    node.classList.toggle('error', error);
    node.classList.add('visible');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => node.classList.remove('visible'), 2800);
  }

  function setText(selector, value) { const node = $(selector); if (node) node.textContent = value; }

  function statusClass(value) {
    if (value === 'healthy') return 'pill-healthy';
    if (value === 'warn') return 'pill-warn';
    if (value === 'halt') return 'pill-halt';
    return 'pill-idle';
  }

  function renderStatus(runtime) {
    const status = String(runtime.status || 'idle');
    setText('#runtime-status', status.toUpperCase());
    const pill = $('#service-status');
    pill.textContent = status.toUpperCase();
    pill.className = `pill ${statusClass(status)}`;
    setText('#generated-at', timestamp(runtime.generated_at_utc));
    setText('#runtime-root', runtime.runtime_root || 'runtime root not configured');
    setText('#last-refresh', `Последнее обновление: ${timestamp(runtime.generated_at_utc)}`);
  }

  function renderAggregate(runtime) {
    const aggregate = runtime.aggregate || {};
    setText('#strategy-count', aggregate.strategy_count ?? 0);
    setText('#observation-count', aggregate.observation_count ?? 0);
    setText('#cycle-count', aggregate.committed_cycles ?? 0);
    setText('#incident-count', number(aggregate.critical_incidents) + number(aggregate.warning_incidents));
    const metrics = [
      ['Strategies', aggregate.strategy_count ?? 0, 'auto-discovered roots'],
      ['Observations', aggregate.observation_count ?? 0, 'strict telemetry rows'],
      ['Committed cycles', aggregate.committed_cycles ?? 0, 'immutable evidence'],
      ['Critical incidents', aggregate.critical_incidents ?? 0, 'HALT conditions'],
      ['Warnings', aggregate.warning_incidents ?? 0, 'execution/freshness'],
      ['Latest evidence', aggregate.latest_timestamp ? timestamp(aggregate.latest_timestamp) : '—', 'UTC'],
    ];
    $('#aggregate-grid').innerHTML = metrics.map(([label, value, note]) => `<article class="card metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small></article>`).join('');
  }

  function renderStrategies(runtime) {
    const strategies = runtime.strategies || [];
    $('#strategy-empty').hidden = strategies.length > 0;
    $('#strategy-rows').innerHTML = strategies.map((item) => {
      const slippage = item.slippage_ratio == null ? '—' : `${num(item.slippage_ratio, 2)}×`;
      return `<tr><td>${escapeHtml(item.strategy_id)}</td><td><span class="health ${escapeHtml(item.health)}">${escapeHtml(item.health)}</span></td><td>${escapeHtml(money(item.latest_equity))}</td><td>${escapeHtml(pct(item.cumulative_return, 2, true))}</td><td>${escapeHtml(multiple(item.latest_gross_realized, 2))}</td><td>${escapeHtml(multiple(item.total_turnover, 2))}</td><td>${escapeHtml(slippage)}</td><td>${escapeHtml(timestamp(item.latest_timestamp))}</td></tr>`;
    }).join('');
  }

  function renderIncidents(runtime) {
    const incidents = runtime.incidents || [];
    $('#incident-empty').hidden = incidents.length > 0;
    $('#incident-list').innerHTML = incidents.map((item) => `<article class="card incident ${escapeHtml(item.severity)}"><span class="incident-bar"></span><div><h3>${escapeHtml(item.title)}</h3><p>${escapeHtml(item.strategy_id)} · ${escapeHtml(item.category)}${item.cycle_id ? ` · ${escapeHtml(item.cycle_id)}` : ''}<br>${escapeHtml(item.detail)}</p></div><time>${escapeHtml(timestamp(item.timestamp))}</time></article>`).join('');
  }

  function detail(label, value) { return `<div class="detail"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? '—')}</strong></div>`; }

  function renderContext(runtime, historical) {
    const v517 = runtime.v517 || {};
    const vState = v517.state;
    const decision = v517.decision;
    if (vState || decision) {
      const stateName = vState?.market_state ?? vState?.state ?? decision?.market_state ?? decision?.state ?? 'available';
      setText('#v517-state', String(stateName).replaceAll('_', ' '));
      $('#v517-details').innerHTML = [
        detail('Target leverage', multiple(decision?.target_leverage ?? decision?.selected_leverage ?? vState?.target_leverage, 3)),
        detail('State age', `${vState?.state_age_days ?? decision?.state_age_days ?? '—'} d`),
        detail('Guard', String(vState?.guard_active ?? decision?.guard_active ?? false)),
        detail('Decision hash', decision?.decision_hash ?? decision?.target_hash ?? '—'),
      ].join('');
      setText('#v517-source', v517.state_source || v517.decision_source || 'runtime artifact');
    } else {
      setText('#v517-state', 'Нет runtime state');
      $('#v517-details').innerHTML = detail('Expected files', 'v517_state.json / v517_decision.json') + detail('Historical policy', 'available in research dashboard');
      setText('#v517-source', 'Fail-closed: no state inferred');
    }

    const market = runtime.market_state;
    if (market) {
      const latest = market.latest && typeof market.latest === 'object' ? market.latest : market;
      const label = latest.state_label ?? latest.state ?? 'runtime market context';
      setText('#market-state', String(label).replaceAll('_', ' '));
      $('#market-details').innerHTML = [
        detail('Confidence', pct(latest.assignment_confidence ?? latest.confidence, 1)),
        detail('Duration', `${latest.state_duration_days ?? latest.duration_days ?? '—'} d`),
        detail('Novelty', num(latest.novelty_ratio, 3)),
        detail('As of', latest.open_time ?? latest.as_of_utc ?? '—'),
      ].join('');
      setText('#market-source', runtime.market_state_source || 'runtime market artifact');
    } else {
      const archived = historical.market || {};
      setText('#market-state', archived.state ? `archive: ${String(archived.state).replaceAll('_', ' ')}` : 'Архивный fallback');
      $('#market-details').innerHTML = [detail('As of', archived.as_of || '—'), detail('Confidence', pct(archived.confidence, 1)), detail('Duration', `${archived.duration_days ?? '—'} d`), detail('Novelty', num(archived.novelty_ratio, 3))].join('');
      setText('#market-source', 'No runtime market_state.json; displaying committed archive context');
    }
  }

  function render(data) {
    state.data = data;
    const runtime = data.runtime || { status: 'idle', aggregate: {}, strategies: [], incidents: [], v517: {} };
    renderStatus(runtime);
    renderAggregate(runtime);
    renderStrategies(runtime);
    renderIncidents(runtime);
    renderContext(runtime, data);
  }

  async function refresh({ quiet = false } = {}) {
    if (state.paused) return;
    try {
      const response = await fetch('./api/v1/dashboard', { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      render(data);
      if (!quiet) toast('Runtime snapshot обновлён');
    } catch (error) {
      const pill = $('#service-status');
      pill.textContent = 'OFFLINE';
      pill.className = 'pill pill-halt';
      setText('#runtime-status', 'OFFLINE');
      if (!quiet) toast(`API недоступен: ${error.message}`, true);
    }
  }

  function startPolling() {
    clearInterval(state.pollTimer);
    setText('#connection-mode', 'POLLING');
    state.pollTimer = setInterval(() => refresh({ quiet: true }), 5000);
  }

  function connectEvents() {
    if (!window.EventSource) { startPolling(); return; }
    state.eventSource?.close();
    const source = new EventSource('./api/v1/events');
    state.eventSource = source;
    source.addEventListener('open', () => setText('#connection-mode', 'SSE'));
    source.addEventListener('snapshot', () => refresh({ quiet: true }));
    source.addEventListener('error', () => { source.close(); state.eventSource = null; startPolling(); });
  }

  function wire() {
    $('#refresh').addEventListener('click', () => refresh());
    $('#pause').addEventListener('click', () => {
      state.paused = !state.paused;
      $('#pause').textContent = state.paused ? 'Продолжить' : 'Пауза';
      setText('#connection-mode', state.paused ? 'PAUSED' : state.eventSource ? 'SSE' : 'POLLING');
      if (!state.paused) refresh({ quiet: true });
    });
  }

  document.addEventListener('DOMContentLoaded', async () => {
    wire();
    await refresh({ quiet: true });
    connectEvents();
  });
})();
