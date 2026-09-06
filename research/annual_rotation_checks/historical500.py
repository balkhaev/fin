"""Post-result diagnostics, not a preselected winner or independent holdout.

Uses the unchanged, hash-pinned annual_rotation model. No trading API exists here.
"""
import argparse
from pathlib import Path
import hashlib
import json
import math
import numpy as np
import pandas as pd
from research.annual_rotation.data import load, SYMBOLS
from research.annual_rotation.model import Config, Costs, feature_bank, weights, simulate
from research.annual_rotation.study import digest

ORIGINAL_RESULT='55cea963fa8fc5e101e1144692340e7032b83cd8cd3d01deaa110b2a2409c161'
POLICY=Config('raw',126,3,7)


def run(data_root,original_root,out):
    original=json.loads((original_root/'results.json').read_text())
    if digest(original)!=ORIGINAL_RESULT:raise ValueError('Original result identity mismatch')
    code=Path(__file__).parents[1]/'annual_rotation'
    for name,want in original['source_sha256'].items():
        if hashlib.sha256((code/name).read_bytes()).hexdigest()!=want:raise ValueError('Original source changed: '+name)
    if out.exists():raise FileExistsError('Choose a new evidence directory')
    frames,audit=load(data_root)
    if audit!=original['data']:raise ValueError('Original market data identity mismatch')
    bank=feature_bank(frames);out.mkdir(parents=True)
    cases=[
        ('calendar2021_base','2021-01-01','2022-01-01',Costs(),None),
        ('calendar2021_double_costs','2021-01-01','2022-01-01',Costs(fee=.002,slip=.001),None),
        ('calendar2021_extra_day_delay','2021-01-01','2022-01-01',Costs(extra_delay=1),None),
        ('calendar2021_capital1000','2021-01-01','2022-01-01',Costs(initial=1000),None),
    ]
    cases += [(f'calendar2021_without_{s}','2021-01-01','2022-01-01',Costs(),s) for s in SYMBOLS]
    cases += [('full_double_costs','2021-01-01','2026-09-01',Costs(fee=.002,slip=.001),None),
              ('later_double_costs','2025-01-01','2026-09-01',Costs(fee=.002,slip=.001),None)]
    rows=[];ledgers=[]
    for name,start,end,cost,exclude in cases:
        report,fills,curve=simulate(frames,weights(bank,POLICY,exclude),POLICY,start,end,cost)
        cash=cost.initial;lots={s:0 for s in SYMBOLS}
        for f in fills.itertuples():
            sign=1 if f.side=='sell' else -1
            cash+=sign*f.quantity*f.price-f.fee;lots[f.symbol]-=sign*round(f.quantity/cost.step)
            if cash<-1e-6 or min(lots.values())<0:raise AssertionError('Independent cash/coin audit failed')
        end_day=pd.Timestamp(end,tz='UTC')-pd.Timedelta(days=1)
        remaining=sum(lots[s]*cost.step*frames[s].loc[end_day,'close']*(1-cost.slip)*(1-cost.fee) for s in SYMBOLS if lots[s])
        if not math.isclose(cash+remaining,report['final_equity'],abs_tol=1e-5,rel_tol=1e-10):raise AssertionError('Final marked account does not reconcile')
        report.update(case=name,excluded=exclude,post_result_diagnostic=True,independent_fill_audit=True)
        rows.append(report);ledgers.append({'case':name,'fills':fills.to_dict('records')})
        fills.to_csv(out/f'{name}_fills.csv',index=False);curve.to_csv(out/f'{name}_equity.csv',index=False)
    result=dict(id='historical500-followup-20260906',policy=POLICY.id,
        original_ci_result_sha256=digest(original),post_result_diagnostic=True,
        selection_source='highest observed2021 calendar result in original36-policy table, not preselected',
        cases=rows,fill_ledger_sha256=digest(ledgers),stable_500_proven=False,live_orders=False)
    (out/'results.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    fields=('case','excluded','initial','final_equity','return_pct','cagr_pct','max_close_drawdown_pct','order_fills','fees','liquidity_rejections','accounting_complete')
    pd.DataFrame([{k:r[k] for k in fields} for r in rows]).to_csv(out/'comparison.csv',index=False)
    print('POST_RESULT_AUDIT_SHA256',digest(result))
    print((out/'comparison.csv').read_text())
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--original',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    a=parser.parse_args();run(a.data,a.original,a.out)
