"""Offline finite portfolio comparison. Never invokes order APIs or alters old accounting.

All financial results come from the original PR134 reference. Additional audits
measure its existing fills/curves; they never repair or fill a missing price.
"""
from pathlib import Path
import argparse
import hashlib
import json
import math
import time
import numpy as np
import pandas as pd
from research.relative_futures.data import load, SYMBOLS
from research.relative_futures.account import simulate, Costs
from research.relative_futures.study import digest, save, qualify, independent_trade_replay
from research.relative_futures_checks.candidates import episode_statistics, STARTS
from .policy import build, PRIMARY, MODELS, engine_gross

CONTROL = 'old_pair_1x'
BASE = '157fe890756519f3faba4903a88950d24a890a8cecfa0fad4ae4e849d0fd9645'


def leverage_audit(frames, fills, curve, costs):
    """Observe entry and held leverage/margin; no alternate trading or valuation rule."""
    if not np.isfinite(curve.equity.to_numpy(float)).all():
        return {'verified': False, 'reason': 'Unpriced equity path; not repaired'}
    prices = pd.concat([frames[s][['mark_high','mark_low','mark_close']].add_prefix(s+'_')
                        for s in SYMBOLS], axis=1)
    when = pd.DatetimeIndex(pd.to_datetime(curve.time))-pd.Timedelta(hours=1)
    prices = prices.reindex(when)
    if not np.isfinite(prices.to_numpy()).all():
        return {'verified': False, 'reason': 'Missing mark source; not repaired'}
    close=np.column_stack([prices[s+'_mark_close'] for s in SYMBOLS])
    high=np.column_stack([prices[s+'_mark_high'] for s in SYMBOLS])
    low=np.column_stack([prices[s+'_mark_low'] for s in SYMBOLS])
    quantities=curve[['btc_quantity','eth_quantity']].to_numpy(float)
    equity=curve.equity.to_numpy(float)
    held=np.abs(quantities).sum(axis=1)>0
    gross=np.sum(np.abs(quantities)*close,axis=1)
    observed=np.divide(gross,equity,out=np.full(len(equity),np.inf),where=equity>0)
    worst_prices=np.where(quantities>=0,low,high)
    worst_equity=equity+np.sum(quantities*(worst_prices-close),axis=1)-np.sum(np.abs(quantities)*worst_prices,axis=1)*(costs.fee+costs.slip)
    maintenance=.1*np.sum(np.abs(quantities)*high,axis=1)
    ratio=np.divide(worst_equity,maintenance,out=np.full(len(equity),np.inf),where=maintenance>0)
    entries=[]
    if len(fills):
        for stamp, block in fills[fills.reason=='entry'].groupby('time',sort=False):
            notional=float((block.quantity_delta.abs()*block.price).sum())
            # Cost-adjusted collateral after the final leg, not original initial deposit.
            funds=float(block.balance_at_open.iloc[-1])
            entries.append(notional/funds if funds>0 else float('inf'))
    return dict(verified=bool(np.isfinite(observed).all()),
        max_entry_gross_after_fees=max(entries,default=0.),
        max_mark_close_gross=float(observed.max()),
        held_hours_above_15=int(np.sum(held&(observed>1.5))),
        held_hours_above_20=int(np.sum(held&(observed>2.))),
        held_hours_above_225=int(np.sum(held&(observed>2.25))),
        minimum_adverse_maintenance_cover=float(ratio[held].min()) if held.any() else None,
        adverse_margin_breach_hours=int(np.sum(held&(ratio<=1))),
        known_full_dataset_only=True, actual_venue_margin_tiers=False,
        realized_volatility_guaranteed=False, liquidations_ruled_out=False)


def annual_checks(curve, initial):
    daily=pd.Series(curve.equity.to_numpy(float),index=pd.to_datetime(curve.time)-pd.Timedelta(nanoseconds=1)).resample('D').last()
    if not np.isfinite(daily).all(): return {'verified':False}
    v=np.r_[initial,daily.to_numpy()]
    ret=v[1:]/v[:-1]-1
    trailing = (v[365:]/v[:-365]-1)*100 if len(v)>365 else np.array([])
    return {'verified':True,'observed_annual_volatility_pct':float(np.std(ret,ddof=1)*np.sqrt(365.25)*100),
            'worst_rolling365_pct':float(trailing.min()) if len(trailing) else None}


def run_study(root, out, stage='all'):
    root=Path(root); out=Path(out)
    if out.exists(): raise FileExistsError('Fresh evidence directory required')
    old=json.loads((root/'report/results.json').read_text())
    if digest(old)!=BASE: raise ValueError('Original result identity changed')
    for name,want in old['source_sha256'].items():
        source=Path(__file__).parents[1]/'relative_futures'/name
        if hashlib.sha256(source.read_bytes()).hexdigest()!=want:
            raise ValueError('Old reference code changed: '+name)
    frames, audit=load(root/'supplemented/reconciled')
    if audit != old['source']: raise ValueError('Market source identity changed')
    out.mkdir(parents=True); started=time.monotonic()
    targets, trace=build(frames)
    trace.to_csv(out/'allocation_diagnostics.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    rows=[]; ledgers=[]; exact=0
    def one(name, period, start, end='2026-09-01', fee=.0005, slip=.0001, delay=0, initial=10000.):
        nonlocal exact
        cost=Costs(gross=engine_gross(name),fee=fee,slip=slip,delay=delay,initial=initial)
        r,f,pay,e,c=simulate(frames,targets[name],start,end,cost)
        if name==CONTROL and period in ('full','later'):
            previous=next(x for x in old['rows'] if x['model']=='pair_momentum720' and x['period']==period)
            for k,v in r.items():
                if v!=previous[k]: raise AssertionError('Legacy control changed: '+k)
            exact+=1
        q=qualify(r,c)
        lev=leverage_audit(frames,f,c,cost)
        if not lev['verified']: q['qualified_historical_scenario']=False; q['issues'].append('leverage_path_audit_failed')
        stats=episode_statistics(e,c,r)
        row=dict(r,model=name,period=period,qualification=q,leverage_audit=lev,
            additional_risk=annual_checks(c,initial),episode_statistics=stats,
            independent_cash=independent_trade_replay(f,pay,r['final_balance'],initial,r['terminal_quantities']))
        key=f'{name}_{period}_{start}'
        for label,frame in [('fills',f),('funding',pay),('episodes',e)]: frame.to_csv(out/f'{key}_{label}.csv',index=False)
        c.to_csv(out/f'{key}_equity.csv.gz',index=False,compression={'method':'gzip','mtime':0})
        rows.append(row);ledgers.append(dict(key=key,fills=f.to_dict('records'),funding=pay.to_dict('records')))
        fields=('model','period','start','return_pct','cagr_pct','max_mark_close_drawdown_pct','completed_episodes')
        print(json.dumps({k:row[k] for k in fields}),flush=True)
        return row
    # First reproduce the exact old baseline, never infer from a similar percentage.
    one(CONTROL,'full','2021-01-01');one(CONTROL,'later','2025-01-01')
    if exact!=2:raise AssertionError('Legacy controls not reproduced')
    for name in MODELS:
        if name!=CONTROL:
            one(name,'full','2021-01-01');one(name,'later','2025-01-01')
        one(name,'validation','2024-01-01','2025-01-01')
        one(name,'later_double_costs','2025-01-01',fee=.001,slip=.0002)
        one(name,'later_delay2','2025-01-01',delay=2)
    for name in (PRIMARY,CONTROL):
        one(name,'full_double_costs','2021-01-01',fee=.001,slip=.0002)
        one(name,'later_capital1000','2025-01-01',initial=1000.)
        for a,b in STARTS:one(name,'origin365',a,b)
    assert len(rows)==63
    get=lambda name,period: next(r for r in rows if r['model']==name and r['period']==period)
    origins={}
    for name in (PRIMARY,CONTROL):
        ori=[r for r in rows if r['model']==name and r['period']=='origin365']
        qualified=[r for r in ori if r['qualification']['qualified_historical_scenario']]
        origins[name]=dict(total=len(ori),qualified=len(qualified),positive=sum(r['return_pct']>0 for r in qualified),
            negative=sum(r['return_pct']<0 for r in qualified),worst=min(r['return_pct'] for r in qualified) if qualified else None)
    full,later=get(PRIMARY,'full'),get(PRIMARY,'later')
    gates=dict(qualified_primary=all(get(PRIMARY,s)['qualification']['qualified_historical_scenario'] for s in ('full','later','later_double_costs','later_delay2')),
        full_cagr_improved=full['cagr_pct'] is not None and full['cagr_pct']>get(CONTROL,'full')['cagr_pct'],
        later_return_improved=later['return_pct'] is not None and later['return_pct']>get(CONTROL,'later')['return_pct'],
        full_dd_within30=full['max_mark_close_drawdown_pct']>=-30,
        later_dd_within15=later['max_mark_close_drawdown_pct']>=-15,
        stress_positive=all(get(PRIMARY,s)['return_pct'] is not None and get(PRIMARY,s)['return_pct']>0 for s in ('later_double_costs','later_delay2')),
        no_more_negative_origins=origins[PRIMARY]['qualified']==7 and origins[PRIMARY]['negative']<=origins[CONTROL]['negative'])
    snapshots={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}
    result=dict(id='opportunity-budget-20260906',primary=PRIMARY,control=CONTROL,rows=rows,origin_sensitivity=origins,
        gates=gates,primary_joint_improvement=all(gates.values()),source=audit,source_sha256=snapshots,
        reference_sha256=old['source_sha256'],ledger_sha256=digest(ledgers),exact_control_reports=exact,
        original_reference_bug_unpatched=True,local_execution=True,unseen_market=False,
        no_real_orders=True,live_ready=False,stable500proven=False,
        limitations=['Repeatedly studied BTC/ETH history; new protocol does not create a pristine holdout.',
            'Known inactive-mark NaN defect remains in original reference; only complete data used for these scenarios.',
            'Sizing applies at new sign-state entry, not continuous precise leverage control. Reductions request flat/re-entry.',
            'Target leverage ceiling and forecast volatility are not guarantees of realized risk or intrabar exposure.',
            'Reference assumes both legs available for entry; true orderbook, liquidation tiers and settlement marks are not reconstructed.',
            'Funding uses recorded rates and approximate hour-open marks; no riskless-carry or funding-prediction claim.',
            'Taxes, custody, exchange/USDT failure and operations costs are not comprehensively modeled.'])
    save(out/'results.json',result)
    fields=('model','period','start','return_pct','cagr_pct','max_mark_close_drawdown_pct','completed_episodes','order_fills','fees','funding_cashflow')
    pd.DataFrame([dict(**{k:r[k] for k in fields},max_gross=r['leverage_audit'].get('max_mark_close_gross'),qualified=r['qualification']['qualified_historical_scenario']) for r in rows]).to_csv(out/'comparison.csv',index=False)
    pd.DataFrame([dict(model=r['model'],period=r['period'],start=r['start'],**y) for r in rows for y in r['annual']]).to_csv(out/'annual.csv',index=False)
    save(out/'verification.json',dict(result_sha256=digest(result),ledger_sha256=result['ledger_sha256'],reports=len(rows),exact_controls=exact))
    print('GATES',json.dumps(gates),flush=True)
    print('RUNTIME_SECONDS',time.monotonic()-started,flush=True)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();run_study(args.source,args.out)
