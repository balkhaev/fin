"""Execution-cadence sensitivity of existing PR127 targets, no new signal policy.

This experiment never reads funding. It neither implements nor substitutes for
funding_crowding/policy.py, and calls the already published spot simulator intact.
"""
from pathlib import Path
import argparse
import hashlib
import json
import math
import numpy as np
import pandas as pd
from research.annual_rotation.data import load as spot_load,SYMBOLS
from research.annual_rotation.model import simulate,Costs,Config
from research.rotation_stability.policy import build
from research.rotation_venue_transfer.data import load as okx_load

POLICIES=('guarded_ensemble20','guarded_ensemble30','guarded_raw12620','ensemble_market_gate')
CADENCES=(1,3,7,14)
PRIMARY=('guarded_ensemble20',1)
CONTROL=('guarded_ensemble20',7)
PINS={'rotation_stability/policy.py':'5a88fe2da2bf8d28cb7f3bead91124465161a0ffdbd1ea6c452e640048d3db0f',
      'annual_rotation/model.py':'e4c9b244e5044dd34062dc83e56c681e33e1e352245529b39d9bc1fa252d95e8'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def write(path,value):
    path.write_text(json.dumps(value,indent=2,allow_nan=False))


def monthly(curve,initial):
    values=np.r_[initial,curve.equity.to_numpy(float)]
    idx=pd.DatetimeIndex(pd.to_datetime(curve.time)-pd.Timedelta(days=1))
    daily=pd.Series(values[1:]/values[:-1]-1,index=idx)
    values=daily.groupby([idx.year,idx.month]).apply(lambda x:(1+x).prod()-1)
    return [dict(year=int(y),month=int(m),return_pct=float(x*100)) for (y,m),x in values.items()]


def audit(fills,report,frames,cost,end):
    cash=cost.initial;units={s:0 for s in SYMBOLS}
    for row in fills.itertuples():
        if row.side not in ('buy','sell'):raise AssertionError('Unknown ledger side')
        sign=1 if row.side=='sell' else -1
        units[row.symbol]-=sign*round(row.quantity/cost.step)
        cash+=sign*row.quantity*row.price-row.fee
        if min(units.values())<0 or cash<-1e-5:raise AssertionError('Borrowed cash or coins')
        if not math.isclose(cash,row.cash_after,abs_tol=1e-5,rel_tol=1e-10):raise AssertionError('Cash flow mismatch')
    remaining={s:n*cost.step for s,n in units.items() if n}
    last=pd.Timestamp(end,tz='UTC')-pd.Timedelta(days=1)
    value=sum(q*frames[s].loc[last,'close']*(1-cost.slip)*(1-cost.fee) for s,q in remaining.items())
    if not math.isclose(cash+value,report['final_equity'],abs_tol=1e-5,rel_tol=1e-10):raise AssertionError('Final coin/cash mismatch')
    if report['accounting_complete'] and remaining:raise AssertionError('Unliquidated complete account')
    return dict(cash_reconciled=True,terminal_quantities=remaining,residual_marked_value=float(value),
        no_writeoff_or_fabricated_fill=True)


def admission(primary,control,stress):
    full=primary['full'];later=primary['later'];old=control['full']
    return dict(complete=full['accounting_complete'] and later['accounting_complete'] and old['accounting_complete'],
        higher_full_CAGR=full['cagr_pct'] is not None and old['cagr_pct'] is not None and full['cagr_pct']>old['cagr_pct'],
        no_worse_full_drawdown=full['max_close_drawdown_pct']>=old['max_close_drawdown_pct'],
        later_positive=later['return_pct'] is not None and later['return_pct']>0,
        all_later_stresses_positive=all(x['accounting_complete'] and x['return_pct']>0 for x in stress),
        more_completed_later_positions=later['closed_asset_positions']>control['later']['closed_asset_positions'],
        nonnegative_full_calendar_years=all(x['return_pct'] is not None and x['return_pct']>=0 for x in full['annual'] if x['full_year']))


def study(source,out):
    source=Path(source);out=Path(out)
    if out.exists():raise FileExistsError('New output directory required')
    here=Path(__file__).parents[1]
    for name,want in PINS.items():
        if hashlib.sha256((here/name).read_bytes()).hexdigest()!=want:raise ValueError('Existing code changed: '+name)
    b,ba=spot_load(source/'prior/rotation-data');o,oa=okx_load(source/'new-source/okx-data')
    if ba['manifest_sha256']!='da9ca6d1e782e8ef6c816390ef3e6ea363eec53a67f58592a8505d754bf5bfe2':raise ValueError('Changed original spot snapshot')
    old=json.loads((source/'prior/static-report/results.json').read_text())
    if digest(old)!='6c6dc96296f0281168e6670ff3231fe47c6bb46bb92e5cc2e4bd31c7e8b6a26a':raise ValueError('Changed old control report')
    bt,_=build(b);ot,_=build(o);out.mkdir(parents=True)
    rows=[];ledgers=[];control_matches=0
    def run(name,every,period,start,end,frames,targets,cost=Costs()):
        nonlocal control_matches
        report,fills,curve=simulate(frames,targets[name],Config('raw',126,3,every),start,end,cost)
        if every==7 and frames is b and period in ('full','later'):
            previous=next(x for x in old['results'] if x['policy']==name and x['period']==period)
            for key,val in report.items():
                if val!=previous[key]:raise AssertionError('Legacy weekly result changed: '+name+'/'+key)
            control_matches+=1
        reconciliation=audit(fills,report,frames,cost,end)
        months=monthly(curve,cost.initial)
        row=dict(report,policy=name,cadence_days=every,period=period,
            venue='binance' if frames is b else 'okx',months=months,fill_audit=reconciliation,
            positive_months=sum(x['return_pct']>1e-10 for x in months),
            zero_months=sum(abs(x['return_pct'])<=1e-10 for x in months),
            negative_months=sum(x['return_pct']<-1e-10 for x in months))
        key=f'{name}_{every}_{period}_{start}'
        fills.to_csv(out/f'{key}_fills.csv',index=False);curve.to_csv(out/f'{key}_equity.csv',index=False)
        ledgers.append(dict(key=key,fills=fills.to_dict('records')));rows.append(row)
        return row
    periods=(('development','2021-01-01','2024-01-01'),('validation','2024-01-01','2025-01-01'),
        ('later','2025-01-01','2026-09-01'),('full','2021-01-01','2026-09-01'))
    for name in POLICIES:
        for every in CADENCES:
            for period,start,end in periods:run(name,every,period,start,end,b,bt)
            for label,cost in [('later_double_costs',Costs(fee=.002,slip=.001)),('later_delay',Costs(extra_delay=1))]:
                run(name,every,label,'2025-01-01','2026-09-01',b,bt,cost)
            run(name,every,'later_okx','2025-01-01','2026-09-01',o,ot)
    for date in pd.date_range('2021-01-01','2025-07-01',freq='QS',tz='UTC'):
        for name,every in (PRIMARY,CONTROL):
            run(name,every,'start365',str(date.date()),str((date+pd.Timedelta(days=365)).date()),b,bt)
    grouped=lambda pair:{x['period']:x for x in rows if (x['policy'],x['cadence_days'])==pair and x['period']!='start365'}
    p,c=grouped(PRIMARY),grouped(CONTROL)
    gates=admission(p,c,[p[k] for k in ('later_double_costs','later_delay','later_okx')])
    high500=[dict(policy=x['policy'],cadence=x['cadence_days'],**y) for x in rows if x['period']=='full'
             for y in x['annual'] if y['full_year'] and y['return_pct'] is not None and y['return_pct']>=500]
    summary=dict(id='guard-execution-audit-20260906',primary=PRIMARY,control=CONTROL,
        rows=rows,source={'binance':ba,'okx':oa},source_pins=PINS,
        weekly_control_reports_exactly_reproduced=control_matches,
        admission_gates=gates,admitted_as_joint_improvement=all(gates.values()),
        observed500calendar=high500,stable500proven=False,live_ready=False,real_orders=0,
        funding_inputs_used=False,blocked_policy_implemented=False,old_signal_and_executor_unchanged=True,
        ledger_sha256=digest(ledgers),
        limitations=['Different cadence is a disclosed new parameter comparison on repeatedly used history.',
          'Sixteen cells are not independent strategies or pristine holdouts.',
          'Risk30 and unscaled market-gate controls carry different target risk; do not call leverage-free risk-free.',
          'Daily-close drawdown and hypothetical executions do not bound intraday or actual exchange risk.',
          'Original residual nulls, actual modeled cash balances and costs preserved.',
          'No funding-based strategy was created; this is a separate existing-code sensitivity audit.',
          'Future profits, tax, custody, infrastructure, counterparty and stablecoin risk not established.'])
    write(out/'results.json',summary)
    fields=('policy','cadence_days','period','start','venue','return_pct','cagr_pct','max_close_drawdown_pct',
            'worst_rolling_365_pct','order_fills','rebalance_days','closed_asset_positions','fees',
            'positive_months','zero_months','negative_months','accounting_complete')
    pd.DataFrame([{k:r[k] for k in fields} for r in rows]).to_csv(out/'comparison.csv',index=False)
    pd.DataFrame([dict(policy=x['policy'],cadence_days=x['cadence_days'],period=x['period'],start=x['start'],**y)
                  for x in rows for y in x['annual']]).to_csv(out/'annual.csv',index=False)
    verified=dict(result_sha256=digest(summary),ledger_sha256=summary['ledger_sha256'],reports=len(rows),
        ledger_count=len(ledgers),source_unchanged=True,live_orders=False)
    write(out/'verification.json',verified)
    print('ALL_BASE_AND_STRESS\n'+pd.DataFrame([{k:r[k] for k in fields} for r in rows if r['period']!='start365']).to_csv(index=False),flush=True)
    print('GATES',json.dumps(gates),flush=True)
    for name,every in (CONTROL,PRIMARY):
        rs=[r for r in rows if r['policy']==name and r['cadence_days']==every and r['period']=='start365']
        complete=[r for r in rs if r['accounting_complete']]
        print('START_SENSITIVITY',json.dumps(dict(policy=name,cadence=every,count=len(rs),complete=len(complete),
            positive=sum(r['return_pct']>0 for r in complete),zero=sum(r['return_pct']==0 for r in complete),
            negative=sum(r['return_pct']<0 for r in complete),worst=min(r['return_pct'] for r in complete))),flush=True)
        print('ANNUAL',name,every,json.dumps(grouped((name,every))['full']['annual']),flush=True)
    print('VERIFY',json.dumps(verified),flush=True)
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();study(a.source,a.out)
