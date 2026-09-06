"""Explicit POST-RESULT cadence audit. Never relabel a later winner as primary.

The first experiment's source, data and negative results must match before any
new replay. This module does not modify the model, fees or cash/coin accounting.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
from research.annual_rotation.data import load as load_binance
from research.rotation_venue_transfer.data import load as load_okx
from research.annual_rotation.model import Config,Costs,simulate
from research.asymmetric_pulse.policy import build,NAMES,PRIMARY
from research.asymmetric_pulse.study import digest,write,inspect_ledger,months,admitted

ORIGINAL='48c5932a6b3eff560feed90274e7e90a9188cca9fb71ebebc695026baa97fbf9'


def run(root,out):
    root=Path(root);out=Path(out)
    first=json.loads((root/'report/results.json').read_text())
    if digest(first)!=ORIGINAL:raise ValueError('Initial result snapshot changed')
    here=Path(__file__).parents[1]
    for name,want in first['source_sha256'].items():
        if hashlib.sha256((here/'asymmetric_pulse'/name).read_bytes()).hexdigest()!=want:
            raise ValueError('Original model changed: '+name)
    for name in ('data.py','model.py'):
        if (here/'annual_rotation'/name).read_bytes()!=(root/'source/prior/delivery/research/annual_rotation'/name).read_bytes():
            raise ValueError('Original executor changed')
    if out.exists():raise FileExistsError('Fresh output required')
    b,ba=load_binance(root/'source/prior/rotation-data')
    o,oa=load_okx(root/'source/new-source/okx-data')
    if ba!=first['data']['binance'] or oa!=first['data']['okx']:raise ValueError('Source identity differs')
    bt,_,_=build(b);ot,_,_=build(o)
    for t in (bt,ot):np.testing.assert_array_equal(t[PRIMARY],t['asymmetric_blend_weekly'])
    names=tuple(n for n in NAMES if n!='asymmetric_blend_weekly')
    out.mkdir(parents=True);rows=[];ledgers=[]
    cases=[('development','2021-01-01','2024-01-01',b,bt,Costs()),
           ('validation','2024-01-01','2025-01-01',b,bt,Costs()),
           ('later','2025-01-01','2026-09-01',b,bt,Costs()),
           ('full','2021-01-01','2026-09-01',b,bt,Costs()),
           ('later_double_costs','2025-01-01','2026-09-01',b,bt,Costs(fee=.002,slip=.001)),
           ('okx_later','2025-01-01','2026-09-01',o,ot,Costs())]
    for name in names:
        for cadence in (3,7):
            cfg=Config('raw',21,3,cadence)
            for label,start,end,frames,targets,cost in cases:
                r,f,c=simulate(frames,targets[name],cfg,start,end,cost)
                audit=inspect_ledger(f,r,frames,cost,end)
                row=dict(r,policy=name,cadence=cadence,period=label,
                    post_result_exploratory=True,monthly=months(c,cost.initial),fill_audit=audit)
                rows.append(row)
                key=f'{name}_every{cadence}_{label}'
                f.to_csv(out/f'{key}_fills.csv',index=False);c.to_csv(out/f'{key}_equity.csv',index=False)
                ledgers.append(dict(key=key,fills=f.to_dict('records')))
                # The existing weekly-primary pair is an exact numerical control.
                if name==PRIMARY and cadence==7 and label in ('development','validation','later','full'):
                    collection=first['development'] if label=='development' else first['rows']
                    original=next(x for x in collection if x['policy']=='asymmetric_blend_weekly' and x['period']==label)
                    for k,v in r.items():
                        if v!=original[k]:raise AssertionError('Weekly target control changed: '+k)
    development=[dict(policy=r['policy'],cadence=r['cadence'],eligible=admitted(r),
        cagr_pct=r['cagr_pct'],drawdown_pct=r['max_close_drawdown_pct'],worst365_pct=r['worst_rolling_365_pct'])
        for r in rows if r['period']=='development']
    result=dict(id='asymmetric-pulse-cadence-post-result',original_sha256=ORIGINAL,
        original_primary_failed=True,original_primary_not_replaced=True,
        unchanged_source=True,data_identity_unchanged=True,weekly_controls_exact=True,
        rows=rows,development_gates=development,
        scenarios=len(rows),fill_ledger_sha256=digest(ledgers),
        stable_profit_proven=False,annual500proven=False,live_orders=False,
        limitations=['Frequency test designed after seeing original failures; no independent holdout.',
         'All target/risk parameters fixed;3/7-day cadence is a disclosed post-result intervention.',
         'Any later positive candidate is exploratory, never an ex-ante confirmed strategy.',
         'Native unliquidated residues keep return andCAGR null; diagnostic mark-to-market not a realized return.',
         'Same nine surviving assets, daily execution assumptions and reused history; no live trading.'])
    write(out/'results.json',result)
    fields=('policy','cadence','period','return_pct','diagnostic_return_pct','cagr_pct','max_close_drawdown_pct','worst_rolling_365_pct','order_fills','rebalance_days','closed_asset_positions','fees','accounting_complete','open_assets')
    pd.DataFrame([{k:r[k] for k in fields} for r in rows]).to_csv(out/'comparison.csv',index=False)
    pd.DataFrame([dict(policy=r['policy'],cadence=r['cadence'],period=r['period'],**a) for r in rows for a in r['annual']]).to_csv(out/'annual.csv',index=False)
    verify=dict(result_sha256=digest(result),fill_ledger_sha256=result['fill_ledger_sha256'],reports=len(rows),
        post_result_exploratory=True,original_primary_failed=True,live_orders=False)
    write(out/'verification.json',verify)
    print('COMPARISON\n'+(out/'comparison.csv').read_text(),flush=True)
    print('DEVELOPMENT_GATES',json.dumps(development),flush=True)
    print('VERIFY',json.dumps(verify),flush=True)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--original',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();run(a.original,a.out)
