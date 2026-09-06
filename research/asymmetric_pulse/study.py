"""Finite exploratory study. Same cash/coin executor, new signal hypotheses.

No forecasts or permission to trade. Every tested result, including incomplete
and losing accounts, is retained. Existing source and fill code are not modified.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
from research.annual_rotation.data import load as load_binance,SYMBOLS
from research.rotation_venue_transfer.data import load as load_okx
from research.annual_rotation.model import Costs,simulate
from .policy import build,NAMES,CONTROLS,PRIMARY,schedule


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def write(path,value):path.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False))


def admitted(r):
    return bool(r['accounting_complete'] and r['cagr_pct'] is not None and r['cagr_pct']>0
        and r['max_close_drawdown_pct']>=-35 and r['worst_rolling_365_pct'] is not None
        and r['worst_rolling_365_pct']>=-15 and r['rebalance_days']>=24)


def select(training):
    candidates=[r for r in training if admitted(r)]
    candidates.sort(key=lambda r:(-r['cagr_pct']/max(10.,abs(r['max_close_drawdown_pct'])),r['policy']))
    return candidates[0]['policy'] if candidates else None


def inspect_ledger(fills,report,frames,cost,end):
    cash=cost.initial;units={s:0 for s in SYMBOLS}
    for row in fills.itertuples():
        if row.side not in ('buy','sell'):raise AssertionError('Unknown side')
        sign=1 if row.side=='sell' else -1
        units[row.symbol]-=sign*round(row.quantity/cost.step)
        cash+=sign*row.quantity*row.price-row.fee
        if cash<-1e-5 or min(units.values())<0:raise AssertionError('Cash/coin borrowing')
        if not math.isclose(cash,row.cash_after,abs_tol=1e-5,rel_tol=1e-10):raise AssertionError('Cashflow discrepancy')
    remaining={s:n*cost.step for s,n in units.items() if n}
    day=pd.Timestamp(end,tz='UTC')-pd.Timedelta(days=1)
    value=sum(q*frames[s].loc[day,'close']*(1-cost.slip)*(1-cost.fee) for s,q in remaining.items())
    if not math.isclose(cash+value,report['final_equity'],abs_tol=1e-5,rel_tol=1e-10):raise AssertionError('Final equity discrepancy')
    return dict(cashflow_reconciled=True,terminal_coins=remaining,terminal_marked_value=float(value),
        no_residual_writeoff=True,entire_cash_account_not_sum_of_strategy_curves=True)


def months(curve,capital):
    dates=pd.to_datetime(curve.time)-pd.Timedelta(days=1)
    path=np.r_[capital,curve.equity.to_numpy(float)]
    daily=pd.Series(path[1:]/path[:-1]-1,index=pd.DatetimeIndex(dates))
    monthly=daily.groupby([daily.index.year,daily.index.month]).apply(lambda x:(1+x).prod()-1)
    return [dict(year=int(y),month=int(m),return_pct=float(v*100)) for (y,m),v in monthly.items()]


def study(source,out):
    source=Path(source);out=Path(out)
    if out.exists():raise FileExistsError('Fresh evidence directory required')
    here=Path(__file__).parents[1]
    for name in ('data.py','model.py'):
        if (here/'annual_rotation'/name).read_bytes()!=(source/'prior/delivery/research/annual_rotation'/name).read_bytes():
            raise ValueError('Original simulator/loader changed')
    old=json.loads((source/'prior/static-report/results.json').read_text())
    if digest(old)!='6c6dc96296f0281168e6670ff3231fe47c6bb46bb92e5cc2e4bd31c7e8b6a26a':raise ValueError('Unexpected prior result')
    b,ba=load_binance(source/'prior/rotation-data');o,oa=load_okx(source/'new-source/okx-data')
    if ba['manifest_sha256']!='da9ca6d1e782e8ef6c816390ef3e6ea363eec53a67f58592a8505d754bf5bfe2':raise ValueError('Unexpected Binance data')
    out.mkdir(parents=True)
    bt,diagnostics,counts=build(b)
    diagnostics.to_csv(out/'binance_signal_diagnostics.csv',index=False)
    rows=[];ledger_sets=[];training=[]
    def run(name,period,start,end,frames,targets,cost=Costs(),exclude=None):
        r,f,c=simulate(frames,targets[name],schedule(name),start,end,cost,hold_only=name=='btc_hold')
        if name in ('guarded_control','raw126_control') and frames is b and period in ('full','later'):
            oldname='guarded_ensemble20' if name=='guarded_control' else 'legacy_raw126'
            control=next(x for x in old['results'] if x['policy']==oldname and x['period']==period)
            for key,value in r.items():
                if value!=control[key]:raise AssertionError('Old control changed: '+key)
        audit=inspect_ledger(f,r,frames,cost,end)
        monthly=months(c,cost.initial)
        key=name+'_'+period+('_without_'+exclude if exclude else '')
        row=dict(r,policy=name,period=period,excluded=exclude,monthly=monthly,fill_audit=audit,
            positive_months=sum(x['return_pct']>1e-10 for x in monthly),
            zero_months=sum(abs(x['return_pct'])<=1e-10 for x in monthly),
            negative_months=sum(x['return_pct']<-1e-10 for x in monthly),
            source_venue='binance' if frames is b else 'okx',history_reused=True)
        f.to_csv(out/f'{key}_fills.csv',index=False);c.to_csv(out/f'{key}_equity.csv',index=False)
        ledger_sets.append(dict(key=key,fills=f.to_dict('records')))
        return row
    for name in NAMES:
        r=run(name,'development','2021-01-01','2024-01-01',b,bt)
        r['admitted']=admitted(r);training.append(r)
    challenger=select(training)
    lock=dict(primary=PRIMARY,challenger=challenger,qualified_challengers=sum(admitted(r) for r in training),
        training_end_exclusive='2024-01-01',later_used_for_selection=False,
        all_periods_already_seen_in_previous_research=True)
    write(out/'selection.json',lock);write(out/'development.json',training)
    print('SELECTION_LOCK',json.dumps(lock),flush=True)
    for name in NAMES+CONTROLS:
        for period,start,end in [('validation','2024-01-01','2025-01-01'),('later','2025-01-01','2026-09-01'),('full','2021-01-01','2026-09-01')]:
            r=run(name,period,start,end,b,bt);rows.append(r)
            print('RESULT',json.dumps({k:r[k] for k in ('policy','period','return_pct','diagnostic_return_pct','cagr_pct','max_close_drawdown_pct','worst_rolling_365_pct','order_fills','rebalance_days','accounting_complete')}),flush=True)
    focus=list(dict.fromkeys([PRIMARY]+([challenger] if challenger else [])))
    ot,odiagnostics,ocounts=build(o);odiagnostics.to_csv(out/'okx_signal_diagnostics.csv',index=False)
    robustness=[]
    for name in focus:
        for scenario,cost in [('double_costs',Costs(fee=.002,slip=.001)),('extra_day_delay',Costs(extra_delay=1))]:
            for label,start in [('full','2021-01-01'),('later','2025-01-01')]:
                robustness.append(run(name,label+'_'+scenario,start,'2026-09-01',b,bt,cost))
        for label,start,end in [('okx_validation','2024-01-01','2025-01-01'),('okx_later','2025-01-01','2026-09-01'),('okx_full_common','2024-01-01','2026-09-01')]:
            robustness.append(run(name,label,start,end,o,ot))
    exclusions=[]
    for asset in SYMBOLS:
        t,_,_=build(b,exclude=asset)
        exclusions.append(run(PRIMARY,'full','2021-01-01','2026-09-01',b,t,exclude=asset))
    years500=[dict(policy=r['policy'],period=r['period'],**a,ex_post_table=True)
        for r in rows if r['period']=='full' for a in r['annual']
        if a['full_year'] and a['return_pct'] is not None and a['return_pct']>=500]
    result=dict(id='asymmetric-pulse-20260906',selection=lock,development=training,rows=rows,
        robustness=robustness,exclusions=exclusions,data={'binance':ba,'okx':oa},
        signal_counts={'binance_full_loaded_history':counts,'okx_full_loaded_history':ocounts},
        observed_calendar_years_above500=years500,legacy_controls_exactly_reproduced=True,
        source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')},
        fill_ledger_sha256=digest(ledger_sets),stable_profit_proven=False,annual500proven=False,live_orders=False,
        limitations=['NEW asymmetric hypotheses on reused history, not pristineOOS.',
            'The same nine known surviving assets are not a point-in-time complete market.',
            'Downside-risk budgeting is an assumption; it is NOT a stop-loss or realized volatility bound.',
            'Riskier full spot allocations do not preserve prior10%drawdown/.25%per-trade limits.',
            'Daily exits are delayed scheduled signals, never inferred intraday stop fills.',
            'Original residual/price flags retained, no zero-recovery convention or invented terminal fill.',
            'Higher order count includes rebalances, not independent round-trip profits.',
            'No real order book execution, liquidation certificate, taxes, custody or infrastructure costs.',
            'Independent runner repetition proves computational repeatability, not market independence.'])
    write(out/'results.json',result)
    fields=('policy','period','source_venue','return_pct','diagnostic_return_pct','cagr_pct','max_close_drawdown_pct','worst_rolling_365_pct','order_fills','rebalance_days','closed_asset_positions','fees','positive_months','zero_months','negative_months','accounting_complete','open_assets')
    pd.DataFrame([{k:r[k] for k in fields} for r in training+rows+robustness]).to_csv(out/'comparison.csv',index=False)
    pd.DataFrame([dict(policy=r['policy'],period=r['period'],**a) for r in rows+robustness for a in r['annual']]).to_csv(out/'annual.csv',index=False)
    pd.DataFrame([dict(excluded=r['excluded'],**{k:r[k] for k in fields}) for r in exclusions]).to_csv(out/'exclusions.csv',index=False)
    verification=dict(result_sha256=digest(result),fills_sha256=result['fill_ledger_sha256'],
        account_reports=len(training)+len(rows)+len(robustness)+len(exclusions),fill_ledgers=len(ledger_sets))
    write(out/'verification.json',verification)
    print('ROBUSTNESS',json.dumps([{k:r[k] for k in fields} for r in robustness]),flush=True)
    print('ANNUAL500',json.dumps(years500),flush=True)
    print('VERIFICATION',json.dumps(verification),flush=True)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();study(args.source,args.out)
