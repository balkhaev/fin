"""Read-only audit of saved ledgers; no alternate account or price repair."""
from __future__ import annotations
import argparse
import importlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from research.holding_horizon.reference import load_reference, digest
from research.holding_horizon.study import candidates

EXPECTED = {
    'holding-horizon-20260906': (68, 'fd786db742ddce242151cae621c38d430a74d399f84c19925978cf99b0d4d35b'),
    'channel-scale-20260906': (53, 'b4235bdfda95b01bd8ccaad1914adc1e5c7095d3535fb137de72f5ac08eef751'),
    'channel-budget-20260906': (43, None),
}


def csv_frame(path: Path, columns: list[str]) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=columns)


def review(source: Path, reports: Path, out: Path) -> dict:
    source, reports, out = Path(source), Path(reports), Path(out)
    result = json.loads((reports / 'results.json').read_text())
    proof = json.loads((reports / 'verification.json').read_text())
    count, expected = EXPECTED[result['id']]
    if digest(result) != proof['result_sha256'] or len(result['rows']) != count or (expected and digest(result) != expected):
        raise ValueError('Calculated result identity changed')
    modules, frames, audit, _, pins = load_reference(source, acknowledged=True)
    if audit != result['source'] or ('original_reference_pins' in result and pins != result['original_reference_pins']):
        raise ValueError('Audit source or reference mismatch')
    close = pd.DataFrame({s: frames[s].close for s in ('BTCUSDT', 'ETHUSDT')})
    states = modules['opportunity_runner.study'].states
    if result['id'] == 'holding-horizon-20260906':
        target = candidates(close, states)
    else:
        from research.channel_scale.study import channel_targets
        breakout = importlib.import_module('research.relative_futures.signals').breakout
        channel, _ = channel_targets(frames, breakout)
        if result['id'] == 'channel-scale-20260906':
            target = channel
            target['ratio30_125'] = states(close, max_hours=720, trailing=False, risk_size=False) * (1.25 / 1.5)
        else:
            from research.channel_budget.study import compose
            target = compose(states(close, max_hours=720, trailing=False, risk_size=False) / .75, channel['both_h24'])
    count_fills = count_funding = count_groups = 0; checks = []
    for r in result['rows']:
        key = f"{r['model']}_{r['period']}_{r['start']}"
        f = csv_frame(reports / (key + '_fills.csv'), ['time', 'symbol', 'quantity_delta', 'price', 'fee', 'reason'])
        pay = csv_frame(reports / (key + '_funding.csv'), ['time', 'symbol', 'cashflow', 'quantity'])
        ep = csv_frame(reports / (key + '_episodes.csv'), ['entry_time', 'exit_time', 'net', 'end_balance'])
        f['time'] = pd.to_datetime(f.time, utc=True); pay['time'] = pd.to_datetime(pay.time, utc=True)
        cost = r['costs']
        for symbol in ('BTCUSDT', 'ETHUSDT'):
            own = f[f.symbol == symbol]; price = frames[symbol]
            if len(own):
                hours = pd.DatetimeIndex(own.time)
                expected_price = price.open.reindex(hours).to_numpy() * (1 + np.sign(own.quantity_delta.to_numpy()) * cost['slip'])
                np.testing.assert_allclose(own.price, expected_price, rtol=1e-12, atol=1e-8)
                np.testing.assert_allclose(own.fee, own.quantity_delta.abs() * own.price * cost['fee'], rtol=1e-12, atol=1e-8)
                units = own.quantity_delta / cost['step']
                np.testing.assert_allclose(units, np.round(units), rtol=0, atol=1e-7)
                cap = price.volume.shift().reindex(hours).to_numpy() * cost['participation']
                if not (own.quantity_delta.abs().to_numpy() <= cap + 1e-8).all():
                    raise AssertionError('Fill exceeded previous-hour capacity')
                if not (own.quantity_delta.abs() * own.price >= cost['minimum'] - 1e-8).all():
                    raise AssertionError('Fill below declared minimum')
            changes = own.groupby('time').quantity_delta.sum().sort_index()
            schedule = price[(price.index >= r['start']) & (price.index < r['end_exclusive']) & price.funding_event]
            cumulative = changes.cumsum().to_numpy()
            before = np.searchsorted(changes.index.asi8, schedule.index.asi8, side='left') - 1
            quantity = np.zeros(len(schedule)); valid = before >= 0
            if len(cumulative): quantity[valid] = cumulative[before[valid]]
            held = np.abs(quantity) > 1e-8
            got = pay[pay.symbol == symbol].sort_values('time')
            if not pd.DatetimeIndex(got.time).equals(schedule.index[held]):
                raise AssertionError('Missing or extra funding event')
            expected_payment = -quantity[held] * schedule.mark_open.to_numpy()[held] * schedule.funding_rate.to_numpy()[held]
            np.testing.assert_allclose(got.cashflow, expected_payment, rtol=1e-9, atol=1e-7)
            np.testing.assert_allclose(got.quantity, quantity[held], rtol=0, atol=1e-8)
            if r['accounting_complete'] and abs(changes.sum()) > 1e-8:
                raise AssertionError('Complete account retains contracts')
        groups = list(f[f.reason == 'entry'].groupby('time', sort=True))
        if r['accounting_complete'] and len(groups) != len(ep):
            raise AssertionError('Entries and completed episodes disagree')
        previous_cash = cost['initial']
        for n, (when, group) in enumerate(groups):
            index = close.index.get_loc(when) - 2 - cost['delay']
            w = target[r['model']][index]
            prices = np.array([frames[s].at[when, 'open'] for s in ('BTCUSDT', 'ETHUSDT')]) * (1 + np.sign(w) * cost['slip'])
            desired = np.sign(w) * np.floor(np.abs(w) * previous_cash * cost['gross'] / prices / cost['step']) * cost['step']
            filled = group.groupby('symbol').quantity_delta.sum().reindex(('BTCUSDT', 'ETHUSDT'), fill_value=0.).to_numpy()
            np.testing.assert_allclose(filled, desired, rtol=0, atol=1e-8)
            if n < len(ep): previous_cash = float(ep.iloc[n].end_balance)
        if r['accounting_complete']:
            cash = cost['initial'] - (f.quantity_delta * f.price).sum() - f.fee.sum() + pay.cashflow.sum()
            np.testing.assert_allclose(cash, r['final_balance'], rtol=1e-10, atol=1e-5)
        count_fills += len(f); count_funding += len(pay); count_groups += len(groups)
        checks.append({'model': r['model'], 'period': r['period'], 'start': r['start'], 'fill_rows': len(f), 'funding_rows': len(pay), 'entry_groups': len(groups)})
    evidence = dict(id=result['id'], result_sha256=proof['result_sha256'], reports=count, fill_rows=count_fills,
        funding_rows=count_funding, entry_groups=count_groups, all_saved_events_checked=True,
        funding_quantity_before_same_timestamp_orders=True, entry_signal_delay_checked=True,
        original_source_not_modified=True, no_new_account_simulation=True, actual_exchange_execution_not_verified=True, rows=checks)
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in evidence.items() if k != 'rows'}), flush=True)
    return evidence


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True); p.add_argument('--reports', type=Path, required=True); p.add_argument('--out', type=Path, required=True)
    a = p.parse_args(); review(a.source, a.reports, a.out)
