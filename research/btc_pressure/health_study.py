"""Paired, precommitted feed-health experiment around the existing paper Broker.

Original entry signals, costs, stops and fill formulas remain untouched. Six
separated session accounts cannot establish a continuous annual return.
"""
from __future__ import annotations
import argparse
from collections import Counter
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
from .account_study import AccountReplay, BASE, Session, prepare, validate_manifest, write
from .adapters import Event
from .event_study import read, confirmed_bars
from .feed_health import SourceObservation, SPOTS, quiet_hold

DATES = ('2025-09-01', '2026-03-01', '2026-08-01', '2026-09-01', '2026-04-01', '2026-07-01')
POLICIES = ('original', 'quote_witnessed_hold')
EXECUTIONS = {'base': BASE, 'joint_stress': replace(BASE, latency_ms=1000, slip=.0002)}


def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''): h.update(block)
    return h.hexdigest()


def identity():
    root = Path(__file__).parent
    names = ('health_study.py','feed_health.py','account_study.py','event_study.py','strategy.py','paper.py','adapters.py')
    return hashlib.sha256(json.dumps({n:digest_file(root/n) for n in names},sort_keys=True).encode()).hexdigest()


def prior_indices(received_us, decision_ms):
    received_us = np.asarray(received_us, dtype=np.int64)
    if np.any(np.diff(received_us) < 0): raise ValueError('Reversed source clock')
    return np.searchsorted(received_us, np.asarray(decision_ms,dtype=np.int64)*1000, side='left')-1


def trade_witnesses(local, occurred, decisions):
    valid = (local-occurred <= 5000000) & (occurred-local <= 500000)
    accepted = local[valid]
    i = prior_indices(accepted, decisions)
    return np.where(i>=0, accepted[np.maximum(i,0)]/1000., np.nan) if len(accepted) else np.full(len(decisions),np.nan)


@dataclass
class HealthTape:
    times: np.ndarray
    perp_trade_ms: np.ndarray
    trade_ms: dict
    quote_ms: dict
    quote_valid: dict
    source_audit: dict
    other_features_ready: np.ndarray | None = None

    def at(self, t):
        k = int(np.searchsorted(self.times,t))
        if k>=len(self.times) or self.times[k]!=t: raise ValueError('No matching health observation')
        return float(self.perp_trade_ms[k]), {
            s:SourceObservation(s,float(self.trade_ms[s][k]),float(self.quote_ms[s][k]),bool(self.quote_valid[s][k])) for s in SPOTS}, bool(self.other_features_ready[k]) if self.other_features_ready is not None else False


def health_tape(root:Path, session:Session) -> HealthTape:
    times = np.array([t for t,_,_ in session.trace],dtype=np.int64)
    perp = trade_witnesses(session.trade_local,session.trade_exchange,times)
    # stale_trade is checked before bar/thin-history faults by the legacy frame
    # builder. Verify those secondary prerequisites independently, not by reason alone.
    previous = trade_witnesses(session.trade_local,session.trade_exchange,times-10000)
    tb = pd.DataFrame({'timestamp':session.trade_exchange,'local_timestamp':session.trade_local,
                       'price':session.trade_values[:,0]})
    bars = confirmed_bars(tb,session.start_ms*1000)
    bi = (((times-session.start_ms)-5000)//60000)-1
    bv = bars[['atr','range_high','range_low']].to_numpy(float)
    safe = np.clip(bi,0,len(bv)-1)
    other = ((bi>=60)&(bi<len(bv))&np.isfinite(bv[safe]).all(axis=1)&(bv[safe,0]>0)
             &np.isfinite(previous)&(times-previous<=20000))
    del tb,bars,bv
    trades,quotes,valids,audit = {},{},{},{}
    manifest = json.loads((root/'manifest.json').read_text())
    lookup={(r['date'],r['venue'],r['kind']):r for r in manifest['files']}
    if len(lookup)!=len(manifest['files']): raise ValueError('Duplicate source identity')
    for source in SPOTS:
        row=lookup.get((session.date,source,'book_snapshot_5'))
        if row is None or row['status']!='downloaded': raise ValueError('Missing spot quote archive')
        name=row['filename']
        if Path(name).name!=name or digest_file(root/name)!=row['sha256']:
            raise ValueError('Spot quote archive checksum/name mismatch')
        prints,_=read(root,session.date,source,'trades')
        trades[source]=trade_witnesses(prints.local_timestamp.to_numpy(),prints.timestamp.to_numpy(),times)
        del prints
        q,qa=read(root,session.date,source,'book_snapshot_5')
        # A new invalid observation blocks the previous good quote; no filtering
        # that would silently preserve an old healthy witness through a fault.
        cols=[f'{s}[{i}].{k}' for s in ('bids','asks') for i in range(5) for k in ('price','amount')]
        values=q[cols].to_numpy(float)
        good=np.isfinite(values).all(axis=1)&(values>0).all(axis=1)
        good &= q['bids[0].price'].to_numpy()<q['asks[0].price'].to_numpy()
        for j in range(1,5):
            good &= q[f'bids[{j}].price'].to_numpy()<q[f'bids[{j-1}].price'].to_numpy()
            good &= q[f'asks[{j}].price'].to_numpy()>q[f'asks[{j-1}].price'].to_numpy()
        lag=q.local_timestamp.to_numpy()-q.timestamp.to_numpy()
        good &= (lag<=5000000)&(lag>=-500000)
        indices=prior_indices(q.local_timestamp.to_numpy(),times)
        if len(q):
            safe=np.maximum(indices,0)
            quotes[source]=np.where(indices>=0,q.local_timestamp.to_numpy()[safe]/1000.,np.nan)
            valids[source]=(indices>=0)&good[safe]
        else:
            quotes[source]=np.full(len(times),np.nan);valids[source]=np.zeros(len(times),bool)
        audit[source]={**qa,'invalid_quote_rows':int((~good).sum()),'sha256':row['sha256']}
        del q,values
    return HealthTape(times,perp,trades,quotes,valids,audit,other)


class HealthReplay(AccountReplay):
    def __init__(self, session, tape, settings=BASE, policy='original'):
        if policy not in POLICIES: raise ValueError('Unknown health policy')
        super().__init__(session,settings)
        self.tape,self.policy=tape,policy
        self.health_log=[]

    def clock(self,t,frame,reason):
        b=self.broker
        if frame is None and reason=='stale_trade' and b.position:
            perp,sources,other=self.tape.at(t)
            decision=quiet_hold(t,reason,perp,b.book_time if b.book else -10**18,b.s.book_age_ms,sources,other)
            retaining=(self.policy=='quote_witnessed_hold' and decision.retain_protected_position
                       and t<self.boundary_at)
            clean = lambda value: float(value) if math.isfinite(value) else None
            self.health_log.append(dict(time_ms=t,position_id=b.position['position_id'],policy=self.policy,
                action='retain_protected_only' if retaining else 'original_exit',
                decision=decision.reason,perpetual_trade_age_ms=clean(t-perp),
                execution_book_age_ms=t-b.book_time,
                **{f'{s}_trade_age_ms':clean(t-v.trade_received_ms) for s,v in sources.items()},
                **{f'{s}_quote_age_ms':clean(t-v.quote_received_ms) for s,v in sources.items()}))
            if retaining:
                b.on_event(Event(t,t,'archive_clock','health',{}))
                # Hard stops, timeout, funding, exposure and risk still execute
                # above and on every subsequent book. Never undo an exit request.
                if b.pending: b.cancel('feature_quality_lost')
                eq=b.equity()
                if eq is None and b.position: self.unpriced_marks+=1
                if t%10000==0:
                    self.equity.append(dict(time_ms=t,cash=b.cash,equity=eq,quantity=b.position['qty'] if b.position else 0.))
                self.reason_counts[reason]+=1
                self._exposure()
                return
        super().clock(t,frame,reason)

    def run(self):
        r=super().run()
        r.update(health_policy=self.policy,health_model_sha256=identity(),health_events=self.health_log,
            retained_clock_count=sum(x['action']=='retain_protected_only' for x in self.health_log),
            retained_position_count=len({x['position_id'] for x in self.health_log if x['action']=='retain_protected_only'}),
            quote_witness_is_trade_completeness_proof=False)
        r['same_fills_double_commission_net']=r['net_closed']-r['fees_closed']
        r['same_fills_stress_is_executable_account']=False
        return r


def audit_ledger(report):
    """Independent arithmetic from entry/exit cashflows, not reported net fields."""
    completed=[];entries=[];exits=[]
    for e in report['events']:
        if e['type']=='entry_fill': entries.append(e)
        elif e['type']=='exit_fill':
            exits.append(e)
            a=sum(x['quantity'] for x in entries);z=sum(x['quantity'] for x in exits)
            if math.isclose(a,z,abs_tol=1e-10) and a>0:
                trade=report['closed_trades'][len(completed)]
                gross=trade['side']*(sum(x['quantity']*x['price'] for x in exits)-sum(x['quantity']*x['price'] for x in entries))
                fee=sum(x['fee'] for x in entries+exits)
                net=gross-fee-trade['funding']
                assert math.isclose(gross,trade['gross'],abs_tol=1e-7)
                assert math.isclose(fee,trade['fees'],abs_tol=1e-7)
                assert math.isclose(net,trade['net'],abs_tol=1e-7)
                completed.append(net);entries=[];exits=[]
    assert len(completed)==report['completed_trade_count']
    if report['accounting_complete']:
        assert math.isclose(sum(completed),report['cash']-report['capital'],abs_tol=1e-7)
    return dict(checked_trades=len(completed),cashflows_reconciled=True,open_fill_residual=bool(entries))


def run_day(root,out,day,executions=None):
    if day not in DATES: raise ValueError('Uncommitted date')
    chosen=tuple(executions or EXECUTIONS)
    if any(x not in EXECUTIONS for x in chosen): raise ValueError('Unknown execution scenario')
    if out.exists(): raise FileExistsError('Do not overwrite evidence')
    source=validate_manifest(root,(day,));out.mkdir(parents=True)
    session=prepare(root,day);tape=health_tape(root,session)
    write(out/'data_audit.json',dict(event=session.audit,spot_quotes=tape.source_audit,files=source))
    pd.DataFrame([asdict(p) for p in session.proposals.values()]).to_csv(out/'signals.csv',index=False)
    summaries=[]
    for scenario in chosen:
        for policy in POLICIES:
            runner=HealthReplay(session,tape,EXECUTIONS[scenario],policy);r=runner.run()
            r['execution_scenario']=scenario;r['independent_audit']=audit_ledger(r)
            prefix=f'{scenario}_{policy}'
            write(out/f'{prefix}.json',r)
            pd.DataFrame(r['closed_trades']).to_csv(out/f'{prefix}_trades.csv',index=False)
            pd.DataFrame(r['health_events']).to_csv(out/f'{prefix}_health.csv',index=False)
            pd.DataFrame(runner.decisions).to_csv(out/f'{prefix}_decisions.csv',index=False)
            pd.DataFrame(runner.equity).to_csv(out/f'{prefix}_equity.csv',index=False)
            fields=('date','health_policy','execution_scenario','raw_signals','submitted_orders','completed_trade_count',
                'session_net_return_pct','net_closed','gross_closed','fees_closed','accounting_complete',
                'max_marked_drawdown_pct','retained_position_count','retained_clock_count',
                'same_fills_double_commission_net','exit_reasons')
            s={k:r[k] for k in fields};summaries.append(s)
            print(json.dumps(s,ensure_ascii=False),flush=True)
    result=dict(date=day,source_sha256=identity(),sessions=summaries,annual_return_pct=None,cagr_pct=None,
        target_achieved=False,live_ready=False,prior_entry_signals_unchanged=True)
    write(out/'results.json',result)
    pd.DataFrame(summaries).to_csv(out/'comparison.csv',index=False)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--date',choices=DATES,required=True)
    p.add_argument('--executions',nargs='+',choices=tuple(EXECUTIONS))
    a=p.parse_args();run_day(a.data,a.out,a.date,a.executions)
