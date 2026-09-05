"""Reproducible 54-configuration study; not a live trading implementation."""
from __future__ import annotations
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform
import numpy as np
import pandas as pd
from .data import load
from .model import Config,FAMILIES,grid,features,signals
from .engine import Costs,market,run


def save(path,value):path.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False))
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def research(root,out):
    if out.exists():raise FileExistsError('New result directory required')
    out.mkdir(parents=True)
    d,audit=load(root);save(out/'data_audit.json',audit)
    print('DATA_AUDIT',json.dumps(audit),flush=True)
    f=features(d);m=market(d);training=[]
    for cfg in grid():
        x=signals(d,f,cfg)
        years=[run(m,x,f'{y}-01-01',f'{y+1}-01-01')[0] for y in (2022,2023)]
        valid=all(y['return_pct'] is not None for y in years)
        score=min(y['return_pct'] for y in years) if valid else -1000000.
        training.append(dict(config=asdict(cfg),id=cfg.id,years=years,score=score,total=sum(y['net_closed'] for y in years)))
        if len(training)%9==0:print('DISCOVERY',len(training),'of',len(grid()),flush=True)
    save(out/'all_54_training.json',training)
    finalists=[]
    for family in FAMILIES:
        winner=sorted([x for x in training if x['config']['family']==family],key=lambda x:(-x['score'],-x['total'],x['id']))[0]
        cfg=Config(**winner['config']);v=run(m,signals(d,f,cfg),'2024-01-01','2025-01-01')[0]
        acceptable=all(x['return_pct'] is not None and x['return_pct']>0 and (x['profit_factor'] or 0)>=1.1 for x in winner['years']+[v])
        acceptable=acceptable and sum(x['trades'] for x in winner['years'])>=300 and v['trades']>=100
        finalists.append(dict(id=cfg.id,config=asdict(cfg),training=winner['years'],validation=v,admitted=bool(acceptable)))
    admitted=[x for x in finalists if x['admitted']]
    selected=sorted(admitted or finalists,key=lambda x:(-(x['validation']['return_pct'] if x['validation']['return_pct'] is not None else -1000000),x['id']))[0]
    selection=dict(finalists=finalists,selected=selected,admitted_count=len(admitted),
       final_period_used_for_selection=False,all_periods_previously_seen_as_candles=True,
       label='admitted_for_further_research' if admitted else 'REJECTED_diagnostic_finalist')
    save(out/'selection.json',selection)
    selection_sha=digest(selection)
    print('SELECTION_LOCK',json.dumps(dict(id=selected['id'],admitted_count=len(admitted),sha256=selection_sha)),flush=True)
    # Only the three frozen family finalists reach the later historical period.
    family_later=[]
    for r in finalists:
        stats,_,_=run(m,signals(d,f,Config(**r['config'])),'2025-01-01','2026-08-01')
        family_later.append(dict(id=r['id'],**stats))
    cfg=Config(**selected['config']);x=signals(d,f,cfg)
    scenarios={'primary':Costs(),'double_slippage':Costs(slip=.0002),'extra_minute_latency':Costs(latency=1),
               'double_commission':Costs(fee=.001),'zero_cost_ablation':Costs(fee=0,slip=0)}
    annual=[];continuous=[]
    for name,cost in scenarios.items():
        for year in range(2022,2027):
            end=f'{year+1}-01-01' if year<2026 else '2026-08-01'
            stats,trades,daily=run(m,x,f'{year}-01-01',end,cost)
            stats.update(scenario=name,year=year,full_calendar_year=year<2026,capital_reset=True)
            annual.append(stats)
            if name=='primary':
                trades.to_csv(out/f'primary_{year}_trades.csv.gz',index=False,compression='gzip')
        stats,trades,daily=run(m,x,'2025-01-01','2026-08-01',cost)
        stats.update(scenario=name,capital_reset=False,history_status='REUSED_not_pristine_OOS')
        continuous.append(stats)
        trades.to_csv(out/f'{name}_continuous_trades.csv.gz',index=False,compression='gzip')
        daily.to_csv(out/f'{name}_continuous_equity.csv',header=['equity'])
        print('CONTINUOUS_RESULT',json.dumps(stats),flush=True)
    primary=continuous[0];annual2025=next(z for z in annual if z['scenario']=='primary' and z['year']==2025)
    numeric=bool((annual2025['return_pct'] or -100)>500 and (primary['cagr_pct'] or -100)>500)
    summary=dict(id='btc-flow-continuous-20260905',protocol_sha256=hashlib.sha256(Path(__file__).with_name('protocol.json').read_bytes()).hexdigest(),
      selection_sha256=selection_sha,data=audit,configuration_count=54,selection=selection,
      annual=annual,continuous=continuous,family_later=family_later,cash_control_return_pct=0.,
      numeric_500_threshold_met=numeric,annual_target_proven=False,live_ready=False,
      native_local_execution_performed=False,execution_environment='GitHub Actions; local execution tools unavailable',
      environment=dict(python=platform.python_version(),pandas=pd.__version__,numpy=np.__version__),
      limitations=['NEW minute spot/perp model, not original second-resolution BTC Pressure.',
        'Periods were previously seen in other research; not an independent unseen annual test.',
        'Actual funding rates, but settlement amounts use minute mark-price OPEN proxy; exact API returned restriction.',
        'Adverse funding range is fixed-fill arithmetic, not a causal alternate strategy.',
        'No full-depth queue or actual fills; all entries taker next-open with scenario fees/slippage.',
        'Historical contract filters and liquidation-margin mechanics are not certified.',
        'Drawdown halts can be exceeded on gaps; annual reset tables do not replace continuous account.',
        'No live trading, no portfolio scheduler changes; no future annual guarantee.'])
    save(out/'results.json',summary)
    flatten=lambda rows:[{k:v for k,v in r.items() if k!='costs'} for r in rows]
    pd.DataFrame(flatten(annual)).to_csv(out/'annual_returns.csv',index=False)
    pd.DataFrame(flatten(continuous)).to_csv(out/'continuous_results.csv',index=False)
    compact=dict(selected_id=selected['id'],admitted_count=len(admitted),data=audit,
                 primary_annual=[{k:r[k] for k in ('year','full_calendar_year','return_pct','trades','halted_at')} for r in annual if r['scenario']=='primary'],
                 continuous=[{k:r[k] for k in ('scenario','days','return_pct','cagr_pct','trades','trades_per_day','max_drawdown_pct','halted_at','fees','funding','gross_closed','funding_events_held','same_fills_adverse_funding_net')} for r in continuous],
                 annual_target_proven=False,results_canonical_sha256=digest(summary))
    save(out/'summary.json',compact)
    print('FINAL_SUMMARY',json.dumps(compact),flush=True)
    return summary

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();research(a.data,a.out)
