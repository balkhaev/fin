"""Explicit post-result lifecycle research; the original futures reference is unchanged."""
from pathlib import Path
import argparse
import hashlib
import json
import numpy as np
import pandas as pd
from research.relative_futures.data import load
from research.relative_futures.account import simulate, Costs
from research.relative_futures.study import digest, save, qualify, independent_trade_replay
from research.relative_futures_checks.candidates import STARTS, episode_statistics
from research.opportunity_budget.study import BASE, leverage_audit, annual_checks

MODELS=('runner720_15','runner720_risk30','runner720_trail15','runner168_trail15')


def states(close, max_hours=168, trailing=False, risk_size=False):
    lp=np.log(close); ratio=lp.iloc[:,1]-lp.iloc[:,0]; rets=ratio.diff()
    z=((ratio-ratio.shift(720))/(rets.rolling(168,min_periods=168).std()*np.sqrt(720))).to_numpy()
    fast=rets.ewm(span=168,adjust=False,min_periods=168).std().to_numpy()
    slow=rets.ewm(span=720,adjust=False,min_periods=720).std().to_numpy()
    sigma=np.maximum(np.maximum(fast,slow), .001)
    valid=close.notna().all(axis=1).rolling(721,min_periods=721).sum().eq(721).to_numpy() & np.isfinite(z)
    r=ratio.to_numpy(); out=np.zeros((len(close),2)); side=0; entered=-1
    entry_size=0.; best=0.; distance=0.
    for i in range(len(out)):
        if not valid[i]:side=0;continue
        if side:
            best=max(best, side*r[i])
            hit_trail=trailing and best-side*r[i]>=distance
            if abs(z[i])<.25 or side*z[i]<0 or i-entered>=max_hours or hit_trail:
                side=0;continue
        elif abs(z[i])>=1.5:
            side=int(np.sign(z[i]));entered=i;best=side*r[i]
            distance=max(.02,4*np.sqrt(24)*sigma[i])
            entry_size=min(1.5,.30/(.5*sigma[i]*np.sqrt(24*365.25))) if risk_size else 1.5
        # Existing simulator gets Costs.gross2. Size stays fixed for the episode.
        out[i]=np.array([-.5,.5])*side*entry_size/2.
    return out


def build(frames):
    close=pd.DataFrame({s:frames[s].close for s in ('BTCUSDT','ETHUSDT')})
    return {MODELS[0]:states(close,720),MODELS[1]:states(close,720,risk_size=True),
            MODELS[2]:states(close,720,trailing=True),MODELS[3]:states(close,168,trailing=True)}


def study(root,out):
    root=Path(root);out=Path(out)
    if out.exists():raise FileExistsError('Fresh output directory required')
    old=json.loads((root/'report/results.json').read_text())
    if digest(old)!=BASE:raise ValueError('Base evidence changed')
    for name,want in old['source_sha256'].items():
        if hashlib.sha256((Path(__file__).parents[1]/'relative_futures'/name).read_bytes()).hexdigest()!=want:
            raise ValueError('Original account/source changed')
    frames,audit=load(root/'supplemented/reconciled')
    if audit!=old['source']:raise ValueError('Data changed')
    target=build(frames);out.mkdir(parents=True);rows=[];ledgers=[]
    def one(name,period,start,end='2026-09-01',fee=.0005,slip=.0001,delay=0,initial=10000.):
        cost=Costs(gross=2,fee=fee,slip=slip,delay=delay,initial=initial)
        r,f,pay,e,c=simulate(frames,target[name],start,end,cost)
        q=qualify(r,c);lev=leverage_audit(frames,f,c,cost)
        if not lev['verified']:q['qualified_historical_scenario']=False;q['issues'].append('leverage_audit_failed')
        row=dict(r,model=name,period=period,qualification=q,leverage_audit=lev,additional_risk=annual_checks(c,initial),
            episode_statistics=episode_statistics(e,c,r),independent_cash=independent_trade_replay(f,pay,r['final_balance'],initial,r['terminal_quantities']))
        key=f'{name}_{period}_{start}'
        for label,frame in [('fills',f),('funding',pay),('episodes',e)]:frame.to_csv(out/f'{key}_{label}.csv',index=False)
        c.to_csv(out/f'{key}_equity.csv.gz',index=False,compression={'method':'gzip','mtime':0})
        rows.append(row);ledgers.append(dict(key=key,fills=f.to_dict('records'),funding=pay.to_dict('records')))
        keys=('model','period','start','return_pct','cagr_pct','max_mark_close_drawdown_pct','completed_episodes')
        print(json.dumps({k:row[k] for k in keys}),flush=True)
    for name in MODELS:
        one(name,'full','2021-01-01');one(name,'later','2025-01-01');one(name,'validation','2024-01-01','2025-01-01')
        one(name,'full_double_costs','2021-01-01',fee=.001,slip=.0002)
        one(name,'later_double_costs','2025-01-01',fee=.001,slip=.0002)
        one(name,'later_delay2','2025-01-01',delay=2)
        one(name,'later_capital1000','2025-01-01',initial=1000.)
        for a,b in STARTS:one(name,'origin365',a,b)
    assert len(rows)==56
    origins={}
    for name in MODELS:
        selected=[r for r in rows if r['model']==name and r['period']=='origin365']
        qualified=[r for r in selected if r['qualification']['qualified_historical_scenario']]
        origins[name]=dict(total=7,qualified=len(qualified),positive=sum(r['return_pct']>0 for r in qualified),
            negative=sum(r['return_pct']<0 for r in qualified),worst=min(r['return_pct'] for r in qualified) if qualified else None)
    result=dict(id='relative-runner-followup-20260906',source=audit,rows=rows,origin_sensitivity=origins,
        ledger_sha256=digest(ledgers),script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        selected_after_initial_study=True,original_multi_primary_still_failed=True,unseen_market=False,
        reference_account_unpatched=True,local_execution=True,live_ready=False,stable500proven=False,no_real_orders=True)
    save(out/'results.json',result)
    keys=('model','period','start','return_pct','cagr_pct','max_mark_close_drawdown_pct','completed_episodes','order_fills','fees','funding_cashflow')
    pd.DataFrame([dict(**{k:r[k] for k in keys},max_gross=r['leverage_audit'].get('max_mark_close_gross'),qualified=r['qualification']['qualified_historical_scenario']) for r in rows]).to_csv(out/'comparison.csv',index=False)
    pd.DataFrame([dict(model=r['model'],period=r['period'],start=r['start'],**y) for r in rows for y in r['annual']]).to_csv(out/'annual.csv',index=False)
    save(out/'verification.json',dict(result_sha256=digest(result),ledger_sha256=result['ledger_sha256'],reports=len(rows)))
    print('ORIGINS',json.dumps(origins),flush=True)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    a=parser.parse_args();study(a.source,a.out)
