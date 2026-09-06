"""Controlled test of incremental flow information on previously examined history.

No actual orders, hyperparameter search or replacement of an existing strategy.
The seven-day forecasting label is not a simulated completed trade; the native
cash-and-coin ledger is the only source of reported portfolio returns.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
from research.annual_rotation.data import SYMBOLS
from research.annual_rotation.model import Costs, Config, simulate
from research.rotation_stability.policy import build as guard_build, PRIMARY as GUARD
from research.rotation_venue_transfer.data import load as okx_load
from .data import load
from .learning import prepare, forecasts, targets, POLICIES, PRIMARY, LAG, HORIZON

CORE_SHA='e4c9b244e5044dd34062dc83e56c681e33e1e352245529b39d9bc1fa252d95e8'


def digest(x):
    return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def write(p,x):
    p.write_text(json.dumps(x,indent=2,ensure_ascii=False,allow_nan=False))


def information_test(features, predictions, start, end):
    mask=(features.dates>=pd.Timestamp(start,tz='UTC'))&(features.dates<pd.Timestamp(end,tz='UTC'))
    valid=features.valid & np.isfinite(features.labels)
    for a in predictions.values(): valid &= np.isfinite(a)
    # A validation-period score cannot consume a label ending after its boundary.
    mature_in_period = features.dates + pd.Timedelta(days=LAG+HORIZON) < pd.Timestamp(end,tz='UTC')
    valid &= (mask & mature_in_period)[:,None]
    rows=[]; paired=[]; mse={}; averages={}
    for name,a in predictions.items():
        err=(features.labels-a)**2
        mse[name]=float(err[valid].mean())
    y=features.labels
    mse['zero_forecast']=float((y[valid]**2).mean())
    for t in np.flatnonzero(valid.any(axis=1)):
        good=valid[t]
        loss={name:float(np.mean((y[t,good]-a[t,good])**2)) for name,a in predictions.items()}
        paired.append(loss['price']-loss['flow'])
        rows.append(dict(signal_date=str(features.dates[t].date()), n_assets=int(good.sum()),
            price_minus_flow_mse=paired[-1], **loss))
    delta=np.asarray(paired)
    if not len(delta): raise ValueError('No matured paired predictions')
    rng=np.random.default_rng(20260906)
    means=[]
    for _ in range(2000):
        starts=rng.integers(0,len(delta),size=(len(delta)+29)//30)
        sampled=((starts[:,None]+np.arange(30))%len(delta)).ravel()[:len(delta)]
        means.append(float(delta[sampled].mean()))
    lo,hi=np.quantile(means,[.025,.975])
    report=dict(start=start,end_exclusive=end,matured_asset_labels=int(valid.sum()),
        signal_days=len(rows),last_matured_signal=rows[-1]['signal_date'],
        mse=mse,mean_daily_price_minus_flow_mse=float(delta.mean()),
        flow_relative_mse_improvement_pct=100*(mse['price']-mse['flow'])/mse['price'],
        block_bootstrap95_mean_improvement=[float(lo),float(hi)],
        block_days=30,bootstrap_draws=2000,labels_overlap=True,
        multiplicity_corrected=False,unseen_market_time=False)
    return report,pd.DataFrame(rows)


def audit_fills(fills,report,frames,cost,end):
    cash=cost.initial; units={s:0 for s in SYMBOLS}
    for f in fills.itertuples():
        sign=1 if f.side=='sell' else -1
        cash+=sign*f.quantity*f.price-f.fee
        units[f.symbol]-=sign*round(f.quantity/cost.step)
        if cash<-1e-6 or min(units.values())<0: raise AssertionError('Borrowing in ledger')
        if not math.isclose(cash,f.cash_after,abs_tol=1e-6,rel_tol=1e-10): raise AssertionError('Cash mismatch')
    remaining={s:n*cost.step for s,n in units.items() if n}
    day=pd.Timestamp(end,tz='UTC')-pd.Timedelta(days=1)
    residual=sum(q*frames[s].loc[day,'close']*(1-cost.slip)*(1-cost.fee) for s,q in remaining.items())
    if not math.isclose(cash+residual,report['final_equity'],abs_tol=1e-6,rel_tol=1e-10):
        raise AssertionError('Terminal holdings do not reconcile')
    return dict(cash_reconciled=True,remaining_coins=remaining,
                residual_marked_value=residual,no_forced_sale_or_writeoff=True)


def monthly(curve,capital):
    days=pd.DatetimeIndex(pd.to_datetime(curve.time)-pd.Timedelta(days=1))
    a=np.r_[capital,curve.equity.to_numpy()]
    daily=pd.Series(a[1:]/a[:-1]-1,index=days)
    vals=daily.groupby([days.year,days.month]).apply(lambda x:(1+x).prod()-1)
    return [dict(year=int(y),month=int(m),return_pct=float(v*100)) for (y,m),v in vals.items()]


def study(source,out):
    source=Path(source);out=Path(out)
    if out.exists(): raise FileExistsError('New evidence directory required')
    here=Path(__file__).parents[1]
    if hashlib.sha256((here/'annual_rotation/model.py').read_bytes()).hexdigest()!=CORE_SHA:
        raise ValueError('Original simulator changed')
    frames,audit=load(source/'prior/rotation-data')
    old=json.loads((source/'prior/static-report/results.json').read_text())
    features=prepare(frames)
    predictions,fit_audit=forecasts(features)
    out.mkdir(parents=True)
    write(out/'fit_audit.json',fit_audit);write(out/'data_audit.json',audit)
    information={}
    for period,start,end in [('validation','2024-01-01','2025-01-01'),('later','2025-01-01','2026-09-01')]:
        information[period],daily=information_test(features,predictions,start,end)
        daily.to_csv(out/f'{period}_paired_prediction_losses.csv',index=False)
    prediction_rows=[]
    for t in np.flatnonzero((features.dates>=pd.Timestamp('2022-01-01',tz='UTC'))):
        for k,s in enumerate(SYMBOLS):
            if not all(np.isfinite(a[t,k]) for a in predictions.values()): continue
            prediction_rows.append(dict(signal_date=str(features.dates[t].date()),symbol=s,
                label_maturity=str((features.dates[t]+pd.Timedelta(days=LAG+HORIZON)).date()),
                realized_normalized_label=float(features.labels[t,k]) if np.isfinite(features.labels[t,k]) else None,
                horizon_volatility=float(features.horizon_vol[t,k]),
                **{name:float(a[t,k]) for name,a in predictions.items()}))
    pd.DataFrame(prediction_rows).to_csv(out/'predictions.csv',index=False)
    matrix={name:targets(features,predictions,name) for name in POLICIES}
    guarded,_=guard_build(frames);matrix['guarded_control']=guarded[GUARD]
    matrix['btc_hold']=np.zeros(features.valid.shape);matrix['btc_hold'][:,0]=1.
    matrix['cash']=np.zeros(features.valid.shape)
    rows=[];ledgers=[]
    def execute(name,period,start,end,cost=Costs(),venue_frames=None,w=None,excluded=None):
        execution=frames if venue_frames is None else venue_frames
        target=matrix[name] if w is None else w
        freq=1 if name=='flow7_daily' else 7
        report,fills,curve=simulate(execution,target,Config('raw',21,3,freq),start,end,cost,hold_only=name=='btc_hold')
        reconciliation=audit_fills(fills,report,execution,cost,end)
        if name=='guarded_control' and period=='later':
            previous=next(x for x in old['results'] if x['policy']==GUARD and x['period']=='later')
            for key,val in report.items():
                if val!=previous[key]: raise AssertionError('Prior control differs: '+key)
        month=monthly(curve,cost.initial)
        r=dict(report,policy=name,period=period,execution_venue='binance' if execution is frames else 'okx',
               excluded=excluded,fill_audit=reconciliation,months=month,
               positive_months=sum(x['return_pct']>1e-10 for x in month),
               zero_months=sum(abs(x['return_pct'])<=1e-10 for x in month),
               negative_months=sum(x['return_pct']<-1e-10 for x in month))
        key=name+'_'+period+('_without_'+excluded if excluded else '')
        rows.append(r);ledgers.append(dict(key=key,fills=fills.to_dict('records')))
        fills.to_csv(out/f'{key}_fills.csv',index=False);curve.to_csv(out/f'{key}_equity.csv',index=False)
        print('RESULT',json.dumps({k:r[k] for k in ('policy','period','excluded','return_pct','diagnostic_return_pct','cagr_pct','max_close_drawdown_pct','order_fills','accounting_complete')}),flush=True)
        return r
    for name in matrix:
        for period,start,end in [('full','2022-01-01','2026-09-01'),('validation','2024-01-01','2025-01-01'),('later','2025-01-01','2026-09-01')]:
            execute(name,period,start,end)
    for name in (PRIMARY,'price7_weekly'):
        for scenario,cost in [('double_costs',Costs(fee=.002,slip=.001)),('extra_day_delay',Costs(extra_delay=1)),('capital1000',Costs(initial=1000))]:
            for period,start in [('full','2022-01-01'),('later','2025-01-01')]:
                execute(name,period+'_'+scenario,start,'2026-09-01',cost)
    okx,okx_audit=okx_load(source/'new-source/okx-data')
    common=okx[SYMBOLS[0]].index
    aligned=pd.DataFrame(matrix[PRIMARY],index=features.dates).loc[common].to_numpy()
    execute(PRIMARY,'later_okx_execution','2025-01-01','2026-09-01',venue_frames=okx,w=aligned)
    for asset in SYMBOLS:
        execute(PRIMARY,'full','2022-01-01','2026-09-01',w=targets(features,predictions,PRIMARY,asset),excluded=asset)
    def get(period):return next(r for r in rows if r['policy']==PRIMARY and r['period']==period and r['excluded'] is None)
    full,later=get('full'),get('later')
    gates=dict(complete_full_and_later=full['accounting_complete'] and later['accounting_complete'],
        full_positive=(full['return_pct'] or -1)>0,later_positive=(later['return_pct'] or -1)>0,
        full_close_drawdown_not_below20=full['max_close_drawdown_pct']>=-20,
        worst_year_not_below10=full['worst_rolling_365_pct']>=-10,
        doubled_cost_later_positive=(get('later_double_costs')['return_pct'] or -1)>0,
        delayed_later_positive=(get('later_extra_day_delay')['return_pct'] or -1)>0)
    for period,r in information.items():
        gates[period+'_incremental_information_positive']=r['mean_daily_price_minus_flow_mse']>0 and r['block_bootstrap95_mean_improvement'][0]>0
    full_years=[a for a in full['annual'] if a['full_year']]
    annual_target_met=bool(full['accounting_complete'] and (full['cagr_pct'] or 0)>=500 and full_years and all(a['return_pct']>=500 for a in full_years))
    result=dict(id='flow-learning-20260906',primary=PRIMARY,source=audit,okx_execution_source=okx_audit,
        predictive_information=information,rows=rows,admission_gates=gates,
        admitted_for_further_research=all(gates.values()),annual500_observed_all_years=annual_target_met,
        fitting=dict(monthly_fits=len(fit_audit),successful_months=sum(x['status']=='fitted' for x in fit_audit),
            coefficient_artifact='fit_audit.json',past_only_embargo_days=7,matched_samples=True),
        ledger_hash=digest(ledgers),source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')},
        original_simulator_sha256=CORE_SHA,old_guard_later_exactly_reproduced=True,
        data_time_holdout=False,live_ready=False,real_orders=0,stable_future_profit_proven=False,
        limitations=['All history and surviving nine-asset selection reused; no pristine OOS.',
        'Training only matured past labels prevents lookahead, not adaptive-research overfitting.',
        'Taker volume is aggressor-side activity, not capital flow or forced liquidations.',
        'Forecasts are normalized seven-day targets; they are not completed trades.',
        'Ordinary ridge coefficients are not calibrated probabilities or causal effects.',
        'Cost hurdle, exchange filters and capacity are scenarios; actual order-book fills unverified.',
        'Native residual nulls preserved; no terminal dust writeoff.',
        'Leave-one-out removes trading permission only, not information or fitted coefficients.',
        'Same Binance predictions executed on OKX, no fabricated OKX aggressor history.',
        'Tax, custody, infrastructure, counterparty and stablecoin risks omitted.'])
    write(out/'results.json',result)
    columns=('policy','period','execution_venue','excluded','return_pct','diagnostic_return_pct','cagr_pct','max_close_drawdown_pct','worst_rolling_365_pct','order_fills','rebalance_days','closed_asset_positions','fees','positive_months','zero_months','negative_months','accounting_complete')
    pd.DataFrame([{k:r[k] for k in columns} for r in rows]).to_csv(out/'comparison.csv',index=False)
    pd.DataFrame([dict(policy=r['policy'],period=r['period'],excluded=r['excluded'],**a) for r in rows for a in r['annual']]).to_csv(out/'annual.csv',index=False)
    write(out/'verification.json',dict(result_sha256=digest(result),ledger_sha256=result['ledger_hash'],
        report_count=len(rows),ledger_count=len(ledgers),admission_gates=gates))
    print('INFORMATION',json.dumps(information),flush=True)
    print('GATES',json.dumps(gates),flush=True)
    print('VERIFICATION', (out/'verification.json').read_text(),flush=True)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();study(a.source,a.out)
