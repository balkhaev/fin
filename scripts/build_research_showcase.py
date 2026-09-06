"""Build a read-only frontend snapshot from immutable, already computed evidence.

No simulator imports, model loading, network calls, orders or position changes.
The source is the finite PR134 artifact, not production/paper account telemetry.
"""
from __future__ import annotations
import argparse
import csv
import gzip
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

SOURCES = {
    'budget': ('opportunity-first', '09c051ca2e5a779775240aa9a28fd155f0cb615e431090e3b8b0f78dad12239e'),
    'runner': ('runner-first', '2631594c74bf298f3c29fb4ad55ae2a2a9b6cb70b48436ebe1a1dd2a70c105c4'),
    'ladder': ('ladder-first', 'f38757a0bfcc95196cc6c4bff1fcad8194f0a9202473ebea34f3374fc4222e68'),
}
LABELS = {
    'old_pair_1x': ('Исходная пара · 1×', 'control'),
    'old_pair_15x': ('Исходная пара · 1,5×', 'control'),
    'old_pair_2x': ('Исходная пара · 2×', 'control'),
    'pair_risk30_cap15': ('Пара · переменный размер', 'exploratory'),
    'btc_slow_risk30_cap15': ('Медленный тренд BTC', 'exploratory'),
    'multi_risk30_cap15': ('Три стратегии · основной опыт', 'rejected'),
    'multi_risk30_cap2': ('Три стратегии · предел 2×', 'exploratory'),
    'multi_risk20_cap15': ('Три стратегии · меньший риск', 'exploratory'),
    'multi_risk30_no_refresh': ('Три стратегии · без переоценки', 'exploratory'),
    'runner720_15': ('Длительное удержание · 1,5×', 'post_result'),
    'runner720_risk30': ('Длительное удержание · по риску', 'post_result'),
    'runner720_trail15': ('Длительное удержание · trailing', 'post_result'),
    'runner168_trail15': ('Недельное удержание · trailing', 'post_result'),
    'runner720_1x': ('Длительное удержание · 1×', 'post_result'),
    'runner720_125x': ('Длительное удержание · 1,25×', 'post_result'),
}
BASE_PERIODS = ('full', 'later', 'validation')
PERIODS = {
    'full': {'label': '2021 — август 2026', 'start': '2021-01-01', 'end_exclusive': '2026-09-01', 'days': 2069},
    'later': {'label': '2025 — август 2026', 'start': '2025-01-01', 'end_exclusive': '2026-09-01', 'days': 608},
    'validation': {'label': '2024 · отдельный счёт', 'start': '2024-01-01', 'end_exclusive': '2025-01-01', 'days': 366},
}
METRICS = ('start', 'end_exclusive', 'days', 'initial', 'final_balance', 'return_pct', 'cagr_pct',
    'max_mark_close_drawdown_pct', 'completed_episodes', 'order_fills', 'fees', 'funding_cashflow',
    'positive_months', 'negative_months', 'zero_months', 'annual', 'months', 'leverage_audit',
    'episode_statistics', 'additional_risk', 'qualification')


def canonical(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def summarize_curve(path: Path, initial: float) -> list[list[float]]:
    """Daily equity closes plus minimum ACTUAL hourly drawdown within each day.

Metrics in the source report retain hourly mark-price granularity. No missing
curve value is filled. The graph is a display aggregation, not a new backtest.
"""
    buckets: dict[str, list[float]] = {}
    last_time = None
    peak = initial
    first_time = None
    with gzip.open(path, 'rt', newline='') as stream:
        for row in csv.DictReader(stream):
            stamp = datetime.fromisoformat(row['time'].replace('Z', '+00:00'))
            if stamp.utcoffset() != timedelta(0):
                raise ValueError('Curve is not UTC')
            value = float(row['equity'])
            if not math.isfinite(value) or value <= 0 or (last_time and stamp <= last_time):
                raise ValueError('Nonfinite, nonpositive or unordered curve; never repaired')
            first_time = first_time or stamp
            last_time = stamp
            peak = max(peak, value)
            dd = 100 * (value / peak - 1)
            day = (stamp - timedelta(microseconds=1)).date().isoformat()
            prior = buckets.get(day)
            buckets[day] = [int(stamp.timestamp() * 1000), round(value, 6), min(prior[2], dd) if prior else dd]
    if first_time is None:
        raise ValueError('Empty curve')
    start = first_time - timedelta(hours=1)
    return [[int(start.timestamp() * 1000), initial, 0.0]] + list(buckets.values())


def validate_snapshot(snapshot: dict) -> None:
    if snapshot.get('schema_version') != 1 or snapshot.get('live_ready') is not False:
        raise ValueError('Invalid snapshot authority')
    models = snapshot['models']
    if [m['id'] for m in models] != list(LABELS):
        raise ValueError('Unexpected models/order; original control must remain first')
    for model in models:
        if set(model['periods']) != set(BASE_PERIODS):
            raise ValueError('Missing baseline period')
        for name, report in model['periods'].items():
            declared = PERIODS[name]
            if any(report[k] != declared[k] for k in ('start', 'end_exclusive', 'days')):
                raise ValueError('Mismatched account period')
            for key in ('initial', 'final_balance', 'return_pct', 'cagr_pct', 'max_mark_close_drawdown_pct'):
                if not finite(report[key]):
                    raise ValueError('Nonfinite report metric')
            if report['initial'] != 10000 or report['max_mark_close_drawdown_pct'] > 0:
                raise ValueError('Changed starting account or drawdown sign')
            if not report['qualification']['qualified_historical_scenario']:
                raise ValueError('Unqualified source report cannot become a valid dashboard metric')
            series = report['curve']
            if len(series) != declared['days'] + 1:
                raise ValueError('Missing plotted date')
            if not math.isclose(series[-1][1], report['final_balance'], abs_tol=1e-5):
                raise ValueError('Curve and final cash disagree')
            if not math.isclose(min(x[2] for x in series), report['max_mark_close_drawdown_pct'], abs_tol=1e-7):
                raise ValueError('Hourly minimum drawdown not preserved')
            if not math.isclose(100 * (series[-1][1] / report['initial'] - 1), report['return_pct'], abs_tol=1e-7):
                raise ValueError('Total return mismatch')
            if name != 'validation' and report['annual'][-1]['full_year'] is not False:
                raise ValueError('2026 must be labelled partial')


def build(source: Path, repo: Path, *, link_navigation: bool = False) -> dict:
    models = {name: {'id': name, 'label': label, 'status': status, 'periods': {}, 'stresses': {}, 'origins': []}
              for name, (label, status) in LABELS.items()}
    identities = {}
    counts = {}
    for dataset, (folder, expected) in SOURCES.items():
        base = source / folder
        report = json.loads((base / 'results.json').read_text())
        if canonical(report) != expected:
            raise ValueError('Frozen result identity mismatch: ' + dataset)
        identities[dataset] = {'result_sha256': expected, 'ledger_sha256': report['ledger_sha256']}
        counts[dataset] = len(report['rows'])
        for row in report['rows']:
            model = models[row['model']]
            section = row['period']
            item = {key: row[key] for key in METRICS}
            if section in BASE_PERIODS:
                if section in model['periods']:
                    raise ValueError('Duplicate scenario')
                filename = f"{row['model']}_{section}_{row['start']}_equity.csv.gz"
                item['curve'] = summarize_curve(base / filename, row['initial'])
                model['periods'][section] = item
            elif section == 'origin365':
                model['origins'].append(item)
            else:
                model['stresses'][section] = item
    if counts != {'budget': 63, 'runner': 56, 'ladder': 28}:
        raise ValueError('Incorrect scenario coverage')
    snapshot = {
        'schema_version': 1, 'snapshot_date': '2026-09-06', 'live_ready': False,
        'mode': 'historical_read_only', 'periods': PERIODS, 'baseline': 'old_pair_1x',
        'default_model': 'runner720_125x', 'default_period': 'full',
        'source': {'repository': 'balkhaev/fin', 'research_pr': 134,
            'artifact_id': 9991153959, 'artifact_sha256': 'a4f8bf2842ad8550a95353b983231b5f08f541153566bf0e262c39105a67d9af',
            'verification_run': 34039211921, 'identities': identities, 'report_counts': counts},
        'restrictions': ['История многократно использовалась: это не новая независимая проверка.',
            'Известная ошибка расчётного ядра остаётся в draft PR #134; ядро здесь не исполняется.',
            'Результаты — моделируемые фьючерсы BTC/ETH, не реальные сделки и не paper-телеметрия.',
            '500% годовых не подтверждены. Выбранный вариант — для сравнения, не рекомендация.',
            'Начальный номинал не ограничивает последующий дрейф плеча или возможный убыток.',
            'Расходы, маржа и funding-марки заданы сценарием; налоги и все операционные риски не включены.'],
        'models': list(models.values()),
    }
    validate_snapshot(snapshot)
    destination = repo / 'frontend/data'
    destination.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(snapshot, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n'
    (destination / 'research-evidence.json').write_text(raw)
    fields = ['model', 'label', 'period', 'start', 'end_exclusive', 'return_pct', 'cagr_pct',
              'max_mark_close_drawdown_pct', 'completed_episodes', 'fees', 'funding_cashflow']
    with (destination / 'research-comparison.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for model in models.values():
            for period, row in model['periods'].items():
                writer.writerow(dict(model=model['id'], label=model['label'], period=period,
                    **{k: row[k] for k in fields if k not in ('model', 'label', 'period')}))
    stamp = {'schema': 1, 'source_identities': identities, 'snapshot_sha256': hashlib.sha256(raw.encode()).hexdigest(),
             'models': 15, 'baseline_periods': 45, 'source_scenarios': 147, 'trading_code_imported': False}
    (destination / 'research-manifest.json').write_text(json.dumps(stamp, ensure_ascii=False, indent=2) + '\n')
    if link_navigation:
        path = repo / 'frontend/live.html'
        html = path.read_text()
        marker = '    <div class="status-line">'
        link = '      <a class="paper-badge" href="./research.html" aria-label="Открыть проверенные исследования">Исследования ↗</a>'
        if 'href="./research.html"' not in html:
            if html.count(marker) != 1:
                raise ValueError('Navigation anchor changed; no broad replacement permitted')
            path.write_text(html.replace(marker, marker + '\n' + link, 1))
    print(json.dumps(stamp, ensure_ascii=False))
    return snapshot


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--repo', type=Path, default=Path('.'))
    parser.add_argument('--link-navigation', action='store_true')
    options = parser.parse_args()
    build(options.source, options.repo, link_navigation=options.link_navigation)
