/* Read-only historical evidence. No account mutations, order endpoints or model execution. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const statusLabels = {control: 'Исходный контроль', exploratory: 'Исследовательский вариант', rejected: 'Основной опыт · не прошёл отбор', post_result: 'Последующая гипотеза · выбрана после результатов'};
  const number = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 2, minimumFractionDigits: 2});
  const integer = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 0});
  const shortDate = new Intl.DateTimeFormat('ru-RU', {month: 'short', year: '2-digit', timeZone: 'UTC'});
  const fullDate = new Intl.DateTimeFormat('ru-RU', {day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC'});
  let evidence = null, selected = 'runner720_125x', period = 'full', chartMode = 'equity';
  let requestSerial = 0;
  const finite = (v) => typeof v === 'number' && Number.isFinite(v);
  const pct = (v, signed = true) => finite(v) ? `${signed && v > 0 ? '+' : ''}${number.format(v)}%` : 'Не рассчитано';
  const usd = (v) => finite(v) ? `${number.format(v)} USDT` : 'Не рассчитано';
  const tone = (v) => !finite(v) || v === 0 ? 'neutral' : v > 0 ? 'positive' : 'negative';
  function node(tag, text, css) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (css) el.className = css; return el; }
  function replace(id, nodes) { $(id).replaceChildren(...nodes); }
  function baseline() { return evidence.models.find(m => m.id === evidence.baseline); }
  function candidate() { return evidence.models.find(m => m.id === selected); }
  function row() { return candidate().periods[period]; }
  function controlRow() { return baseline().periods[period]; }

  function validate(data) {
    if (!data || data.schema_version !== 1 || data.live_ready !== false || data.mode !== 'historical_read_only') throw new Error('Invalid evidence mode');
    if (!Array.isArray(data.models) || data.models.length !== 15 || data.models[0].id !== 'old_pair_1x' || data.baseline !== 'old_pair_1x') throw new Error('Invalid model registry');
    if (new Set(data.models.map(m => m.id)).size !== 15 || !Array.isArray(data.restrictions)) throw new Error('Invalid registry');
    for (const model of data.models) {
      if (typeof model.id !== 'string' || !/^[a-z0-9_]+$/.test(model.id) || typeof model.label !== 'string' || !statusLabels[model.status]) throw new Error('Invalid model metadata');
      for (const p of ['full', 'later', 'validation']) {
        const r = model.periods[p], contract = data.periods[p];
        if (!r || !contract || r.initial !== 10000 || r.start !== contract.start || r.end_exclusive !== contract.end_exclusive || r.days !== contract.days) throw new Error('Mismatched period');
        if (!['return_pct', 'cagr_pct', 'final_balance', 'max_mark_close_drawdown_pct', 'completed_episodes'].every(k => finite(r[k]))) throw new Error('Nonfinite metric');
        if (!r.qualification?.qualified_historical_scenario || !Array.isArray(r.annual) || !Array.isArray(r.curve) || r.curve.length !== r.days + 1) throw new Error('Incomplete evidence');
        let last = -Infinity;
        for (const point of r.curve) { if (point.length !== 3 || !point.every(finite) || point[0] <= last || point[1] <= 0 || point[2] > 1e-7) throw new Error('Invalid curve'); last = point[0]; }
        if (Math.abs(r.curve.at(-1)[1] - r.final_balance) > 1e-5) throw new Error('Curve mismatch');
      }
    }
    if (!data.models.some(m => m.id === data.default_model)) throw new Error('Unknown default');
    return data;
  }

  function metric(label, value, base, className) {
    const card = node('article', undefined, 'metric');
    card.append(node('p', label, 'metric-label'), node('p', value, `metric-value ${className || ''}`));
    const line = node('p', 'Исходная 1×', 'metric-baseline'); line.append(node('span', base)); card.append(line); return card;
  }
  function cell(tr, value, color = null) { const td = node('td', value, color || ''); tr.append(td); return td; }
  function renderMetrics() {
    const r = row(), b = controlRow();
    replace('metric-grid', [
      metric('Накопленно за период', pct(r.return_pct), pct(b.return_pct), tone(r.return_pct)),
      metric('Среднегодовые · CAGR', pct(r.cagr_pct), pct(b.cagr_pct)),
      metric('Просадка · часовой mark', pct(r.max_mark_close_drawdown_pct, false), pct(b.max_mark_close_drawdown_pct, false), 'negative'),
      metric('Завершённых эпизодов', integer.format(r.completed_episodes), integer.format(b.completed_episodes))
    ]);
    $('candidate-title').textContent = candidate().label;
    $('candidate-kind').textContent = statusLabels[candidate().status];
    const p = evidence.periods[period];
    $('period-label').textContent = `${fullDate.format(new Date(p.start + 'T00:00:00Z'))} — ${fullDate.format(new Date(Date.parse(p.end_exclusive + 'T00:00:00Z') - 86400000))} · ${p.days} дней`;
    for (const button of document.querySelectorAll('[data-period]')) button.setAttribute('aria-pressed', String(button.dataset.period === period));
  }
  function renderRisk() {
    const r = row(), lev = r.leverage_audit || {}, stats = r.episode_statistics || {};
    const values = [
      ['Итоговый капитал', usd(r.final_balance)],
      ['Комиссии', usd(r.fees)],
      ['Funding · списано / получено', usd(r.funding_cashflow)],
      ['Максимальный фактический номинал', finite(lev.max_mark_close_gross) ? `${number.format(lev.max_mark_close_gross)}×` : 'Не рассчитано'],
      ['Среднее удержание', finite(stats.mean_episode_hours) ? `${number.format(stats.mean_episode_hours / 24)} суток` : 'Нет эпизодов'],
      ['Месяцы: плюс / ноль / минус', `${r.positive_months} / ${r.zero_months} / ${r.negative_months}`]
    ];
    replace('risk-facts', values.map(([label, value]) => { const el = node('div'); el.append(node('dt', label), node('dd', value)); return el; }));
  }
  function renderAnnual() {
    const base = new Map(controlRow().annual.map(y => [y.year, y]));
    replace('annual-rows', row().annual.map(y => {
      const tr = node('tr'), label = cell(tr, String(y.year));
      if (!y.full_year) label.append(node('span', 'январь–август · неполный год', 'partial'));
      cell(tr, pct(base.get(y.year)?.return_pct), tone(base.get(y.year)?.return_pct));
      cell(tr, pct(y.return_pct), tone(y.return_pct)); return tr;
    }));
  }
  function renderStress() {
    const available = period === 'later' ? [
      ['Базовые расходы', null], ['Расходы ×2', 'later_double_costs'], ['Задержка +2 часа', 'later_delay2'], ['Начальный счёт 1 000 USDT', 'later_capital1000']
    ] : period === 'full' ? [['Базовые расходы', null], ['Расходы ×2', 'full_double_costs']] : [['Базовые расходы', null]];
    replace('stress-rows', available.map(([label, key]) => {
      const tr = node('tr'); cell(tr, label);
      for (const m of [baseline(), candidate()]) {
        const v = key ? m.stresses[key]?.return_pct : m.periods[period].return_pct;
        cell(tr, pct(v), tone(v));
      }
      return tr;
    }));
    $('stress-note').textContent = 'Накопленная доходность на выбранном периоде. «Не рассчитано» означает отсутствие такого сценария, а не нулевую прибыль. База: комиссия 0,05% + slippage 0,01% на сторону каждой ноги.';
  }
  function renderTable() {
    const head = document.querySelector('.comparison-panel thead th:last-child'); head.textContent = 'Эпизодов';
    replace('comparison-rows', evidence.models.map(m => {
      const tr = node('tr', undefined, m.id === selected ? 'selected' : '');
      const first = node('td'), button = node('button', m.label, 'model-button'); button.type = 'button';
      button.setAttribute('aria-label', `Сравнить: ${m.label}`);
      button.addEventListener('click', () => { selected = m.id; $('model-select').value = selected; render(); $('model-select').focus(); });
      first.append(button, node('span', statusLabels[m.status], 'model-status')); tr.append(first);
      const r = m.periods[period];
      cell(tr, pct(r.return_pct), tone(r.return_pct)); cell(tr, pct(r.cagr_pct));
      cell(tr, pct(r.max_mark_close_drawdown_pct, false), 'negative'); cell(tr, integer.format(r.completed_episodes)); return tr;
    }));
  }
  function svgElement(tag, attrs, text) { const e = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, String(v)); if (text !== undefined) e.textContent = text; return e; }
  function renderChart() {
    if (!evidence) return;
    const svg = $('equity-chart'), isDD = chartMode === 'dd';
    const width = Math.max(480, svg.clientWidth || 880), height = 305, pad = {l: 68, r: 22, t: 20, b: 36};
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    const one = controlRow().curve, two = row().curve, col = isDD ? 2 : 1;
    const vals = [...one.map(p => p[col]), ...two.map(p => p[col])];
    let lower = Math.min(...vals), upper = Math.max(...vals);
    if (isDD) { lower = Math.min(-1, lower) * 1.07; upper = 0; }
    else { const margin = Math.max((upper - lower) * .09, 100); lower -= margin; upper += margin; }
    const from = one[0][0], to = one.at(-1)[0];
    const x = t => pad.l + (t - from) / Math.max(1, to - from) * (width - pad.l - pad.r);
    const y = v => pad.t + (upper - v) / (upper - lower) * (height - pad.t - pad.b);
    const elements = [svgElement('title', {}, `${isDD ? 'Просадка' : 'Капитал'}: исходная модель и ${candidate().label}`),
      svgElement('desc', {}, isDD ? 'Минимальная просадка по фактическим часовым mark-закрытиям внутри каждого дня. Не внутрисвечная ликвидация.' : 'Дневные закрытия одного исторического счёта, начальные 10 000 USDT.')];
    for (let i = 0; i <= 4; i++) {
      const v = lower + (upper - lower) * i / 4, pos = y(v);
      elements.push(svgElement('line', {x1: pad.l, x2: width - pad.r, y1: pos, y2: pos, class: 'grid-line'}));
      elements.push(svgElement('text', {x: pad.l - 12, y: pos + 4, 'text-anchor': 'end', class: 'chart-label'}, isDD ? `${Math.round(v)}%` : integer.format(v)));
    }
    for (let i = 0; i <= 4; i++) {
      const t = from + (to - from) * i / 4;
      elements.push(svgElement('text', {x: x(t), y: height - 10, 'text-anchor': i === 0 ? 'start' : i === 4 ? 'end' : 'middle', class: 'chart-label'}, shortDate.format(new Date(t))));
    }
    for (const [points, name] of [[one, 'path-base'], [two, 'path-candidate']]) {
      const d = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(p[0]).toFixed(2)},${y(p[col]).toFixed(2)}`).join(' ');
      elements.push(svgElement('path', {d, class: name}));
    }
    svg.replaceChildren(...elements);
    $('chart-title').textContent = isDD ? 'Глубина просадки' : 'Траектория капитала';
    $('chart-candidate').textContent = candidate().label;
    $('chart-note').textContent = isDD ? 'Минимум фактической часовой просадки внутри дня. Исходная пунктирная линия закреплена для сравнения. Это не предел внутридневного риска.' : 'Кривая: дневные mark-закрытия. Показатель просадки выше рассчитан по всем часовым наблюдениям. Прибыль двух счетов не складывается.';
    $('show-equity').setAttribute('aria-pressed', String(!isDD)); $('show-dd').setAttribute('aria-pressed', String(isDD));
  }
  function render() { renderMetrics(); renderRisk(); renderAnnual(); renderStress(); renderTable(); renderChart(); }
  async function load() {
    const serial = ++requestSerial;
    $('load-status').hidden = false; $('load-error').hidden = true; $('content').hidden = true;
    try {
      const response = await fetch('./data/research-evidence.json', {method: 'GET', credentials: 'omit', cache: 'no-cache'});
      if (!response.ok) throw new Error(`Snapshot HTTP ${response.status}`);
      const loaded = validate(await response.json());
      if (serial !== requestSerial) return;
      evidence = loaded; selected = evidence.default_model; period = evidence.default_period;
      replace('model-select', evidence.models.map(m => { const option = node('option', m.label); option.value = m.id; return option; }));
      $('model-select').value = selected;
      replace('limitations', evidence.restrictions.map(text => node('li', text)));
      $('content').hidden = false; render(); document.documentElement.dataset.ready = 'true';
    } catch (error) {
      if (serial !== requestSerial) return;
      evidence = null; document.documentElement.dataset.ready = 'false'; $('content').hidden = true; $('load-error').hidden = false;
      console.error('Research evidence unavailable:', error.message);
    } finally { if (serial === requestSerial) $('load-status').hidden = true; }
  }
  $('model-select').addEventListener('change', e => { selected = e.target.value; render(); });
  for (const button of document.querySelectorAll('[data-period]')) button.addEventListener('click', () => { period = button.dataset.period; render(); });
  $('show-equity').addEventListener('click', () => { chartMode = 'equity'; renderChart(); });
  $('show-dd').addEventListener('click', () => { chartMode = 'dd'; renderChart(); });
  $('retry').addEventListener('click', load);
  let resizeTimer;
  window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(renderChart, 100); });
  load();
})();
