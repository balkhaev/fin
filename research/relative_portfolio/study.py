"""Exploratory composition of already measured futures signals, not a ledger repair.
A shared signed-contract account is actually replayed; standalone profits are
not added. Daily regime values become available only after the UTC day closes.
"""
from pathlib import Path
import argparse,hashlib,json
import numpy as np
import pandas as pd
from research.relative_futures.data import load,SYMBOLS
from research.relative_futures.signals import build as relative_build
from research.relative_futures.account import simulate,Costs
from research.relative_futures.study import digest,save,qualify,independent_trade_replay
from research.relative_futures_checks.candidates import STARTS,BASE,episode_statistics

PRIMARY='switch_half'
CONTROL='btc_bull_half'
NAMES=(CONTROL,'relative_half_always',PRIMARY,'blend_quarters','relative_half_bear_only')


def build(frames):
    original,diagnostics=relative_build(frames)
    hourly=frames['BTCUSDT'].close
    counts=hourly.resample('D').count()
    daily=hourly.resample('D').last().where(counts==24)
    known=daily.notna().rolling(201,min_periods=201).sum().eq(201)
    bull=known&(daily>daily.rolling(200,min_periods=200).mean())&(daily>daily.shift(63))
    # Daily bar of date D is published only at D+1 00:00UTC.
    release=pd.DataFrame({'known':known,'bull':bull},index=daily.index)
    release.index=release.index+pd.Timedelta(days=1)
    available=hourly.index+pd.Timedelta(hours=1)
    states=release.reindex(available,method='ffill').fillna(False)
    known=states.known.to_numpy(bool);bull=states.bull.to_numpy(bool)
    base=np.zeros((len(hourly),2));base[bull,0]=.5
    pair=.5*original['pair_momentum720']
    pair[~known]=0.
    switch=base.copy();switch[known&~bull]=pair[known&~bull]
    bear=np.zeros_like(pair);bear[known&~bull]=pair[known&~bull]
    result={CONTROL:base,'relative_half_always':pair,PRIMARY:switch,
        'blend_quarters':.5*base+.5*pair,'relative_half_bear_only':bear}
    for name,value in result.items():
        if not np.isfinite(value).all() or (np.abs(value).sum(axis=1)>.5+1e-12).any():raise AssertionError('Invalid composition target: '+name)
    trace=pd.DataFrame({'signal_hour':hourly.index.astype(str),'signal_available':available.astype(str),
        'daily_history_complete':known,'btc_daily_bull':bull,'switch_btc':switch[:,0],'switch_eth':switch[:,1]})
    return result,trace


def study(root,out):
    root=Path(root);out=Path(out)
    original=json.loads((root/'report/results.json').read_text())
    if digest(original)!=BASE:raise ValueError('Frozen initial source evidence changed')
    for name,want in original['source_sha256'].items():
        if hashlib.sha256((Path(__file__).parents[1]/'relative_futures'/name).read_bytes()).hexdigest()!=want:raise ValueError('Original model/account altered')
    if out.exists():raise FileExistsError('Fresh output required')
    frames,audit=load(root/'supplemented/reconciled')
    if audit!=original['source']:raise ValueError('Data changed')
    targets,trace=build(frames);out.mkdir(parents=True);trace.to_csv(out/'completed_day_regime.csv',index=False)
    rows=[];ledgers=[]
    def one(name,label,start,end,cost=Costs()):
        report,f,pay,e,c=simulate(frames,targets[name],start,end,cost)
        row=dict(report,model=name,period=label,qualification=qualify(report,c),
            independent_cash=independent_trade_replay(f,pay,report['final_balance'],cost.initial,report['terminal_quantities']),
            episode_statistics=episode_statistics(e,c,report))
        key=name+'_'+label+'_'+start
        f.to_csv(out/f'{key}_fills.csv',index=False);pay.to_csv(out/f'{key}_funding.csv',index=False)
        e.to_csv(out/f'{key}_episodes.csv',index=False);c.to_csv(out/f'{key}_equity.csv.gz',index=False,compression={'method':'gzip','mtime':0})
        ledgers.append(dict(key=key,fills=f.to_dict('records'),funding=pay.to_dict('records')));rows.append(row)
    for name in NAMES:
        for label,start,end in [('full','2021-01-01','2026-09-01'),('validation','2024-01-01','2025-01-01'),('later','2025-01-01','2026-09-01')]:one(name,label,start,end)
    for name in (PRIMARY,CONTROL):
        for label,cost in [('double_costs',Costs(fee=.001,slip=.0002)),('extra_hour',Costs(delay=1)),('capital1000',Costs(initial=1000))]:
            for period,start in [('full','2021-01-01'),('later','2025-01-01')]:one(name,period+'_'+label,start,'2026-09-01',cost)
        for start,end in STARTS:one(name,'fresh365',start,end)
    assert len(rows)==41
    get=lambda name,label:next(r for r in rows if r['model']==name and r['period']==label)
    positive=lambda r:r['qualification']['qualified_historical_scenario'] and r['return_pct']>0
    origins={}
    for name in (PRIMARY,CONTROL):
        selected=[r for r in rows if r['model']==name and r['period']=='fresh365'];complete=[r for r in selected if r['qualification']['qualified_historical_scenario']]
        origins[name]=dict(total=len(selected),qualified=len(complete),positive=sum(r['return_pct']>0 for r in complete),negative=sum(r['return_pct']<0 for r in complete),
            worst_return_pct=min(r['return_pct'] for r in complete) if complete else None)
    gates={}
    for label in ('full','later'):
        p,c=get(PRIMARY,label),get(CONTROL,label)
        gates[label+'_net_outperformance']=positive(p) and p['return_pct']>c['return_pct']
        gates[label+'_drawdown_within_two_points']=p['max_mark_close_drawdown_pct']>=c['max_mark_close_drawdown_pct']-2
    gates['later_double_cost_positive']=positive(get(PRIMARY,'later_double_costs'))
    gates['no_extra_negative_starts']=origins[PRIMARY]['qualified']==origins[CONTROL]['qualified']==7 and origins[PRIMARY]['negative']<=origins[CONTROL]['negative']
    result=dict(id='relative-regime-composition-20260906',base_result_sha256=BASE,primary=PRIMARY,control=CONTROL,rows=rows,
        origin_summary=origins,gates=gates,exploratory_joint_conditions_passed=all(gates.values()),
        source=audit,original_source_sha256=original['source_sha256'],script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        ledger_sha256=digest(ledgers),post_result_hypothesis=True,old_pair_reversion_primary_still_failed=True,
        account_bug_unpatched=True,account_rules_unchanged=True,new_time_holdout=False,live_ready=False,stable500proven=False,real_orders=0,
        limitations=['Daily regime uses completed bars only, but economic dates and model choices reflect prior research.',
            'Targetgross0.5 is an entry budget, not a continuous leverage cap or a loss bound.',
            'LongBTC perpetual core is not the same instrument/cost model as earlier nine-asset spotguard.',
            'Regimecomposition is actually replayed in one account; standalone returns cannot be added.',
            'Unknown actual exchange fill sequencing, margin tiers and settlement marks remain scenario assumptions.',
            'Known original reference NaN bug remains unfixed; complete source avoids its manifestation here only.'])
    save(out/'results.json',result)
    keys=('model','period','start','return_pct','cagr_pct','max_mark_close_drawdown_pct','simultaneous_mark_extrema_stress_pct',
        'completed_episodes','order_fills','fees','funding_cashflow','gross_price_pnl','positive_months','negative_months','zero_months')
    pd.DataFrame([dict(**{k:r[k] for k in keys},qualified=r['qualification']['qualified_historical_scenario']) for r in rows]).to_csv(out/'comparison.csv',index=False)
    pd.DataFrame([dict(model=r['model'],period=r['period'],start=r['start'],**a) for r in rows for a in r['annual']]).to_csv(out/'annual.csv',index=False)
    verify=dict(result_sha256=digest(result),ledger_sha256=result['ledger_sha256'],reports=len(rows),qualified=sum(r['qualification']['qualified_historical_scenario'] for r in rows))
    save(out/'verification.json',verify)
    print('COMPARISON\n'+(out/'comparison.csv').read_text(),flush=True)
    print('GATES',json.dumps(gates),flush=True);print('ORIGINS',json.dumps(origins),flush=True)
    for name in (PRIMARY,CONTROL):print('ANNUAL',name,json.dumps(get(name,'full')['annual']),flush=True)
    print('VERIFY',json.dumps(verify),flush=True)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();study(a.source,a.out)
