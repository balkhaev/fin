"""Post-result diagnostic replay of two existing comparators, not a new model.
Original signals/account code and known unrepaired bug remain untouched.
"""
from pathlib import Path
import argparse,hashlib,json
import numpy as np
import pandas as pd
from research.relative_futures.data import load
from research.relative_futures.signals import build
from research.relative_futures.account import simulate,Costs
from research.relative_futures.study import digest,save,qualify,independent_trade_replay

BASE='157fe890756519f3faba4903a88950d24a890a8cecfa0fad4ae4e849d0fd9645'
MODELS=('pair_momentum720','btc_trend_ensemble')
STARTS=(('2021-01-01','2022-01-01'),('2022-01-01','2023-01-01'),('2023-01-01','2024-01-01'),
        ('2025-01-01','2026-01-01'),('2022-07-01','2023-07-01'),('2023-07-01','2024-06-30'),('2024-07-01','2025-07-01'))


def episode_statistics(episodes,curve,report):
    net=episodes.net.to_numpy(float) if len(episodes) else np.array([])
    wins=net[net>0];losses=net[net<0];run=longest=0
    for value in net:
        run=run+1 if value<0 else 0;longest=max(longest,run)
    held=(curve.btc_quantity.abs()+curve.eth_quantity.abs())>0
    denominator=float(net.sum());top5=float(np.sort(wins)[-5:].sum())
    return dict(completed_episodes=len(net),positive_episodes=int((net>0).sum()),negative_episodes=int((net<0).sum()),
        zero_episodes=int((net==0).sum()),profit_factor=float(wins.sum()/-losses.sum()) if len(losses) else None,
        largest_five_winning_net=top5,largest_five_as_fraction_of_net=top5/denominator if denominator>0 else None,
        worst_episode_net=float(net.min()) if len(net) else None,best_episode_net=float(net.max()) if len(net) else None,
        longest_losing_streak=longest,held_quote_hours=int(held.sum()),flat_quote_hours=int((~held).sum()),
        mean_episode_hours=float((pd.to_datetime(episodes.exit_time)-pd.to_datetime(episodes.entry_time)).dt.total_seconds().mean()/3600) if len(net) else None,
        same_fills_adverse_funding_extra=report['same_fills_adverse_funding_extra'],
        descriptive_not_an_alternative_trading_rule=True)


def study(root,out):
    root=Path(root);out=Path(out)
    base=json.loads((root/'report/results.json').read_text())
    if digest(base)!=BASE:raise ValueError('Base result identity changed')
    for name,want in base['source_sha256'].items():
        actual=hashlib.sha256((Path(__file__).parents[1]/'relative_futures'/name).read_bytes()).hexdigest()
        if actual!=want:raise ValueError('Base model/reference changed: '+name)
    if out.exists():raise FileExistsError('Fresh result directory required')
    frames,audit=load(root/'supplemented/reconciled')
    if audit!=base['source']:raise ValueError('Source snapshot changed')
    targets,_=build(frames);out.mkdir(parents=True);rows=[];ledgers=[];exact=0
    def one(name,label,start,end,cost=Costs()):
        nonlocal exact
        report,f,pay,e,c=simulate(frames,targets[name],start,end,cost)
        if label in ('full','later'):
            original=next(x for x in base['rows'] if x['model']==name and x['period']==label)
            for key,value in report.items():
                if value!=original[key]:raise AssertionError('Original comparator report changed: '+key)
            exact+=1
        row=dict(report,model=name,period=label,qualification=qualify(report,c),
            independent_cash=independent_trade_replay(f,pay,report['final_balance'],cost.initial,report['terminal_quantities']),
            episode_statistics=episode_statistics(e,c,report))
        key=name+'_'+label+'_'+start
        f.to_csv(out/f'{key}_fills.csv',index=False);pay.to_csv(out/f'{key}_funding.csv',index=False)
        e.to_csv(out/f'{key}_episodes.csv',index=False);c.to_csv(out/f'{key}_equity.csv.gz',index=False,compression={'method':'gzip','mtime':0})
        ledgers.append(dict(key=key,fills=f.to_dict('records'),funding=pay.to_dict('records')));rows.append(row)
    for name in MODELS:
        for label,start in (('full','2021-01-01'),('later','2025-01-01')):
            one(name,label,start,'2026-09-01')
            for scenario,cost in (('double_costs',Costs(fee=.001,slip=.0002)),('extra_hour',Costs(delay=1)),('capital1000',Costs(initial=1000))):
                one(name,label+'_'+scenario,start,'2026-09-01',cost)
        for start,end in STARTS:one(name,'fresh365',start,end)
    assert len(rows)==30 and exact==4
    origins={}
    for name in MODELS:
        selected=[r for r in rows if r['model']==name and r['period']=='fresh365']
        eligible=[r for r in selected if r['qualification']['qualified_historical_scenario']]
        origins[name]=dict(total=len(selected),qualified=len(eligible),positive=sum(r['return_pct']>0 for r in eligible),
            negative=sum(r['return_pct']<0 for r in eligible),worst_return_pct=min(r['return_pct'] for r in eligible) if eligible else None)
    result=dict(id='relative-positive-comparator-audit-20260906',base_result_sha256=BASE,rows=rows,origin_summary=origins,
        exact_original_reports=exact,source=audit,original_source_sha256=base['source_sha256'],
        candidate_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),ledger_sha256=digest(ledgers),
        selected_after_base_results=True,original_primary_still_rejected=True,known_account_bug_unpatched=True,
        no_parameter_change_or_new_model=True,new_time_holdout=False,live_ready=False,real_orders=0)
    save(out/'results.json',result)
    keys=('model','period','start','return_pct','cagr_pct','max_mark_close_drawdown_pct','simultaneous_mark_extrema_stress_pct',
        'completed_episodes','order_fills','fees','funding_cashflow','gross_price_pnl','positive_months','negative_months','zero_months')
    pd.DataFrame([dict(**{k:r[k] for k in keys},qualified=r['qualification']['qualified_historical_scenario']) for r in rows]).to_csv(out/'comparison.csv',index=False)
    pd.DataFrame([dict(model=r['model'],period=r['period'],start=r['start'],**a) for r in rows for a in r['annual']]).to_csv(out/'annual.csv',index=False)
    verification=dict(result_sha256=digest(result),ledger_sha256=result['ledger_sha256'],reports=len(rows),exact_base_reports=exact,
        qualified=sum(r['qualification']['qualified_historical_scenario'] for r in rows),post_result_comparators=True)
    save(out/'verification.json',verification)
    print('COMPARISON\n'+(out/'comparison.csv').read_text(),flush=True)
    print('ORIGINS',json.dumps(origins),flush=True)
    for row in rows:
        if row['period'] in ('full','later'):print('EPISODE_STATS',row['model'],row['period'],json.dumps(row['episode_statistics']),flush=True)
    print('VERIFY',json.dumps(verification),flush=True)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();study(a.source,a.out)
