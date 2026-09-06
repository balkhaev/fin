"""Finite cash-gap research on the unchanged cash-and-coin simulator.

One account per row; a sleeve-only account is a diagnostic, never added to another
account's equity. No forecasting, parameters or evidence are changed after results.
"""
from pathlib import Path
import argparse
import hashlib
import json
import math
import numpy as np
import pandas as pd
from research.annual_rotation.data import load as load_spot, SYMBOLS
from research.annual_rotation.model import Config, Costs, simulate
from research.rotation_venue_transfer.data import load as load_okx
from research.guard_execution_audit.study import PINS, audit, monthly, digest, write
from .targets import build, PRIMARY, NAMES, cadence

CONTROL = 'core_weekly'
PERIODS = (('full', '2021-01-01', '2026-09-01'),
           ('validation', '2024-01-01', '2025-01-01'),
           ('later', '2025-01-01', '2026-09-01'))


def run_lengths(boolean):
    longest = current = 0
    for value in boolean:
        current = current+1 if value else 0
        longest = max(longest, current)
    return longest


def gates(full, later, control_full, control_later, stresses, origins, control_origins):
    complete_origins = [r for r in origins if r['accounting_complete']]
    old_complete = [r for r in control_origins if r['accounting_complete']]
    origin_ok = (len(complete_origins) == len(origins) == 19 and len(old_complete) == 19
        and sum(r['return_pct'] > 0 for r in complete_origins) >= sum(r['return_pct'] > 0 for r in old_complete)
        and min(r['return_pct'] for r in complete_origins) >= min(r['return_pct'] for r in old_complete))
    return dict(complete_full_and_later=all(r['accounting_complete'] for r in (full, later, control_full, control_later)),
        higher_full_CAGR=full['cagr_pct'] is not None and full['cagr_pct'] > control_full['cagr_pct'],
        higher_later_return=later['return_pct'] is not None and later['return_pct'] > control_later['return_pct'],
        full_drawdown_within_two_points=full['max_close_drawdown_pct'] >= control_full['max_close_drawdown_pct']-2,
        positive_later_stresses=all(r['accounting_complete'] and r['return_pct'] > 0 for r in stresses),
        more_closed_later_asset_positions=later['closed_asset_positions'] > control_later['closed_asset_positions'],
        no_worse_origin_results=origin_ok)


def relative_growth(primary_curve, control_curve, diagnostics, start):
    """Partition observed relative log growth by delayed signal regime.

This is an identity for the paired equity paths, not an estimated causal effect,
not sleeve PnL and not a new counterfactual account. Regime is intent, not fills.
"""
    if not primary_curve.time.equals(control_curve.time):
        raise ValueError('Different daily account windows')
    p = np.r_[10000., primary_curve.equity.to_numpy(float)]
    c = np.r_[10000., control_curve.equity.to_numpy(float)]
    if not (np.isfinite(p).all() and np.isfinite(c).all() and (p > 0).all() and (c > 0).all()):
        return {'valid': False}
    delta = np.diff(np.log(p))-np.diff(np.log(c))
    dates = pd.DatetimeIndex(pd.to_datetime(primary_curve.time))-pd.Timedelta(days=1)
    state = pd.Series(diagnostics.core_market_allowed.to_numpy(bool),
        index=pd.to_datetime(diagnostics.signal_date, utc=True)).reindex(dates-pd.Timedelta(days=2))
    if state.isna().any():
        raise ValueError('Missing delayed regime state')
    regime = state.to_numpy(bool)
    parts = {'core_signal_on': float(delta[regime].sum()), 'core_signal_off': float(delta[~regime].sum())}
    total = float(delta.sum())
    if not math.isclose(total, math.log(p[-1]/c[-1]), abs_tol=1e-10):
        raise AssertionError('Relative-growth partition does not reconcile')
    return dict(valid=True, relative_final_wealth_pct=float((p[-1]/c[-1]-1)*100),
        log_growth_difference=total, partition_log_growth=parts,
        signal_on_days=int(regime.sum()), signal_off_days=int((~regime).sum()),
        sum_of_separate_account_profits=False, delayed_regime_not_actual_holdings=True)


def study(source, out):
    source = Path(source); out = Path(out)
    if out.exists():
        raise FileExistsError('Fresh result directory required')
    here = Path(__file__).parents[1]
    for path, wanted in PINS.items():
        if hashlib.sha256((here/path).read_bytes()).hexdigest() != wanted:
            raise ValueError('Original source changed: '+path)
    b, ba = load_spot(source/'prior/rotation-data')
    o, oa = load_okx(source/'new-source/okx-data')
    if ba['manifest_sha256'] != 'da9ca6d1e782e8ef6c816390ef3e6ea363eec53a67f58592a8505d754bf5bfe2':
        raise ValueError('Unexpected source snapshot')
    old = json.loads((source/'prior/static-report/results.json').read_text())
    if digest(old) != '6c6dc96296f0281168e6670ff3231fe47c6bb46bb92e5cc2e4bd31c7e8b6a26a':
        raise ValueError('Unexpected old-control evidence')
    prior_candidate = json.loads((here/'guard_execution_audit/verified_summary.json').read_text())['post_result_candidate']
    bt, bd = build(b); ot, od = build(o)
    out.mkdir(parents=True)
    bd.to_csv(out/'binance_regime_diagnostics.csv', index=False)
    od.to_csv(out/'okx_regime_diagnostics.csv', index=False)
    rows = []; ledgers = []; selected_curves = {}; exact_controls = 0
    def replay(name, period, start, end, frames, target, fee=.001, slip=.0005, delay=0, capital=10000.):
        nonlocal exact_controls
        cost = Costs(fee=fee, slip=slip, extra_delay=delay, initial=capital,
            allocation=.25 if name == 'pr132_budget25_every3' else 1.)
        r, f, c = simulate(frames, target[name], Config('raw', 126, 3, cadence(name)), start, end, cost)
        if name == CONTROL and frames is b and period in ('full', 'later'):
            previous = next(x for x in old['results'] if x['policy'] == 'guarded_ensemble20' and x['period'] == period)
            for k, value in r.items():
                if value != previous[k]:
                    raise AssertionError('Old control differs: '+k)
            exact_controls += 1
        if name == 'pr132_budget25_every3' and frames is b and period in ('full', 'later'):
            for key, suffix in [('return_pct', 'return_pct'), ('cagr_pct', 'CAGR_pct'),
                    ('max_close_drawdown_pct', 'close_DD_pct')]:
                if r[key] != prior_candidate[period+'_'+suffix]:
                    raise AssertionError('PR132 candidate differs')
            exact_controls += 1
        reconciliation = audit(f, r, frames, cost, end)
        m = monthly(c, capital)
        invested = c.invested_assets.to_numpy() > 0
        row = dict(r, policy=name, period=period, venue='binance' if frames is b else 'okx',
            cash_audit=reconciliation, monthly=m,
            positive_months=sum(x['return_pct'] > 1e-10 for x in m),
            zero_months=sum(abs(x['return_pct']) <= 1e-10 for x in m),
            negative_months=sum(x['return_pct'] < -1e-10 for x in m),
            invested_days=int(invested.sum()), flat_days=int((~invested).sum()),
            longest_flat_run_days=run_lengths(~invested))
        key = name+'_'+period+'_'+start
        f.to_csv(out/f'{key}_fills.csv', index=False)
        c.to_csv(out/f'{key}_equity.csv', index=False)
        ledgers.append({'key': key, 'fills': f.to_dict('records')}); rows.append(row)
        if name in (PRIMARY, CONTROL) and period in ('full', 'later'):
            selected_curves[name, period] = c
        return row
    for name in NAMES:
        for period, start, end in PERIODS:
            replay(name, period, start, end, b, bt)
        replay(name, 'later_okx', '2025-01-01', '2026-09-01', o, ot)
    for name in (PRIMARY, CONTROL):
        for period, start, fee, slip, delay, capital in (
            ('later_double_cost', '2025-01-01', .002, .001, 0, 10000.),
            ('later_delay', '2025-01-01', .001, .0005, 1, 10000.),
            ('later_capital1000', '2025-01-01', .001, .0005, 0, 1000.),
            ('full_double_cost', '2021-01-01', .002, .001, 0, 10000.)):
            replay(name, period, start, '2026-09-01', b, bt, fee, slip, delay, capital)
        for date in pd.date_range('2021-01-01', '2025-07-01', freq='QS', tz='UTC'):
            replay(name, 'origin365', str(date.date()), str((date+pd.Timedelta(days=365)).date()), b, bt)
    pick = lambda name, period: next(x for x in rows if x['policy'] == name and x['period'] == period)
    origins = {name: [r for r in rows if r['policy'] == name and r['period'] == 'origin365'] for name in (PRIMARY, CONTROL)}
    starts = {}
    for name, collection in origins.items():
        complete = [r for r in collection if r['accounting_complete']]
        starts[name] = dict(total=len(collection), complete=len(complete),
            positive=sum(r['return_pct'] > 0 for r in complete),
            zero=sum(r['return_pct'] == 0 for r in complete), negative=sum(r['return_pct'] < 0 for r in complete),
            worst_return_pct=min(r['return_pct'] for r in complete) if complete else None)
    accepted = gates(pick(PRIMARY, 'full'), pick(PRIMARY, 'later'), pick(CONTROL, 'full'), pick(CONTROL, 'later'),
        [pick(PRIMARY, k) for k in ('later_double_cost', 'later_delay', 'later_okx', 'later_capital1000')],
        origins[PRIMARY], origins[CONTROL])
    attribution = {period: relative_growth(selected_curves[PRIMARY, period], selected_curves[CONTROL, period], bd, start)
        for period, start in [('full', '2021-01-01'), ('later', '2025-01-01')]}
    full = pick(PRIMARY, 'full')
    annual500 = bool(full['accounting_complete'] and full['cagr_pct'] >= 500 and
        all(x['return_pct'] is not None and x['return_pct'] >= 500 for x in full['annual'] if x['full_year']))
    result = dict(id='cash-gap-20260906', primary=PRIMARY, control=CONTROL, rows=rows, origin_sensitivity=starts,
        regime_relative_growth=attribution, admission=accepted, admitted=all(accepted.values()),
        observed_annual500_conditions=annual500, stable_future_profit_proven=False, live_ready=False, real_orders=0,
        data={'binance': ba, 'okx': oa}, exact_old_control_reports=exact_controls,
        original_source_pins=PINS, source_sha256={f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in Path(__file__).parent.glob('*.py')},
        ledger_sha256=digest(ledgers), new_market_holdout=False,
        limitations=['Same repeatedly researched dates and nine surviving assets; not pristine out-of-sample.',
            'Mutually exclusive targets do not guarantee mutually exclusive actual holdings between scheduled executions.',
            'No leverage or funding receipts. Target budget is not a stop or loss guarantee.',
            'Sleeve-only and core-only returns cannot be added into a valid combined cash account.',
            'The attribution partitions observed relative log growth, not causal alpha or separately funded profits.',
            'Native residual, minimum-notional and capacity rules retained; incomplete reports cannot certify returns.',
            'Daily OHLC and fixed execution costs are scenarios, not actual exchange fills.',
            'Taxes, custody, infrastructure, stablecoin and exchange failure risk omitted.'])
    if len(rows) != 98 or exact_controls != 4:
        raise AssertionError('Unexpected study coverage')
    write(out/'results.json', result)
    fields = ('policy', 'period', 'start', 'venue', 'return_pct', 'diagnostic_return_pct', 'cagr_pct',
        'max_close_drawdown_pct', 'worst_rolling_365_pct', 'order_fills', 'closed_asset_positions', 'fees',
        'invested_days', 'flat_days', 'positive_months', 'zero_months', 'negative_months', 'accounting_complete')
    pd.DataFrame([{k: r[k] for k in fields} for r in rows]).to_csv(out/'comparison.csv', index=False)
    pd.DataFrame([dict(policy=r['policy'], period=r['period'], start=r['start'], **y) for r in rows for y in r['annual']]).to_csv(out/'annual.csv', index=False)
    write(out/'verification.json', dict(result_sha256=digest(result), ledger_sha256=result['ledger_sha256'],
        report_count=len(rows), fill_ledger_count=len(ledgers), exact_controls=exact_controls))
    print('COMPARISON\n'+pd.DataFrame([{k:r[k] for k in fields} for r in rows if r['period'] != 'origin365']).to_csv(index=False), flush=True)
    print('ORIGINS', json.dumps(starts), flush=True)
    print('ADMISSION', json.dumps(accepted), flush=True)
    print('ATTRIBUTION', json.dumps(attribution), flush=True)
    print('ANNUAL_PRIMARY', json.dumps(full['annual']), flush=True)
    print('VERIFY', (out/'verification.json').read_text(), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    study(args.source, args.out)
