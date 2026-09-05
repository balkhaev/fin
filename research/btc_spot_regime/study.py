"""Profit-first historical research; neither annual guarantees nor live orders."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import argparse
import numpy as np
import pandas as pd
from .data import download,load
from .signals import build,PRIMARY,NAMES
from .engine import Settings,run


def write(path,obj):path.write_text(json.dumps(obj,indent=2,allow_nan=False,ensure_ascii=False))
def digest(obj):return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def bootstrap(curve,capital=1000.):
    s=curve.set_index(pd.to_datetime(curve.time_ms,unit='ms',utc=True)).equity
    daily=s.resample('1D',closed='right',label='right').last().dropna().to_numpy()
    if len(daily)<365:return None
    x=np.diff(np.log(np.r_[capital,daily]));rng=np.random.default_rng(20260906)
    length=30;n=len(x);means=[]
    for _ in range(2000):
        starts=rng.integers(0,n,size=(n+length-1)//length)
        indices=(starts[:,None]+np.arange(length)[None,:])%n
        means.append(x[indices.ravel()[:n]].mean())
    bounds=(np.exp(np.quantile(means,[.025,.975])*365.25)-1)*100
    return dict(method='exploratory circular 30-day block bootstrap, 2000 samples',
        annualized_return_95pct_interval=[float(v) for v in bounds],
        not_corrected_for_repeated_research=True,not_a_forecast=True)


def study(root,out):
    out=Path(out)
    if out.exists():raise FileExistsError('Use fresh results directory')
    out.mkdir(parents=True)
    data,audit=load(root);write(out/'data_audit.json',audit)
    print('DATA',json.dumps(audit),flush=True)
    sig=build(data);development=[]
    for name in NAMES:
        r,*_=run(data,sig[name],'2020-01-01','2024-01-01')
        r['policy']=name;development.append(r)
    eligible=[r for r in development if r['accounting_complete'] and r['return_pct']>0 and r['round_trips']>=12]
    eligible.sort(key=lambda r:(-r['cagr_pct']/max(10.,abs(r['max_close_drawdown_pct'])),r['policy']))
    challenger=eligible[0]['policy'] if eligible else None
    lock=dict(primary=PRIMARY,challenger=challenger,selected_with_data_before='2024-01-01',
        later_returns_used=False,eligible_count=len(eligible),development=development)
    write(out/'selection_lock.json',lock)
    print('SELECTION',json.dumps({k:v for k,v in lock.items() if k!='development'}),flush=True)
    summaries=[];annual=[];metrics={};all_fills=[]
    for name in NAMES+('buy_hold','cash'):
        values=sig[name] if name in sig else np.full(len(data),1 if name=='buy_hold' else 0,np.int8)
        for period,start,end in (('validation','2024-01-01','2025-01-01'),
                                 ('later','2025-01-01','2026-09-01'),
                                 ('full','2020-01-01','2026-09-01')):
            r,trades,fills,curve=run(data,values,start,end)
            r.update(policy=name,period=period,scenario='base');summaries.append(r);metrics[name,period]=r
            if period=='later' or name in (PRIMARY,challenger,'buy_hold'):
                prefix=f'{name}_{period}_base'
                trades.to_csv(out/f'{prefix}_trades.csv',index=False)
                fills.to_csv(out/f'{prefix}_fills.csv',index=False)
                curve.to_csv(out/f'{prefix}_equity.csv.gz',index=False,compression='gzip')
                all_fills.append(dict(key=prefix,fills=fills.to_dict('records')))
            if period=='later':print('LATER',json.dumps(r),flush=True)
        # Reset annual accounts are explicitly not stitched into the continuous series.
        for year in range(2020,2027):
            r,*_=run(data,values,f'{year}-01-01',f'{year+1}-01-01' if year<2026 else '2026-09-01')
            r.update(policy=name,year=year,full_calendar_year=year<2026,capital_reset=True);annual.append(r)
    costs={'double_costs':replace(Settings(),fee=.002,slip=.001),
           'delay_24h':replace(Settings(),delay=24),
           'allocation_25pct':replace(Settings(),allocation=.25),
           'permanent_stop_7pct':replace(Settings(),drawdown_stop=.07)}
    focus=list(dict.fromkeys([PRIMARY]+([challenger] if challenger else [])+['buy_hold']))
    uncertainty={}
    for name in focus:
        values=sig[name] if name in sig else np.ones(len(data),np.int8)
        r,_,_,base_curve=run(data,values,'2025-01-01','2026-09-01')
        if r['accounting_complete']:uncertainty[name]=bootstrap(base_curve)
        for scenario,settings in costs.items():
            r,trades,fills,curve=run(data,values,'2025-01-01','2026-09-01',settings)
            r.update(policy=name,period='later',scenario=scenario);summaries.append(r)
            prefix=f'{name}_later_{scenario}'
            trades.to_csv(out/f'{prefix}_trades.csv',index=False)
            fills.to_csv(out/f'{prefix}_fills.csv',index=False)
            curve.to_csv(out/f'{prefix}_equity.csv.gz',index=False,compression='gzip')
            all_fills.append(dict(key=prefix,fills=fills.to_dict('records')))
            print('FOCUS_STRESS',json.dumps(r),flush=True)
    tested_files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}
    validation={n:bool(metrics[n,'validation']['return_pct'] is not None and metrics[n,'validation']['return_pct']>0) for n in focus}
    fivehundred=[dict(policy=r['policy'],year=r['year'],net_return_pct=r['return_pct']) for r in annual
                  if r['full_calendar_year'] and r['return_pct'] is not None and r['return_pct']>500]
    result=dict(id='btc-spot-regime-20260906',selection=lock,data=audit,results=summaries,annual_reset=annual,
        primary_validation_positive=validation.get(PRIMARY),focus_validation=validation,
        uncertainty=uncertainty,observed_full_years_above_500=fivehundred,
        profit_does_not_establish_alpha=True,annual_500_proven=False,live_ready=False,
        exact_source_sha256=tested_files,fill_ledger_sha256=digest(all_fills),
        limitations=['BTC spot long/cash, not original high-frequency futures Pressure.',
         'Old 0.25% risk/7% permanent halt was NOT silently retained: separate 7% comparator reported.',
         'Full allocation can have large drawdowns; 25% refers to entry cash allocation, not constant portfolio exposure.',
         'History including August previously seen by the research program; no pristine holdout or live forward.',
         'Hourly OHLC plus assumed adverse slippage, not actual exchange fills or exact L2 impact.',
         '0.1% commission/side and 0.05% slippage/side are conservative declared scenarios, not historic account entitlements.',
         'Contract filters are scenario assumptions, not time-varying historical exchangeInfo.',
         'USD figures denote USDT numeraire and ignore taxes, infrastructure, custody, counterparty and depeg costs.',
         'Spot positions use cash, so derivative funding and borrowing are not applicable; cash earns zero.',
         'Cumulative multi-year profit is never labeled an annual return.'])
    write(out/'results.json',result)
    flat=lambda rows:[{k:v for k,v in r.items() if k!='settings'} for r in rows]
    pd.DataFrame(flat(development)).to_csv(out/'development.csv',index=False)
    pd.DataFrame(flat(summaries)).to_csv(out/'comparison.csv',index=False)
    pd.DataFrame(flat(annual)).to_csv(out/'annual_reset.csv',index=False)
    final=dict(primary=PRIMARY,challenger=challenger,validation=validation,
       selected_results=[r for r in summaries if r['policy'] in focus],
       full_years_above_500=fivehundred,uncertainty=uncertainty,
       result_sha256=digest(result),annual500proven=False,live_ready=False)
    write(out/'summary.json',final)
    print('FINAL',json.dumps(final),flush=True)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--download',action='store_true');a=p.parse_args()
    if a.download:download(a.data)
    study(a.data,a.out)
