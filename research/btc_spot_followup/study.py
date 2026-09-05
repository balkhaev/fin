"""Explicitly post-result diagnostics; never relabel an exploratory winner as selected."""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import math
import numpy as np
import pandas as pd
from research.btc_spot_regime.data import load
from research.btc_spot_regime.signals import build,NAMES,PRIMARY
from research.btc_spot_regime.engine import Settings,run
from research.btc_spot_regime.study import bootstrap,digest,write

SCENARIOS={
    'base':Settings(),
    'double_costs':Settings(fee=.002,slip=.001),
    'delay_24h':Settings(delay=24),
    'allocation_25pct':Settings(allocation=.25),
    'permanent_stop_7pct':Settings(drawdown_stop=.07),
    'allocation_25pct_double_costs':Settings(allocation=.25,fee=.002,slip=.001),
    'allocation_25pct_delay_24h':Settings(allocation=.25,delay=24),
}


def study(data_root,original_root,out):
    if out.exists():raise FileExistsError(out)
    original=json.loads((original_root/'results.json').read_text())
    if digest(original)!='dd8569eee0229f8d804577ad888e39fcc5d3be9ae1b8d79ae42b71ad122edd82':raise ValueError('Unexpected original result')
    for name,h in original['exact_source_sha256'].items():
        p=Path(__file__).parents[1]/'btc_spot_regime'/name
        if hashlib.sha256(p.read_bytes()).hexdigest()!=h:raise ValueError('Original code changed: '+name)
    data,audit=load(data_root);signals=build(data);rows=[];uncertainty={};all_fills=[]
    out.mkdir(parents=True)
    for name in NAMES+('buy_hold',):
        sig=signals[name] if name in signals else np.ones(len(data),np.int8)
        for scenario,settings in SCENARIOS.items():
            r,trades,fills,curve=run(data,sig,'2025-01-01','2026-09-01',settings)
            r.update(policy=name,scenario=scenario,post_result_exploratory=True)
            if scenario=='base':
                old=next(x for x in original['results'] if x['policy']==name and x['period']=='later' and x['scenario']=='base')
                for k in ('net','fees','round_trips','return_pct','max_close_drawdown_pct'):
                    if not math.isclose(r[k],old[k],abs_tol=1e-8):raise AssertionError('Baseline changed')
                uncertainty[name]=bootstrap(curve) if r['accounting_complete'] else None
            # Independently reconstruct cash and nonnegative coin balance from fills.
            cash=1000.;qty=0.
            for x in fills.to_dict('records'):
                sign=1 if x['side']=='sell' else -1
                cash+=sign*x['quantity']*x['price']-x['fee'];qty-=sign*x['quantity']
                if cash < -1e-7 or qty < -1e-9:raise AssertionError('Borrowed cash or short position')
            if r['accounting_complete']:
                if abs(qty)>1e-8 or not math.isclose(cash,r['final_cash'],abs_tol=1e-6):raise AssertionError('Ledger mismatch')
            prefix=f'{name}_{scenario}'
            write(out/f'{prefix}.json',r)
            trades.to_csv(out/f'{prefix}_trades.csv',index=False)
            fills.to_csv(out/f'{prefix}_fills.csv',index=False)
            curve.to_csv(out/f'{prefix}_equity.csv.gz',index=False,compression='gzip')
            rows.append(r);all_fills.append(dict(key=prefix,fills=fills.to_dict('records')))
            fields=('policy','scenario','return_pct','cagr_pct','max_close_drawdown_pct','adverse_hour_drawdown_pct','round_trips','fees','win_rate_pct','profit_factor','average_hold_hours','halted_at')
            print('DIAGNOSTIC',json.dumps({k:r[k] for k in fields}),flush=True)
    annual=[x for x in original['annual_reset'] if x['year']>=2024]
    for name in NAMES:
        print('YEARS',name,json.dumps([{k:x[k] for k in ('year','return_pct','round_trips','max_close_drawdown_pct')} for x in annual if x['policy']==name]),flush=True)
    result=dict(id='btc-spot-post-result-diagnostics',post_result=True,all_policies_reported=True,
       original_primary=PRIMARY,original_primary_was_not_profitable=True,original_challenger=None,
       original_sha256=digest(original),source_audit=audit,results=rows,annual_reset_2024_on=annual,
       uncertainty=uncertainty,fill_ledger_sha256=digest(all_fills),live_ready=False,annual_500_proven=False)
    write(out/'results.json',result)
    print('BOOTSTRAP',json.dumps(uncertainty),flush=True)
    print('RESULT_SHA256',digest(result),flush=True)
    pd.DataFrame([{k:v for k,v in r.items() if k!='settings'} for r in rows]).to_csv(out/'comparison.csv',index=False)
    pd.DataFrame([{k:v for k,v in r.items() if k!='settings'} for r in annual]).to_csv(out/'annual_reset.csv',index=False)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data',type=Path,required=True)
    p.add_argument('--original',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();study(a.data,a.original,a.out)
