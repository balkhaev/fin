"""Post-result size attribution using existing signals and unchanged native accounting."""
from pathlib import Path
import argparse,hashlib,json
import pandas as pd
from research.opportunity_runner.study import build
from research.opportunity_budget.study import BASE,leverage_audit,annual_checks
from research.relative_futures.data import load
from research.relative_futures.account import simulate,Costs
from research.relative_futures.study import digest,save,qualify,independent_trade_replay
from research.relative_futures_checks.candidates import STARTS,episode_statistics


def study(root,out):
    root=Path(root);out=Path(out)
    if out.exists():raise FileExistsError('Fresh output required')
    base=json.loads((root/'report/results.json').read_text())
    if digest(base)!=BASE:raise ValueError('Base evidence changed')
    for name,want in base['source_sha256'].items():
        if hashlib.sha256((Path(__file__).parents[1]/'relative_futures'/name).read_bytes()).hexdigest()!=want:raise ValueError('Original reference changed')
    runner=Path(__file__).parents[1]/'opportunity_runner/study.py'
    if hashlib.sha256(runner.read_bytes()).hexdigest()!='c7c61ad49c347a5a8500e1310cca7b17be96208c96617beeb1f1b92caba55cd3':raise ValueError('Runner altered')
    frames,source=load(root/'supplemented/reconciled')
    if source!=base['source']:raise ValueError('Source altered')
    raw=build(frames)['runner720_15'];out.mkdir(parents=True);rows=[];ledgers=[]
    def one(name,gross,period,start,end='2026-09-01',fee=.0005,slip=.0001,delay=0,initial=10000.):
        cost=Costs(gross=2,fee=fee,slip=slip,delay=delay,initial=initial)
        target=raw*(gross/1.5)
        r,f,pay,e,c=simulate(frames,target,start,end,cost)
        q=qualify(r,c);lev=leverage_audit(frames,f,c,cost)
        if not lev['verified']:q['qualified_historical_scenario']=False;q['issues'].append('leverage_audit_failed')
        row=dict(r,model=name,period=period,requested_entry_gross=gross,qualification=q,leverage_audit=lev,
            additional_risk=annual_checks(c,initial),episode_statistics=episode_statistics(e,c,r),
            independent_cash=independent_trade_replay(f,pay,r['final_balance'],initial,r['terminal_quantities']))
        key=f'{name}_{period}_{start}'
        for label,frame in [('fills',f),('funding',pay),('episodes',e)]:frame.to_csv(out/f'{key}_{label}.csv',index=False)
        c.to_csv(out/f'{key}_equity.csv.gz',index=False,compression={'method':'gzip','mtime':0})
        rows.append(row);ledgers.append(dict(key=key,fills=f.to_dict('records'),funding=pay.to_dict('records')))
        print(json.dumps({k:row[k] for k in ('model','period','start','return_pct','cagr_pct','max_mark_close_drawdown_pct')}),flush=True)
    for name,gross in [('runner720_1x',1.),('runner720_125x',1.25)]:
        one(name,gross,'full','2021-01-01');one(name,gross,'later','2025-01-01')
        one(name,gross,'validation','2024-01-01','2025-01-01')
        one(name,gross,'full_double_costs','2021-01-01',fee=.001,slip=.0002)
        one(name,gross,'later_double_costs','2025-01-01',fee=.001,slip=.0002)
        one(name,gross,'later_delay2','2025-01-01',delay=2)
        one(name,gross,'later_capital1000','2025-01-01',initial=1000.)
        for a,b in STARTS:one(name,gross,'origin365',a,b)
    assert len(rows)==28
    result=dict(id='runner-size-attribution-20260906',source=source,rows=rows,
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),ledger_sha256=digest(ledgers),
        post_result_sizing_attribution=True,original_primary_still_failed=True,
        reference_account_unpatched=True,local_execution=True,no_real_orders=True,unseen_market=False,live_ready=False,stable500proven=False)
    save(out/'results.json',result)
    keys=('model','period','start','return_pct','cagr_pct','max_mark_close_drawdown_pct','completed_episodes','order_fills','fees','funding_cashflow')
    pd.DataFrame([dict(**{k:r[k] for k in keys},max_gross=r['leverage_audit'].get('max_mark_close_gross'),qualified=r['qualification']['qualified_historical_scenario']) for r in rows]).to_csv(out/'comparison.csv',index=False)
    pd.DataFrame([dict(model=r['model'],period=r['period'],start=r['start'],**y) for r in rows for y in r['annual']]).to_csv(out/'annual.csv',index=False)
    save(out/'verification.json',dict(result_sha256=digest(result),ledger_sha256=result['ledger_sha256'],reports=len(rows)))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();study(a.source,a.out)
