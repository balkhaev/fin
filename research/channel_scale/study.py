"""Signal-clock sensitivity using an already published breakout function.

Only completed signal bars are aggregated; fills/marks/funding remain hourly.
The existing archived accounting code is neither copied into main nor repaired.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from research.holding_horizon.reference import load_reference, digest
from research.holding_horizon.study import STARTS, save

SYMBOLS = ('BTCUSDT', 'ETHUSDT')
CONTROL = 'ratio30_125'; PRIMARY = 'btc_h24'
MODELS = (CONTROL, 'btc_h4', 'btc_h12', PRIMARY, 'both_h4', 'both_h12', 'both_h24')


def completed_bars(frame: pd.DataFrame, hours: int) -> pd.DataFrame:
    if hours not in (4, 12, 24):
        raise ValueError('Only predeclared UTC clocks are allowed')
    if str(frame.index.tz) != 'UTC' or frame.index.has_duplicates:
        raise ValueError('Unique UTC source required')
    if len(frame) > 1 and not np.all(np.diff(frame.index.asi8) == 3600000000000):
        raise ValueError('Do not drop absent-price hours')
    prices = frame[['open', 'high', 'low', 'close']]
    bins = prices.resample(f'{hours}h', origin='epoch', label='left', closed='left')
    bars = bins.agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'})
    good = bins.count().eq(hours).all(axis=1)
    bars.loc[~good, :] = np.nan
    # Index is the instant when the complete bar is knowable, not its start.
    bars.index += pd.Timedelta(hours=hours)
    return bars


def channel_targets(frames: dict, original_breakout):
    index = frames['BTCUSDT'].index
    if set(frames) != set(SYMBOLS) or not frames['ETHUSDT'].index.equals(index):
        raise ValueError('Exactly two aligned original instruments required')
    output, diagnostics = {}, []
    for hours in (4, 12, 24):
        bars = {s: completed_bars(frames[s], hours) for s in SYMBOLS}
        support = pd.concat([b.close for b in bars.values()], axis=1).notna().all(axis=1).rolling(73, min_periods=73).sum().eq(73).to_numpy()
        signs = np.column_stack([original_breakout(bars[s].close, bars[s].high, bars[s].low, support) for s in SYMBOLS])
        released = pd.DataFrame(signs, index=bars['BTCUSDT'].index, columns=SYMBOLS)
        hourly = released.reindex(index + pd.Timedelta(hours=1), method='ffill').fillna(0.).to_numpy(float)
        if not np.isfinite(hourly).all() or not np.isin(hourly, (-1., 0., 1.)).all():
            raise AssertionError('Original function returned invalid states')
        output[f'btc_h{hours}'] = np.column_stack([hourly[:, 0], np.zeros(len(index))])
        output[f'both_h{hours}'] = hourly / np.maximum(np.abs(hourly).sum(axis=1), 1.)[:, None]
        diagnostics.append(pd.DataFrame({'signal_hour': index.astype(str), 'clock_hours': hours,
            'BTC_state': hourly[:, 0], 'ETH_state': hourly[:, 1]}))
    return output, pd.concat(diagnostics, ignore_index=True)


def study(source: Path, out: Path, *, acknowledged: bool = False):
    source, out = Path(source), Path(out)
    if out.exists():
        raise FileExistsError('Fresh output directory required')
    modules, frames, audit, old, pins = load_reference(source, acknowledged=acknowledged)
    breakout = importlib.import_module('research.relative_futures.signals').breakout
    account = modules['relative_futures.account']; report_tools = modules['relative_futures.study']
    risk = modules['opportunity_budget.study']; stats = modules['relative_futures_checks.candidates']
    close = pd.DataFrame({s: frames[s].close for s in SYMBOLS})
    target, trace = channel_targets(frames, breakout)
    target[CONTROL] = modules['opportunity_runner.study'].states(close, max_hours=720, trailing=False, risk_size=False) * (1.25 / 1.5)
    cut = close.index.searchsorted(pd.Timestamp('2025-03-17 07:00', tz='UTC'))
    prefix, _ = channel_targets({s: f.iloc[:cut] for s, f in frames.items()}, breakout)
    for name in prefix:
        np.testing.assert_array_equal(target[name][:cut], prefix[name])
    out.mkdir(parents=True); trace.to_csv(out / 'closed_bar_states.csv.gz', index=False, compression={'method': 'gzip', 'mtime': 0})
    rows, ledgers = [], []; exact = 0
    def run(name, period, start, end='2026-09-01', fee=.0005, slip=.0001, delay=0, initial=10000.):
        nonlocal exact
        cost = account.Costs(gross=2 if name == CONTROL else 1, fee=fee, slip=slip, delay=delay, initial=initial)
        r, f, funding, episodes, curve = account.simulate(frames, target[name], start, end, cost)
        if name == CONTROL and period in ('full', 'later', 'validation'):
            previous = next(x for x in old['rows'] if x['model'] == 'runner720_125x' and x['period'] == period)
            for k, v in r.items():
                if v != previous[k]:
                    raise AssertionError('Old exact baseline changed: ' + k)
            exact += 1
        q = report_tools.qualify(r, curve); leverage = risk.leverage_audit(frames, f, curve, cost)
        if not leverage['verified']:
            q['qualified_historical_scenario'] = False; q['issues'].append('leverage_audit_failed')
        row = dict(r, model=name, period=period, qualification=q, leverage_audit=leverage,
            additional_risk=risk.annual_checks(curve, initial), episode_statistics=stats.episode_statistics(episodes, curve, r),
            independent_cash=report_tools.independent_trade_replay(f, funding, r['final_balance'], initial, r['terminal_quantities']))
        key = f'{name}_{period}_{start}'
        for suffix, data in (('fills', f), ('funding', funding), ('episodes', episodes)):
            data.to_csv(out / f'{key}_{suffix}.csv', index=False)
        curve.to_csv(out / f'{key}_equity.csv.gz', index=False, compression={'method': 'gzip', 'mtime': 0})
        rows.append(row); ledgers.append({'key': key, 'fills': f.to_dict('records'), 'funding': funding.to_dict('records')})
        print('SCENARIO', json.dumps({k: row[k] for k in ('model', 'period', 'start', 'return_pct', 'cagr_pct', 'max_mark_close_drawdown_pct', 'completed_episodes')}), flush=True)
    for period, start, end in (('full', '2021-01-01', '2026-09-01'), ('later', '2025-01-01', '2026-09-01'), ('validation', '2024-01-01', '2025-01-01')):
        run(CONTROL, period, start, end)
    if exact != 3:
        raise AssertionError('Baseline reports missing')
    for name in MODELS:
        if name != CONTROL:
            run(name, 'full', '2021-01-01'); run(name, 'later', '2025-01-01'); run(name, 'validation', '2024-01-01', '2025-01-01')
        run(name, 'later_double_costs', '2025-01-01', fee=.001, slip=.0002)
        run(name, 'later_delay2', '2025-01-01', delay=2)
    for name in (PRIMARY, CONTROL):
        run(name, 'full_double_costs', '2021-01-01', fee=.001, slip=.0002)
        run(name, 'later_capital1000', '2025-01-01', initial=1000.)
        for start, end in STARTS:
            run(name, 'origin365', start, end)
    if len(rows) != 53:
        raise AssertionError('Frozen coverage changed')
    get = lambda name, p: next(r for r in rows if r['model'] == name and r['period'] == p)
    pf, pl = get(PRIMARY, 'full'), get(PRIMARY, 'later')
    positive = lambda r: r['qualification']['qualified_historical_scenario'] and r['return_pct'] is not None and r['return_pct'] > 0
    admission = {'primary_full_later_cost_positive': all(positive(get(PRIMARY, p)) for p in ('full', 'later', 'later_double_costs')),
        'full_drawdown_within30': pf['max_mark_close_drawdown_pct'] >= -30,
        'later_drawdown_within20': pl['max_mark_close_drawdown_pct'] >= -20,
        'all_full_calendar_years_positive': bool(pf['annual']) and all(x['return_pct'] > 0 for x in pf['annual'] if x['full_year']),
        'at_least20_later_episodes': pl['completed_episodes'] >= 20}
    origins = {}
    for name in (PRIMARY, CONTROL):
        valid = [r for r in rows if r['model'] == name and r['period'] == 'origin365' and r['qualification']['qualified_historical_scenario']]
        origins[name] = dict(total=7, qualified=len(valid), positive=sum(r['return_pct'] > 0 for r in valid), negative=sum(r['return_pct'] < 0 for r in valid), worst_return_pct=min((r['return_pct'] for r in valid), default=None))
    result = dict(id='channel-scale-20260906', primary=PRIMARY, control=CONTROL, rows=rows, admission=admission,
        admitted=all(admission.values()), origin_sensitivity=origins, source=audit, original_reference_pins=pins,
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), ledger_sha256=digest(ledgers), exact_original_reports=exact,
        real_history_channel_prefixes=6, completed_bar_release_only=True, new_algorithm_implementation=False,
        post_holding_result_experiment=True, original_account_unpatched_outside_main=True, beta_policy_used=False,
        independent_holdout=False, local_execution=False, real_orders=0, live_ready=False, stable500proven=False,
        limitations=['Same original breakout code on longer fully closed signal bars; not a new beta model or account repair.',
            'Input bar frequency was selected before this stage, after earlier research; economic history is reused.',
            'Trades and funding retain actual original hourly observations, not aggregated execution prices.',
            'Directional BTC risk and1x entrygross differ from1.25x relative control; not a same-risk comparison.',
            'No future winning model promotion, no guaranteed loss/nominal cap or validated actual exchange margin tiers.',
            'Known archival valuation defect is unchanged; this study requires a complete verified source and quarantines bad paths.',
            'Taxes, exact fee tiers, orderbooks, settlement marks, custody, infrastructure and USDT risks remain limitations.'])
    save(out / 'results.json', result)
    fields = ('model', 'period', 'start', 'return_pct', 'cagr_pct', 'max_mark_close_drawdown_pct', 'completed_episodes', 'order_fills', 'fees', 'funding_cashflow')
    pd.DataFrame([dict(**{k: r[k] for k in fields}, max_gross=r['leverage_audit'].get('max_mark_close_gross'), qualified=r['qualification']['qualified_historical_scenario']) for r in rows]).to_csv(out / 'comparison.csv', index=False)
    pd.DataFrame([dict(model=r['model'], period=r['period'], start=r['start'], **a) for r in rows for a in r['annual']]).to_csv(out / 'annual.csv', index=False)
    save(out / 'verification.json', dict(result_sha256=digest(result), ledger_sha256=result['ledger_sha256'], reports=53,
        qualified=sum(r['qualification']['qualified_historical_scenario'] for r in rows), exact_original_reports=exact))
    print('ADMISSION', json.dumps(admission), flush=True); print('ORIGINS', json.dumps(origins), flush=True)
    for name in (PRIMARY, 'both_h24', CONTROL):
        print('ANNUAL', name, json.dumps(get(name, 'full')['annual']), flush=True)
    print('VERIFY', (out / 'verification.json').read_text(), flush=True)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--source', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True); p.add_argument('--allow-archived-reference', action='store_true')
    a = p.parse_args(); study(a.source, a.out, acknowledged=a.allow_archived_reference)
