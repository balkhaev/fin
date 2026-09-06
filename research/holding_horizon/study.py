"""Finite sensitivity study of an UNCHANGED archived signal function.

This module does not implement beta signals, fix a valuation engine or place
orders. It explicitly calls the pinned previously published historical reference.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from .reference import load_reference, digest

HORIZONS = {'hold14': 336, 'hold30': 720, 'hold60': 1440, 'hold90': 2160, 'signal_only': 1000000000}
BUDGETS = {'100': 1., '125': 1.25}
PRIMARY = 'hold90_125'
CONTROL = 'hold30_125'
MODELS = tuple(f'{label}_{size}' for label in HORIZONS for size in BUDGETS)
STARTS = (('2021-01-01', '2022-01-01'), ('2022-01-01', '2023-01-01'),
    ('2023-01-01', '2024-01-01'), ('2025-01-01', '2026-01-01'),
    ('2022-07-01', '2023-07-01'), ('2023-07-01', '2024-06-30'), ('2024-07-01', '2025-07-01'))


def save(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def candidates(close: pd.DataFrame, original_states):
    """Only declared arguments of the existing function; no new entry/exit logic."""
    result = {}
    for label, hours in HORIZONS.items():
        unchanged = original_states(close, max_hours=hours, trailing=False, risk_size=False)
        for size, gross in BUDGETS.items():
            result[f'{label}_{size}'] = unchanged * (gross / 1.5)
    return result


def assess(primary: dict, control: dict, origins: dict) -> dict:
    full, later = primary['full'], primary['later']
    qualified = lambda r: bool(r['qualification']['qualified_historical_scenario'])
    pstart, cstart = origins[PRIMARY], origins[CONTROL]
    return {
        'qualified_primary': all(qualified(primary[k]) for k in ('full', 'later', 'later_double_costs', 'later_delay2')),
        'full_CAGR_above_control': full['cagr_pct'] is not None and control['full']['cagr_pct'] is not None and full['cagr_pct'] > control['full']['cagr_pct'],
        'later_net_above_control': later['return_pct'] is not None and control['later']['return_pct'] is not None and later['return_pct'] > control['later']['return_pct'],
        'full_drawdown_at_most30': full['max_mark_close_drawdown_pct'] >= -30.,
        'later_drawdown_at_most15': later['max_mark_close_drawdown_pct'] >= -15.,
        'later_stress_positive': all(qualified(primary[k]) and primary[k]['return_pct'] is not None and primary[k]['return_pct'] > 0 for k in ('later_double_costs', 'later_delay2')),
        'at_least20_later_episodes': later['completed_episodes'] >= 20,
        'origins_no_worse': pstart['qualified'] == cstart['qualified'] == 7 and pstart['negative'] <= cstart['negative'] and pstart['worst_return_pct'] >= cstart['worst_return_pct'],
    }


def study(source: Path, out: Path, *, acknowledged: bool = False) -> dict:
    source, out = Path(source), Path(out)
    if out.exists():
        raise FileExistsError('Use a fresh evidence output directory')
    modules, frames, audit, old, pins = load_reference(source, acknowledged=acknowledged)
    account = modules['relative_futures.account']; review = modules['relative_futures.study']
    risk = modules['opportunity_budget.study']; stats = modules['relative_futures_checks.candidates']
    states = modules['opportunity_runner.study'].states
    close = pd.DataFrame({s: frames[s].close for s in ('BTCUSDT', 'ETHUSDT')})
    target = candidates(close, states)
    # Actual-market prefix test at a noncalendar boundary before financial results.
    cut = close.index.searchsorted(pd.Timestamp('2025-03-17 07:00', tz='UTC'))
    prefix = candidates(close.iloc[:cut], states)
    for name in MODELS:
        np.testing.assert_array_equal(target[name][:cut], prefix[name])
        if not np.isfinite(target[name]).all() or (np.abs(target[name]).sum(axis=1) > .625 + 1e-12).any():
            raise AssertionError('Unexpected target gross or support: ' + name)
    out.mkdir(parents=True)
    rows, ledgers = [], []; exact = 0
    def run(name, period, start, end='2026-09-01', fee=.0005, slip=.0001, delay=0, initial=10000.):
        nonlocal exact
        cost = account.Costs(gross=2, fee=fee, slip=slip, delay=delay, initial=initial)
        r, fills, funding, episodes, curve = account.simulate(frames, target[name], start, end, cost)
        if name == CONTROL and period in ('full', 'later', 'validation'):
            previous = next(x for x in old['rows'] if x['model'] == 'runner720_125x' and x['period'] == period)
            for key, value in r.items():
                if value != previous[key]:
                    raise AssertionError('Original complete financial report changed: ' + key)
            exact += 1
        qualification = review.qualify(r, curve)
        leverage = risk.leverage_audit(frames, fills, curve, cost)
        if not leverage['verified']:
            qualification['qualified_historical_scenario'] = False
            qualification['issues'].append('leverage_path_not_verified')
        row = dict(r, model=name, period=period, qualification=qualification, leverage_audit=leverage,
            additional_risk=risk.annual_checks(curve, initial), episode_statistics=stats.episode_statistics(episodes, curve, r),
            independent_cash=review.independent_trade_replay(fills, funding, r['final_balance'], initial, r['terminal_quantities']))
        key = f'{name}_{period}_{start}'
        for suffix, frame in (('fills', fills), ('funding', funding), ('episodes', episodes)):
            frame.to_csv(out / f'{key}_{suffix}.csv', index=False)
        curve.to_csv(out / f'{key}_equity.csv.gz', index=False, compression={'method': 'gzip', 'mtime': 0})
        rows.append(row); ledgers.append({'key': key, 'fills': fills.to_dict('records'), 'funding': funding.to_dict('records')})
        shown = ('model', 'period', 'start', 'return_pct', 'cagr_pct', 'max_mark_close_drawdown_pct', 'completed_episodes')
        print('RESULT', json.dumps({k: row[k] for k in shown}), flush=True)
    # Confirm old baseline on all three independent-account periods first.
    for label, start, end in (('full', '2021-01-01', '2026-09-01'), ('later', '2025-01-01', '2026-09-01'), ('validation', '2024-01-01', '2025-01-01')):
        run(CONTROL, label, start, end)
    if exact != 3:
        raise AssertionError('Original control verification incomplete')
    for name in MODELS:
        if name != CONTROL:
            run(name, 'full', '2021-01-01'); run(name, 'later', '2025-01-01')
            run(name, 'validation', '2024-01-01', '2025-01-01')
        run(name, 'later_double_costs', '2025-01-01', fee=.001, slip=.0002)
        run(name, 'later_delay2', '2025-01-01', delay=2)
    for name in (PRIMARY, CONTROL):
        run(name, 'full_double_costs', '2021-01-01', fee=.001, slip=.0002)
        run(name, 'later_capital1000', '2025-01-01', initial=1000.)
        for start, end in STARTS:
            run(name, 'origin365', start, end)
    if len(rows) != 68:
        raise AssertionError('Frozen scenario count differs')
    origins = {}
    for name in (PRIMARY, CONTROL):
        selected = [r for r in rows if r['model'] == name and r['period'] == 'origin365']
        valid = [r for r in selected if r['qualification']['qualified_historical_scenario']]
        origins[name] = dict(total=len(selected), qualified=len(valid), positive=sum(r['return_pct'] > 0 for r in valid),
            negative=sum(r['return_pct'] < 0 for r in valid), worst_return_pct=min((r['return_pct'] for r in valid), default=None))
    group = lambda name: {r['period']: r for r in rows if r['model'] == name and r['period'] != 'origin365'}
    gates = assess(group(PRIMARY), group(CONTROL), origins)
    result = dict(id='holding-horizon-20260906', primary=PRIMARY, control=CONTROL, rows=rows,
        admission=gates, admitted=all(gates.values()), origin_sensitivity=origins, source=audit,
        original_reference_pins=pins, source_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')},
        ledger_sha256=digest(ledgers), exact_original_reports=exact, actual_market_prefixes_verified=len(MODELS),
        execution='finite_offline_research', local_execution=False, original_account_still_unpatched=True,
        beta_policy_used=False, code_in_main_excludes_reference_engine=True, real_orders=0, live_ready=False, stable500proven=False,
        limitations=['Existing signal only: ten frozen horizon-size comparisons on repeatedly observed market history.',
            'The ninety-day variant remains primary irrespective of which row looks better after results.',
            'Entry gross is not a continuous gross or loss cap; actual marked leverage is reported.',
            'Saved reference has a known inactive-mark missing-value defect; unchanged outside main and never made live-ready.',
            'Fully observed tested data avoids that defect only on this snapshot. No prices are invented or interpolated.',
            'Funding settlement marks, costs, margin and paired execution are research approximations, not actual venue histories.',
            'More holding time can concentrate profit in fewer events. Annual losses and overlapping starts remain visible.',
            'Tax, infrastructure, custody, exchange and USDT risks are not comprehensively modeled.'])
    save(out / 'results.json', result)
    fields = ('model', 'period', 'start', 'return_pct', 'cagr_pct', 'max_mark_close_drawdown_pct',
        'completed_episodes', 'order_fills', 'fees', 'funding_cashflow')
    pd.DataFrame([dict(**{k: r[k] for k in fields}, max_gross=r['leverage_audit'].get('max_mark_close_gross'),
        qualified=r['qualification']['qualified_historical_scenario']) for r in rows]).to_csv(out / 'comparison.csv', index=False)
    pd.DataFrame([dict(model=r['model'], period=r['period'], start=r['start'], **a) for r in rows for a in r['annual']]).to_csv(out / 'annual.csv', index=False)
    save(out / 'verification.json', dict(result_sha256=digest(result), ledger_sha256=result['ledger_sha256'],
        reports=len(rows), qualified=sum(r['qualification']['qualified_historical_scenario'] for r in rows), exact_original_reports=exact))
    print('ADMISSION', json.dumps(gates), flush=True); print('ORIGINS', json.dumps(origins), flush=True)
    print('VERIFICATION', (out / 'verification.json').read_text(), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True); parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--allow-archived-reference', action='store_true', help='Explicit offline use of the pinned reference; its known defect is not fixed')
    args = parser.parse_args(); study(args.source, args.out, acknowledged=args.allow_archived_reference)
