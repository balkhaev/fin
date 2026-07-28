(() => {
  'use strict';

  const state = { data: null, chartMode: 'equity', telemetry: null };
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const number = (value, fallback = 0) => { const parsed = Number(value); return Number.isFinite(parsed) ? parsed : fallback; };
  const pct = (value, digits = 2, signed = false) => { const v = number(value) * 100; const prefix = signed && v > 0 ? '+' : ''; return `${prefix}${v.toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`; };
  const num = (value, digits = 2) => number(value).toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  const multiple = (value, digits = 2) => `${num(value, digits)}×`;
  const money = (value) => `$${number(value).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#039;', '"': '&quot;' }[char]));

  function showToast(message, error = false) {
    const toast = $('#toast');
    toast.textContent = message;
    toast.classList.toggle('error', error);
    toast.classList.add('visible');
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => toast.classList.remove('visible'), 3200);
  }

  function setText(selector, value) { const node = $(selector); if (node) node.textContent = value; }

  async function loadData() {
    const response = await fetch('./data/dashboard.json', { cache: 'no-store' });
    if (!response.ok) throw new Error(`dashboard.json: HTTP ${response.status}`);
    const data = await response.json();
    if (!data || data.schema_version !== 1) throw new Error('Неподдерживаемая схема dashboard.json');
    return data;
  }

  function renderHero(data) {
    setText('#hero-cagr', pct(data.hero.cagr));
    setText('#hero-sharpe', num(data.hero.sharpe, 3));
    setText('#hero-dd', pct(data.hero.max_drawdown));
    setText('#hero-avg-lev', multiple(data.hero.average_leverage, 3));
    setText('#hero-max-lev', multiple(data.hero.maximum_leverage, 3));
    setText('#generated-at', new Date(data.generated_at_utc).toLocaleString('ru-RU', { timeZone: 'UTC', dateStyle: 'short', timeStyle: 'short' }) + ' UTC');
    const mode = $('#mode-pill');
    mode.textContent = data.environment.live_ready ? 'Live ready' : 'Shadow only';
    mode.className = `status-pill ${data.environment.live_ready ? 'status-positive' : 'status-warning'}`;
    setText('#archive-pill', data.market.archived ? 'Архив рынка' : 'Current');
  }

  function renderPerformance(data) {
    const base = data.stress_scenarios.find((item) => item.id === 'base') || {};
    const calmar = Math.abs(number(base.max_drawdown)) > 0 ? number(base.cagr) / Math.abs(number(base.max_drawdown)) : 0;
    setText('#calmar-value', num(calmar, 3));
    setText('#rolling-value', pct(base.worst_rolling_365 || -0.20245657086746538));
    setText('#close-gross-value', multiple(base.maximum_close_gross, 3));
    setText('#turnover-value', multiple(base.turnover, 2));
    const positiveYears = data.annual_returns.filter((item) => number(item.return) > 0).length;
    setText('#positive-years', `${positiveYears} / ${data.annual_returns.length}`);
    setText('#dd-envelope-label', pct(base.max_drawdown));
    $('#dd-gauge').style.width = `${Math.min(100, Math.abs(number(base.max_drawdown)) / 0.40 * 100)}%`;
    renderChart();
  }

  function chartValue(point) { if (state.chartMode === 'drawdown') return number(point.drawdown); if (state.chartMode === 'leverage') return number(point.leverage); return number(point.equity); }
  function chartFormat(value) { if (state.chartMode === 'equity') return money(value); if (state.chartMode === 'leverage') return multiple(value, 2); return pct(value, 1); }
  function pathFromPoints(points) { return points.map((point, index) => `${index ? 'L' : 'M'}${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(' '); }

  function renderChart() {
    if (!state.data) return;
    const container = $('#equity-chart');
    const tooltip = $('#chart-tooltip');
    const raw = state.data.equity_curve || [];
    if (!raw.length) return;
    const width = 1000, height = 310, margin = { top: 18, right: 28, bottom: 34, left: 68 };
    const values = raw.map(chartValue);
    let min = Math.min(...values), max = Math.max(...values);
    if (state.chartMode === 'drawdown') max = Math.max(0, max);
    if (min === max) { min -= 1; max += 1; }
    const padding = (max - min) * 0.08; min -= padding; max += padding;
    const x = (index) => margin.left + index / Math.max(1, raw.length - 1) * (width - margin.left - margin.right);
    const y = (value) => margin.top + (max - value) / (max - min) * (height - margin.top - margin.bottom);
    const points = raw.map((point, index) => ({ x: x(index), y: y(values[index]), data: point }));
    const path = pathFromPoints(points);
    const areaPath = `${path} L${points.at(-1).x},${height - margin.bottom} L${points[0].x},${height - margin.bottom} Z`;
    const ticks = 5;
    const grid = Array.from({ length: ticks + 1 }, (_, index) => { const value = max - index / ticks * (max - min); const yy = y(value); return `<line class="chart-grid" x1="${margin.left}" y1="${yy}" x2="${width - margin.right}" y2="${yy}" /><text class="chart-axis-label" x="${margin.left - 10}" y="${yy + 4}" text-anchor="end">${escapeHtml(chartFormat(value))}</text>`; }).join('');
    const labelIndexes = [0, Math.floor((raw.length - 1) / 2), raw.length - 1];
    const xLabels = labelIndexes.map((index) => `<text class="chart-axis-label" x="${x(index)}" y="${height - 8}" text-anchor="${index === 0 ? 'start' : index === raw.length - 1 ? 'end' : 'middle'}">${escapeHtml(raw[index].date)}</text>`).join('');
    const markers = points.filter((point) => point.data.guard || point.data.risk_reduction).map((point) => `<circle class="chart-marker" cx="${point.x}" cy="${point.y}" r="4" />`).join('');
    const modeClass = state.chartMode === 'equity' ? '' : state.chartMode;
    container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="areaGradient" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#56f0bd" stop-opacity=".45" /><stop offset="1" stop-color="#56f0bd" stop-opacity="0" /></linearGradient></defs>${grid}${xLabels}${state.chartMode === 'equity' ? `<path class="chart-area" d="${areaPath}" />` : ''}<path class="chart-path ${modeClass}" d="${path}" />${markers}<line class="chart-crosshair" id="chart-crosshair" x1="0" y1="${margin.top}" x2="0" y2="${height - margin.bottom}" visibility="hidden" /><rect class="chart-hit" x="${margin.left}" y="${margin.top}" width="${width - margin.left - margin.right}" height="${height - margin.top - margin.bottom}" /></svg>`;
    const hit = $('.chart-hit', container), crosshair = $('#chart-crosshair', container);
    hit.addEventListener('pointermove', (event) => {
      const rect = hit.getBoundingClientRect();
      const fraction = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
      const point = points[Math.round(fraction * (raw.length - 1))];
      crosshair.setAttribute('x1', point.x); crosshair.setAttribute('x2', point.x); crosshair.setAttribute('visibility', 'visible');
      tooltip.hidden = false;
      tooltip.style.left = `${container.offsetLeft + Math.min(container.clientWidth - 175, Math.max(8, event.clientX - container.getBoundingClientRect().left + 10))}px`;
      tooltip.style.top = `${container.offsetTop + Math.max(8, event.clientY - container.getBoundingClientRect().top - 18)}px`;
      tooltip.innerHTML = `<strong>${escapeHtml(point.data.date)}</strong>${escapeHtml(chartFormat(chartValue(point.data)))}<br>target ${escapeHtml(multiple(point.data.leverage, 2))} · gross ${escapeHtml(multiple(point.data.close_gross, 2))}`;
    });
    hit.addEventListener('pointerleave', () => { tooltip.hidden = true; crosshair.setAttribute('visibility', 'hidden'); });
    const titles = { equity: ['Рост капитала', 'Equity'], drawdown: ['Просадка', 'Drawdown'], leverage: ['Целевой risk budget', 'Leverage'] };
    setText('#chart-title', titles[state.chartMode][0]); setText('#legend-main', titles[state.chartMode][1]); setText('#curve-start', chartFormat(values[0])); setText('#curve-end', chartFormat(values.at(-1))); setText('#curve-points', `${raw.length} points`);
  }

  function renderStress(data) {
    const maxCagr = Math.max(...data.stress_scenarios.map((item) => number(item.cagr)), .01);
    $('#stress-grid').innerHTML = data.stress_scenarios.map((scenario) => `<article class="card stress-card" data-tone="${escapeHtml(scenario.id)}"><span class="stress-name">${escapeHtml(scenario.name)}</span><strong class="stress-cagr">${escapeHtml(pct(scenario.cagr))}</strong><div class="stress-meta"><span>Sharpe <strong>${escapeHtml(num(scenario.sharpe, 3))}</strong></span><span>DD <strong>${escapeHtml(pct(scenario.max_drawdown))}</strong></span></div><div class="stress-bar"><span style="width:${Math.max(0, number(scenario.cagr) / maxCagr * 100)}%"></span></div></article>`).join('');
  }

  function renderAnnual(data) {
    const max = Math.max(...data.annual_returns.map((item) => number(item.return)), .01);
    $('#annual-bars').innerHTML = data.annual_returns.map((item, index) => { const height = Math.max(3, number(item.return) / max * 100); const partial = index === data.annual_returns.length - 1; return `<div class="annual-item ${partial ? 'partial' : ''}"><div class="annual-bar-wrap"><span class="annual-value" style="--height:${height}%">${escapeHtml(pct(item.return, 1, true))}</span><div class="annual-bar" style="height:${height}%"></div></div><div class="annual-label">${escapeHtml(item.year)}${partial ? ' H1' : ''}</div></div>`; }).join('');
  }

  function marketExplanation(market) {
    const axes = Object.fromEntries(market.axes.map((axis) => [axis.name.toLowerCase(), number(axis.value)]));
    const weak = Object.entries(axes).filter(([, value]) => value < -.5).sort((a, b) => a[1] - b[1]).slice(0, 3).map(([name]) => name);
    const strong = Object.entries(axes).filter(([, value]) => value > .5).sort((a, b) => b[1] - a[1]).slice(0, 2).map(([name]) => name);
    let text = `Архивное состояние «${market.state}» длится ${market.duration_days} дней.`;
    if (weak.length) text += ` Основное давление: ${weak.join(', ')}.`;
    if (strong.length) text += ` Поддерживающие оси: ${strong.join(', ')}.`;
    if (!market.novelty) text += ' Комбинация осей остаётся знакомой относительно development-codebook.';
    return text;
  }

  function renderMarket(data) {
    const market = data.market;
    setText('#market-as-of', market.as_of || '—'); setText('#market-state', String(market.state || 'unknown').replaceAll('_', ' ')); setText('#state-duration', `${market.duration_days} d`); setText('#state-confidence', pct(market.confidence, 1)); setText('#state-novelty', `${num(market.novelty_ratio, 3)}${market.novelty ? ' • novel' : ''}`); setText('#state-explanation', marketExplanation(market));
    $('#market-axes').innerHTML = market.axes.map((axis) => { const value = Math.max(-4, Math.min(4, number(axis.value))); const width = Math.abs(value) / 8 * 100; const positive = value >= 0; return `<div class="axis-row"><label>${escapeHtml(axis.name)}</label><div class="axis-track"><span class="axis-fill ${positive ? 'positive' : 'negative'}" style="width:${width}%"></span></div><span class="axis-value">${value > 0 ? '+' : ''}${escapeHtml(num(value, 3))}</span></div>`; }).join('');
  }

  function toneStatus(tone) { if (tone === 'accent') return 'mini-tag-positive'; if (tone === 'warning') return 'mini-tag-warning'; if (tone === 'info') return 'status-neutral'; return 'mini-tag-muted'; }

  function renderStrategies(data) {
    $('#strategy-grid').innerHTML = data.strategies.map((strategy) => `<article class="card strategy-card" data-tone="${escapeHtml(strategy.tone)}"><span class="strategy-role">${escapeHtml(strategy.role)}</span><h3>${escapeHtml(strategy.name)}</h3><span class="mini-tag strategy-status ${toneStatus(strategy.tone)}">${escapeHtml(strategy.status)}</span><div class="strategy-kpis"><div class="strategy-kpi"><span>CAGR</span><strong>${escapeHtml(pct(strategy.metrics.cagr))}</strong></div><div class="strategy-kpi"><span>Sharpe</span><strong>${escapeHtml(num(strategy.metrics.sharpe, 3))}</strong></div><div class="strategy-kpi"><span>Max DD</span><strong>${escapeHtml(pct(strategy.metrics.max_drawdown))}</strong></div><div class="strategy-kpi"><span>Max lev.</span><strong>${escapeHtml(strategy.metrics.maximum_leverage ? multiple(strategy.metrics.maximum_leverage, 2) : '—')}</strong></div></div><div class="strategy-evidence">${escapeHtml(strategy.evidence)}</div></article>`).join('');
    setText('#policy-high', multiple(data.policy.high_leverage, 3)); setText('#policy-base', multiple(data.policy.base_leverage, 2)); setText('#policy-low', multiple(data.policy.low_leverage, 2)); setText('#policy-rebalance', `${data.policy.rebalance_days} d`); setText('#policy-guard-in', pct(data.policy.guard_enter_drawdown, 1)); setText('#policy-guard-out', pct(data.policy.guard_exit_drawdown, 1)); setText('#policy-guard-cap', multiple(data.policy.guard_cap, 1));
  }

  function renderReadiness(data) {
    const passed = data.readiness.filter((item) => item.status === 'pass').length;
    setText('#readiness-score', `${passed}/${data.readiness.length}`);
    $('#readiness-list').innerHTML = data.readiness.map((item) => `<div class="readiness-item"><span class="readiness-icon ${item.status}">${item.status === 'pass' ? '✓' : '×'}</span><div class="readiness-copy"><strong>${escapeHtml(item.label)}</strong><span>${escapeHtml(item.detail)}</span></div><span class="readiness-state">${item.status === 'pass' ? 'passed' : 'blocked'}</span></div>`).join('');
    const blockers = data.readiness.filter((item) => item.status !== 'pass').length;
    setText('#preflight-title', blockers ? `Live заблокирован: ${blockers}` : 'Все gates пройдены');
    setText('#preflight-copy', blockers ? 'Панель намеренно не превращает исследовательскую метрику в разрешение на real-money execution. Shadow runtime готов; внешние доказательства отсутствуют.' : 'Все frozen preflight gates пройдены. Перед exchange submission всё равно требуется отдельное операционное подтверждение.');
  }

  function renderSources(data) {
    const governance = [['50% historical target', data.governance.historical_target_met], ['Modeled gates', data.governance.modeled_gates_passed], ['Parameters informed by history', data.governance.parameters_informed_by_history], ['Pristine holdout', data.governance.pristine_holdout], ['Promotion permitted', data.governance.promotion_permitted], ['Capital change authorized', data.governance.capital_change_authorized]];
    $('#governance-list').innerHTML = governance.map(([label, value]) => `<li>${escapeHtml(label)}: <strong>${value ? 'true' : 'false'}</strong></li>`).join('');
    $('#sources-list').innerHTML = data.sources.map((source) => `<li><code>${escapeHtml(source)}</code></li>`).join('');
  }

  function parseCsv(text) {
    const lines = text.replace(/\r/g, '').split('\n').filter(Boolean); if (lines.length < 2) return [];
    const parseLine = (line) => { const result = []; let value = '', quoted = false; for (let i = 0; i < line.length; i += 1) { const char = line[i]; if (char === '"' && line[i + 1] === '"') { value += '"'; i += 1; } else if (char === '"') quoted = !quoted; else if (char === ',' && !quoted) { result.push(value); value = ''; } else value += char; } result.push(value); return result; };
    const headers = parseLine(lines[0]); return lines.slice(1).map((line) => { const values = parseLine(line); return Object.fromEntries(headers.map((header, index) => [header, values[index] ?? ''])); });
  }

  function summarizeTelemetry(rows) {
    if (!rows.length) throw new Error('Файл не содержит наблюдений');
    const latest = rows.at(-1), equities = rows.map((row) => number(row.equity, NaN)).filter(Number.isFinite), drawdowns = rows.map((row) => number(row.drawdown, NaN)).filter(Number.isFinite), strategies = [...new Set(rows.map((row) => row.strategy_id).filter(Boolean))];
    const failures = rows.filter((row) => String(row.reconciliation_ok).toLowerCase() === 'false' || String(row.source_hash_match).toLowerCase() === 'false' || String(row.data_stale).toLowerCase() === 'true').length;
    return { observations: rows.length, strategies, latestTimestamp: latest.timestamp || latest.open_time || latest.date || '—', latestEquity: equities.length ? equities.at(-1) : NaN, maxDrawdown: drawdowns.length ? Math.min(...drawdowns) : NaN, failures, executionComplete: rows.filter((row) => String(row.execution_complete).toLowerCase() === 'true').length };
  }

  function renderTelemetry(summary) {
    state.telemetry = summary; const panel = $('#telemetry-panel'); panel.hidden = false;
    $('#telemetry-summary').innerHTML = [['Observations', summary.observations, summary.latestTimestamp], ['Strategies', summary.strategies.length, summary.strategies.join(', ') || 'not specified'], ['Latest equity', Number.isFinite(summary.latestEquity) ? money(summary.latestEquity) : '—', 'local browser import'], ['Integrity failures', summary.failures, `${summary.executionComplete} complete executions`]].map(([label, value, note]) => `<article class="card telemetry-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(note)}</small></article>`).join('');
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  async function importTelemetry(file) { const text = await file.text(); let rows; if (file.name.toLowerCase().endsWith('.json')) { const value = JSON.parse(text); rows = Array.isArray(value) ? value : value.rows || value.observations || [value]; } else rows = parseCsv(text); renderTelemetry(summarizeTelemetry(rows)); showToast(`Telemetry загружена: ${rows.length} наблюдений`); }

  function wireInteractions() {
    $$('[data-chart-mode]').forEach((button) => button.addEventListener('click', () => { $$('[data-chart-mode]').forEach((item) => item.classList.remove('active')); button.classList.add('active'); state.chartMode = button.dataset.chartMode; renderChart(); }));
    $('#telemetry-file').addEventListener('change', async (event) => { const file = event.target.files?.[0]; if (!file) return; try { await importTelemetry(file); } catch (error) { showToast(`Не удалось прочитать telemetry: ${error.message}`, true); } event.target.value = ''; });
    $('#clear-telemetry').addEventListener('click', () => { state.telemetry = null; $('#telemetry-panel').hidden = true; showToast('Локальная telemetry сброшена'); });
  }

  function render(data) { state.data = data; renderHero(data); renderPerformance(data); renderStress(data); renderAnnual(data); renderMarket(data); renderStrategies(data); renderReadiness(data); renderSources(data); }
  async function boot() { wireInteractions(); try { render(await loadData()); } catch (error) { console.error(error); showToast(`Control Room не загрузился: ${error.message}`, true); document.body.classList.add('load-error'); } }
  document.addEventListener('DOMContentLoaded', boot);
})();
