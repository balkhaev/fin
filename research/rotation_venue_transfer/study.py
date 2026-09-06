"""Out-of-source research, not unseen market history or a trading service.

Uses the unchanged PR127 policy and PR126 simulator. No retuning or residual
writeoff. All four arms share the same cohort, rules, periods and cost scenarios.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
from research.annual_rotation.data import load as load_binance, SYMBOLS
from research.annual_rotation.model import simulate, Costs
from research.rotation_stability.policy import build, PRIMARY, WEEKLY
from .data import load as load_okx

PERIODS=(('2024','2024-01-01','2025-01-01'),
         ('later','2025-01-01','2026-09-01'),
         ('full_common','2024-01-01','2026-09-01'))
POLICY_SHA='5a88fe2da2bf8d28cb7f3bead91124465161a0ffdbd1ea6c452e640048d3db0f'
BASELINE_SHA='6c6dc96296f0281168e6670ff3231fe47c6bb46bb92e5cc2e4bd31c7e8b6a26a'


def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def write(path,x):path.write_text(json.dumps(x,indent=2,allow_nan=False))


def align_targets(values,source_index,destination_index):
    """Only explicitly unavailable PRE-warmup targets become zero, never prices."""
    if values.shape!=(len(source_index),len(SYMBOLS)):raise ValueError('Wrong target dimensions')
    if source_index.has_duplicates or destination_index.has_duplicates:raise ValueError('Duplicate target time')
    table=pd.DataFrame(values,index=source_index,columns=SYMBOLS).reindex(destination_index)
    missing=table.isna().any(axis=1)
    if (missing & (table.index>=source_index[0])).any():raise ValueError('Missing required target support')
    table.loc[missing]=0.
    return table.to_numpy(float)


def audit_fills(fills,report,step,frames,end):
    cash=report['initial'];units={s:0 for s in SYMBOLS}
    for row in fills.itertuples():
        sign=1 if row.side=='sell' else -1
        units[row.symbol]-=sign*round(row.quantity/step)
        cash+=sign*row.quantity*row.price-row.fee
        if cash < -1e-6 or min(units.values())<0:raise AssertionError('Borrowed coins/cash in single spot account')
        if not math.isclose(cash,row.cash_after,abs_tol=1e-5,rel_tol=1e-10):raise AssertionError('Cashflow mismatch')
    remaining={s:n*step for s,n in units.items() if n}
    day=pd.Timestamp(end,tz='UTC')-pd.Timedelta(days=1)
    mark=sum(q*frames[s].loc[day,'close']*(1-report['settings']['fee'])*(1-report['settings']['slip']) for s,q in remaining.items())
    if not math.isclose(cash+mark,report['final_equity'],abs_tol=1e-5,rel_tol=1e-10):raise AssertionError('Residual-inclusive equity mismatch')
    return dict(cash_reconciled=True,remaining_coins=remaining,residual_valued_not_fabricated_sale=True)


def monthly(curve,initial):
    path=np.r_[initial,curve.equity.to_numpy()]
    dates=pd.to_datetime(curve.time)-pd.Timedelta(days=1)
    values=pd.Series(path[1:]/path[:-1]-1,index=dates)
    result=values.groupby([dates.dt.year,dates.dt.month]).apply(lambda r:(1+r).prod()-1)
    # Use explicit arrays: group labels must not align integer indices against dates.
    result=values.groupby([values.index.year,values.index.month]).apply(lambda r:(1+r).prod()-1)
    return [dict(year=int(y),month=int(m),return_pct=float(v*100)) for (y,m),v in result.items()]


def study(binance_root,okx_root,prior_root,out):
    out=Path(out)
    if out.exists():raise FileExistsError('New results directory required')
    here=Path(__file__).parents[1]
    if hashlib.sha256((here/'rotation_stability/policy.py').read_bytes()).hexdigest()!=POLICY_SHA:
        raise ValueError('Frozen policy was modified')
    prior=json.loads((Path(prior_root)/'static-report/results.json').read_text())
    if digest(prior)!=BASELINE_SHA:raise ValueError('Unexpected PR127 results')
    for name in ('data.py','model.py'):
        archived=Path(prior_root)/'delivery/research/annual_rotation'/name
        if archived.read_bytes()!=(here/'annual_rotation'/name).read_bytes():raise ValueError('Frozen simulator or Binance loader changed')
    b,ba=load_binance(Path(binance_root));o,oa=load_okx(Path(okx_root))
    if ba['manifest_sha256']!='da9ca6d1e782e8ef6c816390ef3e6ea363eec53a67f58592a8505d754bf5bfe2':
        raise ValueError('Unexpected Binance dataset')
    venues={'binance':b,'okx':o};targets={};traces={}
    for venue,frames in venues.items():
        all_targets,diagnostics=build(frames)
        targets[venue]=all_targets[PRIMARY];traces[venue]=diagnostics[PRIMARY]
    # Finite lookbacks must have recovered before the full-common evaluation.
    crop={s:f.loc['2023-01-01':] for s,f in b.items()};cropped,_=build(crop)
    bidx=b[SYMBOLS[0]].index;oidx=o[SYMBOLS[0]].index
    selection=bidx>=pd.Timestamp('2024-01-01',tz='UTC')
    cropped_aligned=align_targets(cropped[PRIMARY],crop[SYMBOLS[0]].index,bidx)
    np.testing.assert_allclose(targets['binance'][selection],cropped_aligned[selection],atol=1e-12,rtol=1e-11)
    out.mkdir(parents=True)
    for name,t in traces.items():t.to_csv(out/f'{name}_target_diagnostics.csv',index=False)
    rows=[];ledgers=[]
    def one(signal_venue,execution_venue,period,start,end,scenario='base',cost=Costs()):
        frames=venues[execution_venue]
        source_index=venues[signal_venue][SYMBOLS[0]].index
        dest_index=frames[SYMBOLS[0]].index
        t=align_targets(targets[signal_venue],source_index,dest_index)
        r,f,c=simulate(frames,t,WEEKLY,start,end,cost)
        if signal_venue==execution_venue=='binance' and period=='later' and scenario=='base':
            old=next(x for x in prior['results'] if x['policy']==PRIMARY and x['period']=='later')
            for k,v in r.items():
                if v!=old[k]:raise AssertionError('Existing Binance control changed: '+k)
        reconciliation=audit_fills(f,r,cost.step,frames,end)
        arm=signal_venue+'_to_'+execution_venue;key=arm+'_'+period+'_'+scenario
        months=monthly(c,cost.initial)
        row=dict(r,arm=arm,signal_source=signal_venue,execution_source=execution_venue,
            period=period,scenario=scenario,monthly=months,independent_fill_audit=reconciliation)
        rows.append(row);ledgers.append(dict(key=key,fills=f.to_dict('records')))
        f.to_csv(out/f'{key}_fills.csv',index=False);c.to_csv(out/f'{key}_equity.csv',index=False)
        fields=('arm','period','scenario','return_pct','cagr_pct','max_close_drawdown_pct','order_fills','closed_asset_positions','fees','accounting_complete','liquidity_rejections')
        print('TRANSFER_RESULT',json.dumps({k:row[k] for k in fields}),flush=True)
    for signal in venues:
        for execution in venues:
            for name,start,end in PERIODS:one(signal,execution,name,start,end)
    for scenario,cost in [('double_costs',Costs(fee=.002,slip=.001)),('extra_day_delay',Costs(extra_delay=1)),('capital1000',Costs(initial=1000))]:
        for name,start,end in PERIODS[1:]:one('okx','okx',name,start,end,scenario,cost)
    common=oidx[oidx>=pd.Timestamp('2024-01-01',tz='UTC')]
    price_comparison=[]
    for s in SYMBOLS:
        bc=b[s].loc[common];oc=o[s].loc[common]
        dif=(oc.close/bc.close-1).abs()*10000
        ratio=oc.quote_volume/bc.quote_volume.replace(0,np.nan)
        price_comparison.append(dict(symbol=s,compared_days=len(common),median_abs_close_difference_bps=float(dif.median()),
            p99_abs_close_difference_bps=float(dif.quantile(.99)),maximum_abs_close_difference_bps=float(dif.max()),
            largest_difference_day=str(dif.idxmax().date()),median_okx_binance_quote_volume_ratio=float(ratio.median())))
    bt=pd.DataFrame(targets['binance'],index=bidx).loc[common].to_numpy()
    ot=pd.DataFrame(targets['okx'],index=oidx).loc[common].to_numpy()
    l1=np.abs(bt-ot).sum(axis=1)
    signal_comparison=dict(compared_days=len(common),mean_L1_target_difference=float(l1.mean()),
        days_L1_exceeds_001=int((l1>.01).sum()),days_positive_binance_target=int((bt.sum(axis=1)>0).sum()),
        days_positive_okx_target=int((ot.sum(axis=1)>0).sum()),
        zero_vs_nonzero_gate_disagreements=int(((bt.sum(axis=1)>0)!=(ot.sum(axis=1)>0)).sum()))
    result=dict(id='rotation-venue-transfer-20260906',primary=PRIMARY,rows=rows,
        data={'binance':ba,'okx':oa},price_comparison=price_comparison,signal_comparison=signal_comparison,
        frozen_policy_verified=True,original_simulator_verified=True,original_later_baseline_exactly_reproduced=True,
        warmup_equivalence_checked=True,fill_ledger_sha256=digest(ledgers),
        new_time_holdout=False,independent_price_venue=True,independent_market_regime=False,
        stable_profit_proven=False,annual500proven=False,live_orders=False,
        limitations=['Fixed surviving nine-asset cohort and already researched dates.',
        'OKX historical REST is a separate venue, not independent economic returns or a future holdout.',
        'Same scenario fee/slippage/filter settings; no claim of historical actual account entitlements.',
        'Information venue and execution venue are separate arms, not transfers or arbitrage.',
        'Original incomplete/residual flags unchanged; no forced dust writeoff.',
        'No local execution, no actual exchange fills, taxes, custody or infrastructure costs.'])
    write(out/'results.json',result)
    flatkeys=('arm','period','scenario','return_pct','cagr_pct','max_close_drawdown_pct','worst_rolling_365_pct','order_fills','rebalance_days','closed_asset_positions','fees','accounting_complete','open_assets','liquidity_rejections')
    pd.DataFrame([{k:r[k] for k in flatkeys} for r in rows]).to_csv(out/'comparison.csv',index=False)
    pd.DataFrame([dict(arm=r['arm'],period=r['period'],scenario=r['scenario'],**a) for r in rows for a in r['annual']]).to_csv(out/'annual.csv',index=False)
    pd.DataFrame(price_comparison).to_csv(out/'venue_data_comparison.csv',index=False)
    verification=dict(result_sha256=digest(result),fill_ledger_sha256=result['fill_ledger_sha256'],report_count=len(rows))
    write(out/'verification.json',verification)
    print('SOURCE_COMPARISON',json.dumps(price_comparison),flush=True)
    print('SIGNAL_COMPARISON',json.dumps(signal_comparison),flush=True)
    print('VERIFICATION',json.dumps(verification),flush=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('binance','okx','prior','out'):parser.add_argument('--'+name,type=Path,required=True)
    a=parser.parse_args();study(a.binance,a.okx,a.prior,a.out)
