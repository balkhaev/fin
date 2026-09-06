"""Explicit post-result allocation study; all account calculations use the original archive.
Not a beta policy, live trader or valuation repair. Same-sign weights are not
continuously resized by the reference, so actual holdings remain independently audited.
"""
from pathlib import Path
import argparse
import hashlib
import importlib
import json
import numpy as np
import pandas as pd
from research.holding_horizon.reference import load_reference, digest
from research.holding_horizon.study import STARTS, save
from research.channel_scale.study import channel_targets

CONTROL = 'relative125'; PRIMARY = 'mix25'
MODELS = (CONTROL, 'daily25', 'daily50', PRIMARY, 'mix50')
CHANNEL_SHA = 'b4235bdfda95b01bd8ccaad1914adc1e5c7095d3535fb137de72f5ac08eef751'


def compose(relative_one, daily_one):
    a, b = np.asarray(relative_one, float), np.asarray(daily_one, float)
    if a.shape != b.shape or a.ndim != 2 or a.shape[1] != 2:
        raise ValueError('Two aligned signed instrument requests required')
    for w in (a, b):
        if not np.isfinite(w).all() or (np.abs(w).sum(axis=1) > 1 + 1e-12).any():
            raise ValueError('Inputs must have finite gross<=1')
    # The original reference multiplies every target by Costs.gross2.
    return {CONTROL: .625*a, 'daily25': .125*b, 'daily50': .25*b,
            PRIMARY: (.75*a+.25*b)/2, 'mix50': (.5*a+.5*b)/2}


def study(source, channel_evidence, out, *, acknowledged=False):
    source, channel_evidence, out = Path(source), Path(channel_evidence), Path(out)
    if out.exists():
        raise FileExistsError('Fresh output required')
    old_channel = json.loads((channel_evidence/'report/results.json').read_text())
    if digest(old_channel) != CHANNEL_SHA:
        raise ValueError('Original channel result identity changed')
    if hashlib.sha256((Path(__file__).parents[1]/'channel_scale/study.py').read_bytes()).hexdigest() != old_channel['script_sha256']:
        raise ValueError('Original channel aggregation changed')
    modules, frames, audit, old, pins = load_reference(source, acknowledged=acknowledged)
    if audit != old_channel['source']:
        raise ValueError('Different input data')
    account=modules['relative_futures.account']; tools=modules['relative_futures.study']
    risk=modules['opportunity_budget.study']; stats=modules['relative_futures_checks.candidates']
    breakout=importlib.import_module('research.relative_futures.signals').breakout
    close=pd.DataFrame({s:frames[s].close for s in ('BTCUSDT','ETHUSDT')})
    old_states=modules['opportunity_runner.study'].states
    relative=old_states(close,max_hours=720,trailing=False,risk_size=False)/.75
    channels,_=channel_targets(frames,breakout)
    target=compose(relative,channels['both_h24'])
    cut=close.index.searchsorted(pd.Timestamp('2025-03-17 07:00',tz='UTC'))
    pc,_=channel_targets({s:d.iloc[:cut] for s,d in frames.items()},breakout)
    pr=old_states(close.iloc[:cut],max_hours=720,trailing=False,risk_size=False)/.75
    for name,value in compose(pr,pc['both_h24']).items():
        np.testing.assert_allclose(target[name][:cut],value,atol=1e-12,rtol=0)
    out.mkdir(parents=True);rows=[];ledgers=[];exact=0
    def run(name,period,start,end='2026-09-01',fee=.0005,slip=.0001,delay=0,initial=10000.):
        nonlocal exact
        cost=account.Costs(gross=2,fee=fee,slip=slip,delay=delay,initial=initial)
        r,f,pay,e,c=account.simulate(frames,target[name],start,end,cost)
        if name==CONTROL and period in ('full','later','validation'):
            previous=next(x for x in old['rows'] if x['model']=='runner720_125x' and x['period']==period)
            for k,v in r.items():
                if v!=previous[k]:raise AssertionError('Old control differs: '+k)
            exact+=1
        q=tools.qualify(r,c);lev=risk.leverage_audit(frames,f,c,cost)
        if not lev['verified']:q['qualified_historical_scenario']=False;q['issues'].append('leverage_audit_failed')
        row=dict(r,model=name,period=period,qualification=q,leverage_audit=lev,
            additional_risk=risk.annual_checks(c,initial),episode_statistics=stats.episode_statistics(e,c,r),
            independent_cash=tools.independent_trade_replay(f,pay,r['final_balance'],initial,r['terminal_quantities']))
        key=f'{name}_{period}_{start}'
        for suffix,d in (('fills',f),('funding',pay),('episodes',e)):d.to_csv(out/f'{key}_{suffix}.csv',index=False)
        c.to_csv(out/f'{key}_equity.csv.gz',index=False,compression={'method':'gzip','mtime':0})
        rows.append(row);ledgers.append(dict(key=key,fills=f.to_dict('records'),funding=pay.to_dict('records')))
        print('CASE',json.dumps({k:row[k] for k in ('model','period','start','return_pct','cagr_pct','max_mark_close_drawdown_pct','completed_episodes')}),flush=True)
    for p,a,b in (('full','2021-01-01','2026-09-01'),('later','2025-01-01','2026-09-01'),('validation','2024-01-01','2025-01-01')):run(CONTROL,p,a,b)
    for name in MODELS:
        if name!=CONTROL:
            run(name,'full','2021-01-01');run(name,'later','2025-01-01');run(name,'validation','2024-01-01','2025-01-01')
        run(name,'later_double_costs','2025-01-01',fee=.001,slip=.0002);run(name,'later_delay2','2025-01-01',delay=2)
    for name in (PRIMARY,CONTROL):
        run(name,'full_double_costs','2021-01-01',fee=.001,slip=.0002)
        run(name,'later_capital1000','2025-01-01',initial=1000.)
        for a,b in STARTS:run(name,'origin365',a,b)
    assert len(rows)==43 and exact==3
    get=lambda name,p:next(r for r in rows if r['model']==name and r['period']==p)
    origins={}
    for name in (PRIMARY,CONTROL):
        valid=[r for r in rows if r['model']==name and r['period']=='origin365' and r['qualification']['qualified_historical_scenario']]
        origins[name]=dict(total=7,qualified=len(valid),positive=sum(r['return_pct']>0 for r in valid),negative=sum(r['return_pct']<0 for r in valid),worst_return_pct=min((r['return_pct'] for r in valid),default=None))
    p,l=get(PRIMARY,'full'),get(PRIMARY,'later')
    gates=dict(qualified_positive_primary=all(get(PRIMARY,k)['qualification']['qualified_historical_scenario'] and get(PRIMARY,k)['return_pct']>0 for k in ('full','later','later_double_costs')),
        full_drawdown_within30=p['max_mark_close_drawdown_pct']>=-30,late_drawdown_within15=l['max_mark_close_drawdown_pct']>=-15,
        full_CAGR_not_below_control=p['cagr_pct'] is not None and p['cagr_pct']>=get(CONTROL,'full')['cagr_pct'],
        late_return_not_below_control=l['return_pct'] is not None and l['return_pct']>=get(CONTROL,'later')['return_pct'],
        no_more_losing_starts=origins[PRIMARY]['qualified']==7 and origins[PRIMARY]['negative']<=origins[CONTROL]['negative'])
    result=dict(id='channel-budget-20260906',primary=PRIMARY,control=CONTROL,source=audit,rows=rows,gates=gates,
        admitted=all(gates.values()),origin_sensitivity=origins,original_channel_sha256=CHANNEL_SHA,
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),ledger_sha256=digest(ledgers),exact_original_reports=exact,
        local_execution=False,post_result_allocation_experiment=True,source_and_account_unchanged=True,
        reference_bug_unpatched_outside_main=True,beta_model_used=False,live_ready=False,stable500proven=False,real_orders=0,
        limitations=['Allocations fixed after seeing daily channel results, not a fresh time holdout.',
            'One net signed account, not sum of standalone profits; actual quantities persist while signs stay unchanged.',
            'Lower requested gross is not a hard continuous exposure or loss cap.',
            'Known archived account defect remains and complete snapshot is an explicit requirement.',
            'Historical mark/funding costs and margin are scenario approximations; real orderbook and operational risks not verified.'])
    save(out/'results.json',result)
    fields=('model','period','start','return_pct','cagr_pct','max_mark_close_drawdown_pct','completed_episodes','order_fills','fees','funding_cashflow')
    pd.DataFrame([dict(**{k:r[k] for k in fields},max_gross=r['leverage_audit'].get('max_mark_close_gross'),qualified=r['qualification']['qualified_historical_scenario']) for r in rows]).to_csv(out/'comparison.csv',index=False)
    pd.DataFrame([dict(model=r['model'],period=r['period'],start=r['start'],**y) for r in rows for y in r['annual']]).to_csv(out/'annual.csv',index=False)
    save(out/'verification.json',dict(result_sha256=digest(result),ledger_sha256=result['ledger_sha256'],reports=43,qualified=sum(r['qualification']['qualified_historical_scenario'] for r in rows),exact_original_reports=exact))
    print('GATES',json.dumps(gates),flush=True);print('ORIGINS',json.dumps(origins),flush=True)
    for name in MODELS:print('YEARS',name,json.dumps(get(name,'full')['annual']),flush=True)
    print('VERIFY',(out/'verification.json').read_text(),flush=True)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--channel-evidence',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--allow-archived-reference',action='store_true')
    a=p.parse_args();study(a.source,a.channel_evidence,a.out,acknowledged=a.allow_archived_reference)
