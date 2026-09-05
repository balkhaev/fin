"""One-account replay of the predeclared BTC Pressure archive signal profile.

Actual normalized trades and top-five snapshots drive the existing paper Broker.
Sampled days are separate accounts, not adjacent days or an annual strategy record.
No calibration is fabricated and there is no real exchange order path.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any

import numpy as np
import pandas as pd

from .adapters import Event
from .event_study import TYPES, frames, read, validate_book
from .paper import Broker, Settings
from .strategy import Frame, Proposal

DATES = ('2025-09-01', '2026-03-01', '2026-09-01', '2026-08-01')
CONTRACT = {'tick': .1, 'qty_step': .001, 'min_qty': .001, 'min_notional': 100.}
BASE = Settings(taker_fee=.00055)
SCENARIOS = {
    'original_passive': (BASE, False),
    'double_slippage': (replace(BASE, slip=.0002), False),
    'one_second_latency': (replace(BASE, latency_ms=1000), False),
    'double_commission': (replace(BASE, maker_fee=.0004, taker_fee=.0011), False),
    'forced_taker_comparator': (BASE, True),
}


def write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def source_identity() -> str:
    root = Path(__file__).parent
    h = hashlib.sha256()
    for name in ('account_study.py', 'event_study.py', 'strategy.py', 'paper.py', 'adapters.py'):
        h.update(name.encode() + b'\0' + hashlib.sha256((root / name).read_bytes()).digest())
    return h.hexdigest()


def validate_manifest(root: Path, dates: tuple[str, ...]) -> dict:
    """Verify bytes against the frozen manifest; no silent future download."""
    manifest = json.loads((root / 'manifest.json').read_text())
    lookup = {(r['date'], r['venue'], r['kind']): r for r in manifest['files']}
    if len(lookup) != len(manifest['files']):
        raise ValueError('Duplicate manifest identity')
    used = []
    for day in dates:
        for venue, kinds in TYPES.items():
            for kind in kinds:
                row = lookup.get((day, venue, kind))
                if row is None or row['status'] != 'downloaded':
                    raise ValueError(f'Missing mandatory archive: {day} {venue} {kind}')
                path = root / row['filename']
                if path.name != row['filename']:
                    raise ValueError('Unsafe source filename')
                h = hashlib.sha256()
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1 << 20), b''):
                        h.update(chunk)
                if h.hexdigest() != row['sha256']:
                    raise ValueError(f'Checksum mismatch: {path.name}')
                used.append({k: row[k] for k in ('date', 'venue', 'kind', 'filename', 'sha256', 'bytes')})
    return {'files': used, 'manifest_sha256': hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest()}


@dataclass
class Session:
    date: str
    start_ms: int
    end_ms: int
    trace: list[tuple[int, Frame | None, str]]
    proposals: dict[int, Proposal]
    # Columns are fixed, raw provider local receipt times remain microseconds.
    book_local: np.ndarray
    book_exchange: np.ndarray
    book_values: np.ndarray
    trade_local: np.ndarray
    trade_exchange: np.ndarray
    trade_values: np.ndarray
    ticker_local: np.ndarray
    ticker_exchange: np.ndarray
    ticker_funding: np.ndarray
    timeline_type: np.ndarray
    timeline_index: np.ndarray
    timeline_time: np.ndarray
    audit: dict


def timeline(book_us, trade_us, ticker_us, frame_ms):
    """Frames precede an input at exactly their boundary (features exclude it).

    Ticker then book then trade resolve equal local clocks deterministically.
    Equal-book/trade receipt times cannot establish ordering: maker fill is suppressed.
    """
    sequences = [np.asarray(frame_ms, dtype=np.int64)*1000, ticker_us, book_us, trade_us]
    ts = np.concatenate(sequences).astype(np.int64)
    typ = np.concatenate([np.full(len(a), k, dtype=np.int8) for k, a in enumerate(sequences)])
    ix = np.concatenate([np.arange(len(a), dtype=np.int64) for a in sequences])
    # Stable ordering preserves input order of rows within each stream.
    order = np.argsort(ts, kind='stable')
    return typ[order], ix[order], ts[order]


def prepare(root: Path, date: str) -> Session:
    data, audit = {}, {}
    for venue, kinds in TYPES.items():
        for kind in kinds:
            data[venue, kind], audit[f'{venue}:{kind}'] = read(root, date, venue, kind)
    start_ms = int(pd.Timestamp(date, tz='UTC').timestamp()*1000)
    end_ms = start_ms + 86400000
    book = data['bybit', 'book_snapshot_5']
    validate_book(book)
    # Keep invalid/stale books in the broker tape: they must invalidate execution,
    # not silently leave a previous quote alive. Only features filter their latency.
    lag = book.local_timestamp-book.timestamp
    good = (lag <= 5000000) & (lag >= -500000)
    trace = []
    proposals, counters = frames(
        data['bybit','trades'], [data['binance','trades'], data['bybit-spot','trades']],
        book.loc[good].reset_index(drop=True), data['bybit','liquidations'], start_ms*1000,
        observer=lambda t, f, reason: trace.append((t, f, reason)),
    )
    if len(trace) != 86400 or len({t for t, _, _ in trace}) != 86400:
        raise AssertionError('Frame observer did not cover every receive second')
    fields = [f'{side}[{i}].{field}' for side in ('bids','asks') for i in range(5) for field in ('price','amount')]
    t = data['bybit','trades']
    ticker = data['bybit','derivative_ticker']
    ticker_rates = pd.to_numeric(ticker.funding_timestamp, errors='coerce').to_numpy(float)
    valid_funding = np.isnan(ticker_rates) | (np.isfinite(ticker_rates) & (ticker_rates >= 0))
    if not valid_funding.all():
        raise ValueError('Invalid next-funding timestamps')
    b_local, t_local, c_local = [x.local_timestamp.to_numpy(np.int64) for x in (book,t,ticker)]
    typ, ix, times = timeline(b_local, t_local, c_local, [x[0] for x in trace])
    audit.update(features=counters,raw_signals=len(proposals),late_book_rows=int((~good).sum()),
        exact_book_trade_receipt_ties=int(np.isin(t_local,b_local).sum()),
        input_records=sum(v['rows'] for v in audit.values() if isinstance(v,dict) and 'rows' in v))
    return Session(
        date, start_ms, end_ms, trace, {p.time:p for p in proposals},
        b_local, book.timestamp.to_numpy(np.int64), book[fields].to_numpy(float),
        t_local, t.timestamp.to_numpy(np.int64),
        np.column_stack([t.price.to_numpy(float), t.amount.to_numpy(float), np.where(t.side.eq('buy'),1,-1)]),
        c_local, ticker.timestamp.to_numpy(np.int64), ticker_rates,
        typ, ix, times, audit,
    )


class AccountReplay:
    """Adapter around the shared Broker. No independent PnL/fill formula."""
    def __init__(self, session: Session, settings: Settings, force_taker=False):
        self.session = session
        self.broker = Broker(settings, venue='bybit_perp', mode='diagnostic')
        self.force_taker = force_taker
        self.last_book_us = -1
        self.events_skipped = Counter()
        self.reason_counts = Counter()
        self.decisions = []
        self.equity = []
        self.max_exposure = 0.
        self.unpriced_marks = 0
        self.boundary_at = session.end_ms-60000
        self.broker.on_event(Event(session.start_ms,session.start_ms,'bybit_perp','instrument',CONTRACT))

    def _exposure(self):
        b = self.broker
        if b.position and b.fresh():
            eq = b.equity()
            px = b.book['bids'][0][0] if b.position['side']==1 else b.book['asks'][0][0]
            if eq and eq > 0:
                self.max_exposure = max(self.max_exposure,b.position['qty']*px/eq)

    def clock(self, t: int, frame: Frame | None, reason: str):
        b = self.broker
        b.on_event(Event(t,t,'archive_clock','health',{}))
        boundary = t >= self.boundary_at
        if boundary:
            b.cancel('sample_boundary'); b.request_exit('sample_boundary')
        if frame is None:
            if b.pending: b.cancel('feature_quality_lost')
            if b.position: b.request_exit('feature_quality_lost')
        else:
            b.manage_frame(frame)
            p = self.session.proposals.get(t)
            if p:
                if self.force_taker: p=replace(p,passive=False)
                before = len(b.events)
                if not boundary:
                    accepted = b.propose(p)
                else:
                    accepted = False
                details = b.events[before:]
                rejected = next((e['reason'] for e in reversed(details) if e['type']=='entry_blocked'),None)
                self.decisions.append(dict(time_ms=t,family=p.family,side=p.side,passive=p.passive,
                    submitted=bool(accepted),reason=rejected or ('sample_boundary' if boundary else 'submitted' if accepted else 'position_or_risk_or_contract_gate')))
        eq=b.equity()
        if eq is None and b.position: self.unpriced_marks+=1
        if t%10000==0 or boundary:
            self.equity.append(dict(time_ms=t,cash=b.cash,equity=eq,quantity=b.position['qty'] if b.position else 0.))
        self.reason_counts[reason]+=1
        self._exposure()

    def book(self, k: int):
        s,b = self.session,self.broker
        now=int(s.book_local[k]//1000);occurred=int(s.book_exchange[k]//1000)
        v=s.book_values[k]
        payload=dict(bids=[(float(v[i]),float(v[i+1])) for i in range(0,10,2)],
                     asks=[(float(v[i]),float(v[i+1])) for i in range(10,20,2)],
                     sequence=k,depth_kind='provider_top5_not_exchange_sequence')
        b.on_event(Event(now,occurred,'bybit_perp','book',payload))
        self.last_book_us=int(s.book_local[k])
        self._exposure()

    def trade(self, k:int):
        s,b=self.session,self.broker
        # Idle market prints cannot change a flat account. All price-changing
        # book rows and frame clocks still run; feature flow was independently
        # computed from EVERY real print, not this fast path.
        if not b.position and not b.pending:
            self.events_skipped['irrelevant_flat_trade']+=1; return
        now=int(s.trade_local[k]//1000);occurred=int(s.trade_exchange[k]//1000)
        p,q,side=s.trade_values[k]
        if int(s.trade_local[k])==self.last_book_us and b.pending and b.pending['proposal'].passive:
            self.events_skipped['ambiguous_maker_trade_book_tie']+=1
            b.on_event(Event(now,occurred,'archive_clock','health',{})); return
        b.on_event(Event(now,occurred,'bybit_perp','trade',dict(price=float(p),qty=float(q),side=int(side))))
        self._exposure()

    def context(self,k:int):
        s,b=self.session,self.broker
        timestamp=s.ticker_funding[k]
        if not math.isfinite(timestamp):return
        now=int(s.ticker_local[k]//1000);occurred=int(s.ticker_exchange[k]//1000)
        # Only future settlement TIME is a feature. Never turn ticker's forecast
        # rate into a realized payment. Existing Broker tracks obligations.
        next_time=int(timestamp//1000)
        if next_time==b.next_funding and not b.position and not b.pending:return
        b.on_event(Event(now,occurred,'bybit_perp','context',dict(next_funding=next_time)))

    def run(self):
        s=self.session
        for typ,k,us in zip(s.timeline_type,s.timeline_index,s.timeline_time):
            if us<s.start_ms*1000 or us>s.end_ms*1000:continue
            if typ==0:
                self.clock(*s.trace[int(k)])
            elif typ==1:self.context(int(k))
            elif typ==2:self.book(int(k))
            else:self.trade(int(k))
        b=self.broker
        report=b.report()
        counts=Counter(e['type'] for e in report['events'])
        exits=Counter(t['reason'] for t in report['closed_trades'])
        complete=(not report['execution_incomplete'] and not report['open_position_at_end']
                  and not report['pending_entry'] and report['funding_time_drawdown_verified'])
        trades=report['closed_trades']
        wins=sum(t['net'] for t in trades if t['net']>0)
        loss=-sum(t['net'] for t in trades if t['net']<0)
        net=sum(t['net'] for t in trades)
        if complete and not math.isclose(net,report['cash']-report['capital'],abs_tol=1e-7):
            raise AssertionError('Completed session account and closed trades diverged')
        report.update(date=s.date,experiment='single_account_archive_replay',archive_model_sha256=source_identity(),
            primary_signal_profile='existing event_study closed seconds',
            accounting_complete=bool(complete),session_net_return_pct=report['marked_return_pct'] if complete else None,
            gross_closed=sum(t['gross'] for t in trades),fees_closed=sum(t['fees'] for t in trades),
            funding_closed=sum(t['funding'] for t in trades),net_closed=net,
            completed_trade_count=len(trades),win_rate_pct=100*sum(t['net']>0 for t in trades)/len(trades) if trades else None,
            profit_factor=wins/loss if loss>0 else None,event_counts=dict(counts),exit_reasons=dict(exits),
            raw_signals=len(s.proposals),submitted_orders=counts['entry_submitted'],
            max_observed_notional_equity=self.max_exposure,unpriced_clock_marks=self.unpriced_marks,
            skipped_execution_events=dict(self.events_skipped),independent_session_capital=True,
            performance_proven=False,annual_return_pct=None,cagr_pct=None,target_achieved=False,live_ready=False,
            contract_scenario=CONTRACT,historical_contract_filters_verified=False,
            limitations=['One sampled day; never annualized or stitched across unobserved months.',
                'Maker fills are conservative L2 estimates, not actual exchange acknowledgements.',
                'One-second signal clock and delayed finalized trade bars match archive, not raw recorder.',
                'CSV does not certify complete connections or synchronized venue collector clocks.',
                'Sample boundary forces flat; seven-day trend behavior not evaluated across sample gaps.',
                'Funding forecast is not used as payment; held missing settlements invalidate full account result.',
                'Historical fees, quantity filters, network latency and margin liquidation remain scenario assumptions.'])
        return report


def run_study(root:Path,out:Path,dates=DATES,scenarios=None):
    if out.exists():raise FileExistsError('Use a fresh evidence output directory')
    dates=tuple(dates)
    if any(d not in DATES for d in dates):raise ValueError('Date not in frozen protocol')
    selected=tuple(scenarios or SCENARIOS)
    if any(s not in SCENARIOS for s in selected):raise ValueError('Unknown scenario')
    manifest=validate_manifest(root,dates)
    out.mkdir(parents=True)
    write(out/'source_manifest.json',manifest)
    summaries=[];audits=[]
    for day in dates:
        start=time.monotonic()
        print('Preparing frozen signal tape:',day,flush=True)
        session=prepare(root,day)
        audits.append(dict(date=day,**session.audit))
        pd.DataFrame([asdict(p) for p in session.proposals.values()]).to_csv(out/f'{day}_signals.csv',index=False)
        print('Signals:',len(session.proposals),'events:',len(session.timeline_time),'prepare_s:',round(time.monotonic()-start,2),flush=True)
        for name in selected:
            started=time.monotonic();settings,taker=SCENARIOS[name]
            replay=AccountReplay(session,settings,taker);report=replay.run();report['scenario']=name
            prefix=out/f'{day}_{name}'
            write(Path(str(prefix)+'_report.json'),report)
            pd.DataFrame(report['closed_trades']).to_csv(Path(str(prefix)+'_trades.csv'),index=False)
            pd.DataFrame(replay.decisions).to_csv(Path(str(prefix)+'_decisions.csv'),index=False)
            pd.DataFrame(replay.equity).to_csv(Path(str(prefix)+'_equity.csv'),index=False)
            summary={k:v for k,v in report.items() if k not in ('events','closed_trades','limitations')}
            summaries.append(summary)
            print(json.dumps({k:summary[k] for k in ('date','scenario','session_net_return_pct','completed_trade_count','submitted_orders','accounting_complete','exit_reasons')}),
                  'elapsed_s',round(time.monotonic()-started,2),flush=True)
        del session
    result=dict(id='btc-pressure-account-replay-20260905',source_sha256=source_identity(),
                dates=list(dates),scenarios=list(selected),sessions=summaries,data_audit=audits,
                selected_on_profit=False,annual_return_pct=None,cagr_pct=None,
                continuous_days=1,observed_sample_days=len(dates),target_achieved=False,live_ready=False)
    write(out/'results.json',result)
    fields=('date','scenario','session_net_return_pct','accounting_complete','completed_trade_count','submitted_orders',
            'gross_closed','fees_closed','funding_closed','net_closed','win_rate_pct','profit_factor','max_marked_drawdown_pct',
            'max_observed_notional_equity','unpriced_clock_marks')
    pd.DataFrame([{k:s[k] for k in fields} for s in summaries]).to_csv(out/'session_results.csv',index=False)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--dates',nargs='+',choices=DATES,default=list(DATES))
    parser.add_argument('--scenarios',nargs='+',choices=list(SCENARIOS))
    a=parser.parse_args();run_study(a.data,a.out,a.dates,a.scenarios)
