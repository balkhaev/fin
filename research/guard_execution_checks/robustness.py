"""Post-result checks of unchanged old API, not a new funding/target/ledger policy."""
from pathlib import Path
import argparse
import hashlib
import json
import pandas as pd
from research.annual_rotation.data import load as spot_load,SYMBOLS
from research.annual_rotation.model import simulate,Costs,Config
from research.rotation_stability.policy import build
from research.rotation_venue_transfer.data import load as okx_load
from research.guard_execution_audit.study import digest,write,audit,monthly,PINS

BUDGET_SHA='c0780fb5468bb57cd34b94ea251c79d58cbee96ef491370de7d5f9ec92d5fd90'
NAME='ensemble_market_gate'


def study(original,budget,out):
    original=Path(original);budget=Path(budget);out=Path(out)
    previous=json.loads((budget/'report/results.json').read_text())
    if digest(previous)!=BUDGET_SHA:raise ValueError('Budget evidence identity changed')
    first=json.loads((original/'report/results.json').read_text())
    if digest(first)!=previous['source_result_sha256']:raise ValueError('Original evidence changed')
    for path,want in PINS.items():
        if hashlib.sha256((Path(__file__).parents[1]/path).read_bytes()).hexdigest()!=want:raise ValueError('Source changed')
    if out.exists():raise FileExistsError('Choose new output directory')
    b,ba=spot_load(original/'source/prior/rotation-data');o,oa=okx_load(original/'source/new-source/okx-data')
    if ba!=first['source']['binance'] or oa!=first['source']['okx']:raise ValueError('Data changed')
    bt,_=build(b);ot,_=build(o);out.mkdir(parents=True);rows=[];ledgers=[]
    def one(name,start,end,frames,target,cost=Costs(allocation=.25),excluded=None):
        report,fills,curve=simulate(frames,target,Config('raw',126,3,3),start,end,cost)
        if name in ('full','later') and excluded is None:
            old=next(r for r in previous['rows'] if r['allocation']==.25 and r['cadence_days']==3 and r['period']==name)
            for k,v in report.items():
                if v!=old[k]:raise AssertionError('Original candidate differs: '+k)
        reconciliation=audit(fills,report,frames,cost,end)
        m=monthly(curve,cost.initial);key=name+'_'+start+('_without_'+excluded if excluded else '')
        fills.to_csv(out/f'{key}_fills.csv',index=False);curve.to_csv(out/f'{key}_equity.csv',index=False)
        row=dict(report,case=name,excluded=excluded,months=m,fill_audit=reconciliation,
            venue='binance' if frames is b else 'okx',positive_months=sum(x['return_pct']>1e-10 for x in m),
            zero_months=sum(abs(x['return_pct'])<=1e-10 for x in m),negative_months=sum(x['return_pct']<-1e-10 for x in m))
        rows.append(row);ledgers.append(dict(key=key,fills=fills.to_dict('records')))
    for label,start in [('full','2021-01-01'),('later','2025-01-01')]:
        one(label,start,'2026-09-01',b,bt[NAME])
        for scenario,cost in [('double_cost',Costs(allocation=.25,fee=.002,slip=.001)),
            ('delay',Costs(allocation=.25,extra_delay=1)),('capital1000',Costs(allocation=.25,initial=1000))]:
            one(label+'_'+scenario,start,'2026-09-01',b,bt[NAME],cost)
    for symbol in SYMBOLS:
        excluded_targets,_=build(b,exclude=symbol)
        for label,start in [('full_exclusion','2021-01-01'),('later_exclusion','2025-01-01')]:
            one(label,start,'2026-09-01',b,excluded_targets[NAME],excluded=symbol)
    for date in pd.date_range('2021-01-01','2025-07-01',freq='QS',tz='UTC'):
        one('origin365',str(date.date()),str((date+pd.Timedelta(days=365)).date()),b,bt[NAME])
    one('okx_later','2025-01-01','2026-09-01',o,ot[NAME])
    one('okx_full_common','2024-01-01','2026-09-01',o,ot[NAME])
    origins=[r for r in rows if r['case']=='origin365'];complete=[r for r in origins if r['accounting_complete']]
    stats=dict(count=len(origins),complete=len(complete),positive=sum(r['return_pct']>0 for r in complete),
        zero=sum(r['return_pct']==0 for r in complete),negative=sum(r['return_pct']<0 for r in complete),
        worst_return_pct=min(r['return_pct'] for r in complete),median_return_pct=float(pd.Series([r['return_pct'] for r in complete]).median()))
    result=dict(id='guard25-three-day-candidate-audit-20260906',candidate_selected_after_results=True,
        original_budget_sha256=BUDGET_SHA,candidate=dict(existing_target=NAME,allocation=.25,cadence=3),
        rows=rows,origin_sensitivity=stats,ledger_sha256=digest(ledgers),
        exact_original_two_base_reports=True,old_engine_and_target_unchanged=True,
        funding_policy_implemented=False,new_holdout=False,live_ready=False,real_orders=0,
        limits=['An ex-post candidate of earlier cadence/budget comparisons, not preselected primary.',
            'Same named survivor cohort, market history and cost assumptions; not independent economic evidence.',
            'ExcludingBTC removes its position and market-gate condition; not pureposition attribution.',
            'No portfolio compounding of independently funded accounts, no dust writeoff, no borrowed cash.',
            'Daily-close/low stress are not proven live intraday risk or execution.',
            'Full-period advantage does not erase lower later returns versus old guard.'])
    write(out/'results.json',result)
    fields=('case','excluded','start','venue','return_pct','cagr_pct','max_close_drawdown_pct',
        'worst_rolling_365_pct','order_fills','rebalance_days','closed_asset_positions','fees',
        'positive_months','zero_months','negative_months','accounting_complete')
    pd.DataFrame([{k:r[k] for k in fields} for r in rows]).to_csv(out/'comparison.csv',index=False)
    pd.DataFrame([dict(case=r['case'],excluded=r['excluded'],start=r['start'],**y) for r in rows for y in r['annual']]).to_csv(out/'annual.csv',index=False)
    verify=dict(result_sha256=digest(result),ledger_sha256=result['ledger_sha256'],reports=len(rows),ledger_count=len(ledgers))
    write(out/'verification.json',verify)
    print('COMPARISON\n'+(out/'comparison.csv').read_text(),flush=True)
    print('ORIGINS',json.dumps(stats),flush=True)
    print('BASE_FULL_ANNUAL',json.dumps(next(r for r in rows if r['case']=='full')['annual']),flush=True)
    print('VERIFY',json.dumps(verify),flush=True)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('original','budget','out'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();study(a.original,a.budget,a.out)
