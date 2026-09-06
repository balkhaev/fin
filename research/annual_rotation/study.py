"""All fixed candidates retained; later winners are not retroactively preselected."""
from dataclasses import asdict,replace
from pathlib import Path
import argparse
import hashlib
import json
import numpy as np
import pandas as pd
from .data import load,SYMBOLS
from .model import grid,PRIMARY,Costs,Config,feature_bank,weights,simulate


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def write(path,value):path.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False))


def uncertainty(curve,initial):
    eq=curve.equity.to_numpy(float)
    if not np.isfinite(eq).all() or (eq<=0).any() or len(eq)<365:return None
    r=np.diff(np.log(np.r_[initial,eq]));rng=np.random.default_rng(20260906);samples=[]
    for _ in range(2000):
        starts=rng.integers(0,len(r),size=(len(r)+29)//30)
        ix=((starts[:,None]+np.arange(30))%len(r)).ravel()[:len(r)]
        samples.append(r[ix].mean())
    bounds=(np.exp(np.quantile(samples,[.025,.975])*365.25)-1)*100
    return dict(bootstrap_95_cagr_pct=[float(x) for x in bounds],block_days=30,draws=2000,
                exploratory_not_selection_corrected=True,not_a_forecast=True)


def study(root,out):
    out=Path(out)
    if out.exists():raise FileExistsError('New output directory required')
    out.mkdir(parents=True)
    frames,audit=load(root);bank=feature_bank(frames);targets={c.id:weights(bank,c) for c in grid()}
    write(out/'data_audit.json',audit);print('DATA_AUDIT',json.dumps(audit),flush=True)
    train=[]
    for cfg in grid():
        r,_,_=simulate(frames,targets[cfg.id],cfg,'2021-01-01','2024-01-01')
        r.update(config=asdict(cfg),id=cfg.id)
        r['admitted_for_validation']=bool(r['accounting_complete'] and (r['cagr_pct'] or -1)>0 and r['max_close_drawdown_pct']>=-50 and r['rebalance_days']>=12)
        train.append(r)
    eligible=[r for r in train if r['admitted_for_validation']]
    eligible.sort(key=lambda r:(-r['cagr_pct']/max(10,abs(r['max_close_drawdown_pct'])),r['id']))
    selected=eligible[0]['id'] if eligible else None
    selection=dict(primary=PRIMARY.id,challenger=selected,eligible_count=len(eligible),
        selected_with_data_before='2024-01-01',later_used_for_selection=False,history_reused=True)
    write(out/'selection.json',selection);write(out/'training.json',train)
    print('SELECTION_LOCK',json.dumps(selection),'sha256',digest(selection),flush=True)
    configs={c.id:c for c in grid()};n=len(frames[SYMBOLS[0]])
    # A BTC long/cash control uses the same primary eligibility and fixed one-asset slot.
    btc_scores={k:v.copy() for k,v in bank.items()}
    for v in btc_scores.values():v[:,1:]=np.nan
    btc_cfg=Config('risk_adjusted',63,1,7)
    controls={'btc_trend_control':(weights(btc_scores,btc_cfg),btc_cfg),
              'btc_hold_control':(np.eye(1,len(SYMBOLS),0).repeat(n,axis=0),btc_cfg),
              'cash_control':(np.zeros((n,len(SYMBOLS))),btc_cfg)}
    rows=[];curves={};full500=[];all_ledgers=[]
    periods=(('validation','2024-01-01','2025-01-01'),('later','2025-01-01','2026-09-01'),('full','2021-01-01','2026-09-01'))
    for name in list(configs)+list(controls):
        t,c=(targets[name],configs[name]) if name in configs else controls[name]
        for label,start,end in periods:
            r,fills,curve=simulate(frames,t,c,start,end)
            r.update(id=name,period=label,scenario='base');rows.append(r)
            if label=='full':
                for annual in r['annual']:
                    if annual['full_year'] and annual['return_pct'] is not None and annual['return_pct']>500:
                        full500.append(dict(id=name,**annual,primary=name==PRIMARY.id,training_selected=name==selected,ex_post_table=True))
            if label=='later' or name in (PRIMARY.id,selected,'btc_hold_control','btc_trend_control'):
                prefix=f'{name}_{label}'
                fills.to_csv(out/f'{prefix}_fills.csv',index=False)
                curve.to_csv(out/f'{prefix}_equity.csv',index=False)
                all_ledgers.append(dict(key=prefix,fills=fills.to_dict('records')))
            if name in (PRIMARY.id,selected,'btc_hold_control','btc_trend_control'):
                print('FOCUS_RESULT',json.dumps(r),flush=True)
                if label=='later':curves[name]=curve
    stress=[];exclusions=[]
    focus=list(dict.fromkeys([PRIMARY.id]+([selected] if selected else [])))
    for name in focus:
        cfg=configs[name]
        for label,cost in [('double_costs',Costs(fee=.002,slip=.001)),('extra_day_delay',Costs(extra_delay=1)),('quarter_allocation',Costs(allocation=.25))]:
            r,fills,curve=simulate(frames,targets[name],cfg,'2025-01-01','2026-09-01',cost)
            r.update(id=name,period='later',scenario=label);stress.append(r)
            prefix=f'{name}_{label}';fills.to_csv(out/f'{prefix}_fills.csv',index=False);curve.to_csv(out/f'{prefix}_equity.csv',index=False)
            all_ledgers.append(dict(key=prefix,fills=fills.to_dict('records')))
            print('STRESS_RESULT',json.dumps(r),flush=True)
        for asset in SYMBOLS:
            r,_,_=simulate(frames,weights(bank,cfg,asset),cfg,'2025-01-01','2026-09-01')
            r.update(id=name,excluded=asset);exclusions.append(r)
    un={name:uncertainty(curve,Costs().initial) for name,curve in curves.items()}
    source={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}
    result=dict(id='annual-target-rotation-20260906',selection=selection,data=audit,training=train,
        results=rows,stresses=stress,exclusions=exclusions,uncertainty=un,observed_full_year_500=full500,
        source_sha256=source,fill_ledger_sha256=digest(all_ledgers),stable_500_proven=False,live_ready=False,
        limitations=['NEW nine-asset spot cohort; not BTC-only or original BTC Pressure.',
            'All prices previously observable within a repeatedly reused research program; no pristine holdout.',
            'Fixed surviving cohort selected today is not a historical complete or survivor-bias-free universe.',
            'No borrowing or funding cost; actual order-depth/market impact and historic filters remain scenarios.',
            '500% in a single historical calendar year is not500% CAGR or a stable future annual return.',
            'Daily close drawdown differs from conservative simultaneous-low stress, which is not synchronous observed NAV.',
            'The final table of all policies is exploratory; only primary and training challenger were fixed before later returns.',
            'Taxes, custody, counterparty, infrastructure and USDT depeg costs are not modeled.'])
    write(out/'results.json',result)
    fields=('id','period','scenario','return_pct','cagr_pct','max_close_drawdown_pct','worst_rolling_365_pct',
            'order_fills','rebalance_days','fills_per_day','fees','accounting_complete')
    pd.DataFrame([{k:r[k] for k in fields} for r in rows+stress]).to_csv(out/'comparison.csv',index=False)
    annual=[dict(id=r['id'],period=r['period'],**a) for r in rows for a in r['annual']]
    pd.DataFrame(annual).to_csv(out/'annual_returns.csv',index=False)
    pd.DataFrame([{k:r[k] for k in ('id','excluded','return_pct','cagr_pct','max_close_drawdown_pct','order_fills','accounting_complete')} for r in exclusions]).to_csv(out/'exclusions.csv',index=False)
    write(out/'verification_hash.json',dict(result_sha256=digest(result),fill_ledger_sha256=result['fill_ledger_sha256']))
    print('SUMMARY',json.dumps(dict(selection=selection,observed500=full500,uncertainty=un,result_sha256=digest(result))),flush=True)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();study(a.data,a.out)
