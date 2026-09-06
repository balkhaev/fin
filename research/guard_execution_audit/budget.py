"""Post-result sizing diagnostic, using only existing signal and simulator APIs.

NOT a funding policy, new ledger or multiplication of an existing equity curve.
Every native account is re-executed with its own quantities, fees and cash.
"""
from pathlib import Path
import argparse
import hashlib
import json
import pandas as pd
from research.annual_rotation.data import load as spot_load
from research.annual_rotation.model import simulate,Costs,Config
from research.rotation_stability.policy import build
from research.rotation_venue_transfer.data import load as okx_load
from .study import digest,write,audit,monthly,PINS

INITIAL='f2e5335220f26a56ef63c496c1e96683bcd341225ba5e6d3883e02704083e8e9'
NAME='ensemble_market_gate'
ALLOCATIONS=(.25,.5,.75)
CADENCES=(1,3,7,14)
FOCUS=(.25,7)


def study(original,out):
    original=Path(original);out=Path(out)
    if out.exists():raise FileExistsError('New evidence directory required')
    old=json.loads((original/'report/results.json').read_text())
    if digest(old)!=INITIAL:raise ValueError('Original cadence evidence changed')
    for path,want in PINS.items():
        if hashlib.sha256((Path(__file__).parents[1]/path).read_bytes()).hexdigest()!=want:
            raise ValueError('Original signal or executor changed')
    source=original/'source';b,ba=spot_load(source/'prior/rotation-data');o,oa=okx_load(source/'new-source/okx-data')
    if ba!=old['source']['binance'] or oa!=old['source']['okx']:raise ValueError('Market snapshot changed')
    bt,_=build(b);ot,_=build(o);out.mkdir(parents=True);rows=[];ledgers=[]
    def run(allocation,cadence,period,start,end,frames,targets,fee=.001,slip=.0005,delay=0):
        cost=Costs(allocation=allocation,fee=fee,slip=slip,extra_delay=delay)
        report,fills,curve=simulate(frames,targets[NAME],Config('raw',126,3,cadence),start,end,cost)
        reconciliation=audit(fills,report,frames,cost,end);month=monthly(curve,cost.initial)
        key=f'budget{allocation}_every{cadence}_{period}_{start}'
        fills.to_csv(out/f'{key}_fills.csv',index=False);curve.to_csv(out/f'{key}_equity.csv',index=False)
        rows.append(dict(report,allocation=allocation,cadence_days=cadence,period=period,
            venue='binance' if frames is b else 'okx',months=month,fill_audit=reconciliation,
            positive_months=sum(x['return_pct']>1e-10 for x in month),
            zero_months=sum(abs(x['return_pct'])<=1e-10 for x in month),
            negative_months=sum(x['return_pct']<-1e-10 for x in month)))
        ledgers.append(dict(key=key,fills=fills.to_dict('records')))
    for allocation in ALLOCATIONS:
        for cadence in CADENCES:
            for period,start,end in [('validation','2024-01-01','2025-01-01'),('later','2025-01-01','2026-09-01'),('full','2021-01-01','2026-09-01')]:
                run(allocation,cadence,period,start,end,b,bt)
            run(allocation,cadence,'later_double_costs','2025-01-01','2026-09-01',b,bt,fee=.002,slip=.001)
            run(allocation,cadence,'later_delay','2025-01-01','2026-09-01',b,bt,delay=1)
            run(allocation,cadence,'later_okx','2025-01-01','2026-09-01',o,ot)
    for date in pd.date_range('2021-01-01','2025-07-01',freq='QS',tz='UTC'):
        run(*FOCUS,'start365',str(date.date()),str((date+pd.Timedelta(days=365)).date()),b,bt)
    focus={r['period']:r for r in rows if (r['allocation'],r['cadence_days'])==FOCUS and r['period']!='start365'}
    controls={r['period']:r for r in old['rows'] if r['policy']=='guarded_ensemble20' and r['cadence_days']==7 and r['period']!='start365'}
    starts=[r for r in rows if r['period']=='start365'];oldstarts=[r for r in old['rows'] if r['policy']=='guarded_ensemble20' and r['cadence_days']==7 and r['period']=='start365']
    complete=[r for r in starts if r['accounting_complete']];oldcomplete=[r for r in oldstarts if r['accounting_complete']]
    stats=dict(total=len(starts),complete=len(complete),positive=sum(r['return_pct']>0 for r in complete),
        zero=sum(r['return_pct']==0 for r in complete),negative=sum(r['return_pct']<0 for r in complete),
        worst=min(r['return_pct'] for r in complete),control_worst=min(r['return_pct'] for r in oldcomplete))
    full=focus['full'];later=focus['later'];base=controls['full']
    gates=dict(complete=full['accounting_complete'] and later['accounting_complete'],
        improved_full_CAGR=full['cagr_pct'] is not None and full['cagr_pct']>base['cagr_pct'],
        no_worse_full_drawdown=full['max_close_drawdown_pct']>=base['max_close_drawdown_pct'],
        later_positive=later['return_pct'] is not None and later['return_pct']>0,
        all_later_stresses_positive=all(focus[k]['accounting_complete'] and focus[k]['return_pct']>0 for k in ('later_double_costs','later_delay','later_okx')),
        all_calendar_years_nonnegative=all(y['return_pct'] is not None and y['return_pct']>=0 for y in full['annual'] if y['full_year']),
        rolling_starts_not_worse=stats['complete']==19 and stats['positive']>=15 and stats['worst']>=stats['control_worst'])
    result=dict(id='guard-fixed-budget-followup-20260906',source_result_sha256=INITIAL,focus=FOCUS,
        rows=rows,controls=controls,start_sensitivity=stats,gates=gates,joint_improvement=all(gates.values()),
        post_result_exploratory=True,initial_daily_primary_not_replaced=True,original_signal_and_execution_unchanged=True,
        funding_policy_implemented=False,source_data_identical=True,ledger_sha256=digest(ledgers),
        live_ready=False,stable_profit_proven=False,stable500proven=False,real_orders=0,
        limits=['Constant risk-budget test designed after first results; not a pristine holdout.',
          'Changing Costs.allocation changes actual native fills, not an arithmetic equity multiplier.',
          'Twelve cells are not independent economic tests; each test uses the same known historical markets.',
          'Idle cash, drawdowns, negative years and residual-null outcomes remain explicit.',
          'Costs, capacity and lot filters remain scenarios; intraday, taxes and live execution not proved.'])
    write(out/'results.json',result)
    fields=('allocation','cadence_days','period','start','venue','return_pct','cagr_pct','max_close_drawdown_pct',
        'worst_rolling_365_pct','order_fills','rebalance_days','closed_asset_positions','fees','positive_months','zero_months','negative_months','accounting_complete')
    pd.DataFrame([{k:r[k] for k in fields} for r in rows]).to_csv(out/'comparison.csv',index=False)
    pd.DataFrame([dict(allocation=r['allocation'],cadence_days=r['cadence_days'],period=r['period'],start=r['start'],**y) for r in rows for y in r['annual']]).to_csv(out/'annual.csv',index=False)
    verified=dict(result_sha256=digest(result),ledger_sha256=result['ledger_sha256'],reports=len(rows),ledger_count=len(ledgers),funding_policy_implemented=False,real_orders=0)
    write(out/'verification.json',verified)
    print('COMPARISON\n'+pd.DataFrame([{k:r[k] for k in fields} for r in rows if r['period']!='start365']).to_csv(index=False),flush=True)
    print('FOCUS_ANNUAL',json.dumps(full['annual']),flush=True)
    print('ORIGIN_TEST',json.dumps(stats),flush=True);print('GATES',json.dumps(gates),flush=True)
    print('VERIFY',json.dumps(verified),flush=True)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--original',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();study(a.original,a.out)
