"""Phased BTC research. August cannot be loaded by the discovery command."""
from __future__ import annotations
import argparse,hashlib,json,zipfile
from pathlib import Path
from dataclasses import asdict
import numpy as np
import pandas as pd
from .models import Config,FAMILIES,grid,aggregate,prepare_bars,signals
from .engine import Costs,Risk,market,run
from research.btc_flow.study import load_data,COLS


def write(path,obj):path.write_text(json.dumps(obj,indent=2,allow_nan=False))
def prepare(d,f):return {t:prepare_bars(aggregate(d,t),f) for t in (1,5,15,60,240,1440)}


def discovery(d,f,out):
    """Only 2022-2024 data reaches this function's signals or replay."""
    d=d.loc[d.index<=pd.Timestamp('2025-01-01',tz='UTC')].copy()
    f=f.loc[f.calc_time<pd.Timestamp('2025-01-01',tz='UTC').timestamp()*1000].copy()
    prepared=prepare(d,f);m=market(d,f);rows=[]
    for cfg in grid():
        inputs=signals(d,prepared,cfg)
        annual=[run(m,inputs,f'{y}-01-01',f'{y+1}-01-01')[0] for y in (2022,2023)]
        rows.append(dict(id=cfg.id,config=asdict(cfg),score=min(x['return_pct'] for x in annual),
                         sum_return=sum(x['return_pct'] for x in annual),years=annual))
        if len(rows)%16==0:print('discovery completed',len(rows),'/',len(grid()),flush=True)
    write(out/'discovery.json',rows)
    winners=[]
    for family in FAMILIES:
        candidates=[r for r in rows if r['config']['family']==family]
        best=sorted(candidates,key=lambda r:(-r['score'],-r['sum_return'],r['id']))[0]
        cfg=Config(**best['config'])
        val=run(m,signals(d,prepared,cfg),'2024-01-01','2025-01-01')[0]
        admitted=(all(y['return_pct']>0 and (y['profit_factor'] or 0)>=1.1 and
                      y['adverse_bar_drawdown_pct']>=-10 for y in best['years']+[val]) and
                  sum(y['trades'] for y in best['years'])>=100)
        winners.append(dict(id=best['id'],config=best['config'],training_score=best['score'],
                            training_trades=sum(y['trades'] for y in best['years']),
                            validation=val,admitted=bool(admitted)))
    eligible=[r for r in winners if r['admitted']]
    selected=sorted(eligible or winners,key=lambda r:(-r['validation']['return_pct'],r['id']))[0]
    selection=dict(protocol_commit='2b4be34a82049f804d796c26eb5df68128ce83dc',
                   candidate_count=len(rows),families=winners,selected=selected,
                   admitted_count=len(eligible),fresh_data_used_for_selection=False,
                   post_2024_used_for_selection=False)
    write(out/'selection.json',selection)
    print(json.dumps(selection,indent=2),flush=True)
    return selection


def load_fresh(root,d,f):
    """Checksums + exact August coverage, no zero funding replacement."""
    manifest=json.loads((root/'manifest.json').read_text());bars=[];funds=[];marks=[]
    required={(k,f'2026-08-{i:02d}') for k in ('klines','markPriceKlines') for i in range(1,32)}|{('fundingRate','2026-08')}
    if len(manifest)!=len(required) or {(x['kind'],x['period']) for x in manifest}!=required:
        raise ValueError('Incomplete fresh manifest')
    for row in manifest:
        if row['status']!='verified':raise ValueError('Missing fresh archive')
        p=root/row['filename']
        if hashlib.sha256(p.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('Fresh checksum mismatch')
        with zipfile.ZipFile(p) as z:
            if len(z.namelist())!=1:raise ValueError('Expected one CSV')
            with z.open(z.namelist()[0]) as stream:
                if row['kind']=='fundingRate':funds.append(pd.read_csv(stream));continue
                frame=pd.read_csv(stream,names=COLS,header=None,dtype=str)
                frame=frame[pd.to_numeric(frame.timestamp,errors='coerce').notna()].apply(pd.to_numeric,errors='raise')
                (bars if row['kind']=='klines' else marks).append(frame)
    b=pd.concat(bars).sort_values('timestamp').reset_index(drop=True)
    mark=pd.concat(marks).sort_values('timestamp').reset_index(drop=True)
    start=int(pd.Timestamp('2026-08-01',tz='UTC').timestamp()*1000)
    end=int(pd.Timestamp('2026-09-01',tz='UTC').timestamp()*1000)
    expected=np.arange(start,end,60000)
    for frame in (b,mark):
        if not np.array_equal(frame.timestamp.to_numpy(),expected):raise ValueError('Fresh minute gaps/duplicates')
        if not np.isfinite(frame.to_numpy(float)).all():raise ValueError('Nonfinite data')
        if (frame[['open','high','low','close']]<=0).any().any():raise ValueError('Nonpositive OHLC')
        if ((frame.high<frame[['open','low','close']].max(axis=1))|
            (frame.low>frame[['open','high','close']].min(axis=1))).any():raise ValueError('Invalid OHLC')
    newf=pd.concat(funds).sort_values('calc_time').reset_index(drop=True)
    if not newf.funding_interval_hours.eq(8).all() or ((newf.calc_time%60000)>5000).any():raise ValueError('Funding timing changed')
    newf['minute_time']=newf.calc_time//60000*60000
    if not np.array_equal(newf.minute_time.to_numpy(),np.arange(start,end,28800000)):raise ValueError('Funding gaps')
    if not np.isfinite(newf.to_numpy(float)).all():raise ValueError('Nonfinite funding')
    b.timestamp=b.timestamp.astype('int64');b.index=pd.to_datetime(b.timestamp,unit='ms',utc=True)+pd.Timedelta(minutes=1)
    combined=pd.concat([d,b]);allf=pd.concat([f,newf],ignore_index=True)
    if not np.all(np.diff(combined.timestamp)==60000):raise ValueError('Old/fresh boundary gap')
    audit=dict(fresh_rows=len(b),fresh_funding_rows=len(newf),fresh_mark_rows=len(mark),
        manifest_sha256=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest(),
        same_venue='Binance USD-M BTCUSDT',funding_price='minute-open trade price approximation; marks not substituted silently')
    return combined,allf,audit


def router(m,models,start='2024-01-01',end='2026-09-01'):
    """One combined account, not a sum of independent leveraged strategy curves."""
    n=len(m);out=[np.zeros(n,np.int8),np.full(n,.001),np.zeros(n,np.int8),np.full(n,1.),np.zeros(n),np.zeros(n,bool)]
    choices=[];previous=None
    for month in pd.date_range(start,end,freq='MS',tz='UTC',inclusive='left'):
        cutoff=month-pd.Timedelta(days=2);begin=cutoff-pd.Timedelta(days=180)
        stats=[]
        for name,x in models.items():
            s=run(m,x,str(begin.date()),str(cutoff.date()))[0]
            if s['return_pct']>0 and s['trades']>=20 and (s['profit_factor'] or 0)>=1.1 and s['adverse_bar_drawdown_pct']>=-10:
                stats.append((s['return_pct'],name))
        choice=sorted(stats,key=lambda x:(-x[0],x[1]))[0][1] if stats else None
        stop=month+pd.offsets.MonthBegin(1)
        a=int(np.searchsorted(m[:,0],month.timestamp()*1000));b=int(np.searchsorted(m[:,0],stop.timestamp()*1000))
        if choice:
            x=models[choice]
            for k in range(6):out[k][a:b]=x[k] if np.isscalar(x[k]) else x[k][a:b]
        if a>0 and choice!=previous:out[2][a-1]=2;out[0][a-1]=0
        choices.append(dict(month=str(month.date()),score_start=str(begin.date()),score_end_exclusive=str(cutoff.date()),
                            selected=choice or 'CASH',eligible_scores=stats))
        previous=choice
    return tuple(out),choices


def evaluate(d,f,selection,out,fresh_audit=None):
    prepared=prepare(d,f);m=market(d,f)
    models={r['id']:signals(d,prepared,Config(**r['config'])) for r in selection['families']}
    chosen=selection['selected']['id'];x=models[chosen]
    routed,choices=router(m,models,end='2026-09-01' if fresh_audit else '2026-08-01')
    write(out/'router_choices.json',choices)
    annual=[];runs=[]
    scenarios=[('selected',x,Costs(),Risk()),('router',routed,Costs(),Risk()),
               ('double_slippage',x,Costs(slip=.0002),Risk()),('extra_minute_latency',x,Costs(latency=1),Risk()),
               ('double_commission',x,Costs(fee=.001),Risk()),('zero_cost_ablation',x,Costs(0,0),Risk()),
               ('risk_0.1pct',x,Costs(),Risk(fraction=.001)),('risk_0.5pct',x,Costs(),Risk(fraction=.005))]
    for name,inputs,cost,risk in scenarios:
        for y in range(2022,2027):
            end=f'{y+1}-01-01' if y<2026 else '2026-08-01'
            s,t,daily=run(m,inputs,f'{y}-01-01',end,cost,risk)
            s.update(scenario=name,year=y,full_year=y<2026,independent_annual_capital=True);annual.append(s)
        periods=[('reused','2025-01-01','2026-08-01')]+([('fresh_august','2026-08-01','2026-09-01')] if fresh_audit else [])
        for label,start,end in periods:
            s,t,daily=run(m,inputs,start,end,cost,risk);s.update(scenario=name,period=label);runs.append(s)
            daily.to_csv(out/f'{name}_{label}_daily.csv',header=['equity'])
            t.to_csv(out/f'{name}_{label}_trades.csv.gz',index=False,compression='gzip')
    family_results=[]
    for name,inputs in models.items():
        s=run(m,inputs,'2025-01-01','2026-08-01')[0];s['id']=name;family_results.append(s)
    result=dict(selection=selection,fresh_data=fresh_audit,annual=annual,runs=runs,family_reused=family_results,
        target_achieved=False,live_ready=False,full_year_fresh_validation=False,
        notes=['Historical minute simulation, not tick or live execution.',
               'Old 2025-Jul2026 evaluation was previously seen and is reused.',
               'August is 31 days; annualization prohibited.',
               'Funding uses minute-open trade price; exact settlement mark not modeled.',
               'Intrabar DD uses previous closed-bar peak; exact tick path unknown.',
               'Hyperliquid not backtested; no cross-venue fee substitution.',
               'No margin/liquidation certification; max exposure 2x is a model constraint.'])
    write(out/'results.json',result)
    flat=lambda rows:pd.DataFrame([{k:v for k,v in r.items() if k not in ('costs','risk')} for r in rows])
    flat(annual).to_csv(out/'annual.csv',index=False);flat(runs).to_csv(out/'evaluations.csv',index=False)
    print(json.dumps({'selected':chosen,'admitted':selection['selected']['admitted'],
       'runs':[{k:s[k] for k in ('scenario','period','return_pct','trades','max_drawdown_pct','halted_at')} for s in runs]},indent=2),flush=True)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--phase',choices=['discover','evaluate'],required=True)
    p.add_argument('--selection',type=Path);p.add_argument('--fresh',type=Path)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    d,f,audit=load_data(a.data);write(a.out/'old_data_audit.json',audit)
    if a.phase=='discover':
        if a.fresh or a.selection:raise ValueError('Discovery cannot use fresh data or final selection')
        discovery(d,f,a.out)
    else:
        if a.selection is None:raise ValueError('Frozen selection required')
        selection=json.loads(a.selection.read_text());fresh_audit=None
        if a.fresh:d,f,fresh_audit=load_fresh(a.fresh,d,f)
        evaluate(d,f,selection,a.out,fresh_audit)

if __name__=='__main__':main()
