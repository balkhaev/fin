"""Finite historical experiment. This wrapper does NOT repair missing valuations.
Every report from the saved futures reference is retained. Missing curves,
funding or margin evidence prevent a case from being declared qualified.
"""
from pathlib import Path
from dataclasses import asdict
import argparse,hashlib,json,math
import numpy as np
import pandas as pd
from .data import load
from .signals import build,NAMES,PRIMARY,CONTROL
from .account import simulate,Costs


def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2,allow_nan=False))

def qualify(report,curve):
    values=curve.equity.to_numpy(float)
    issues=[]
    if not np.isfinite(values).all():issues.append('nonfinite_equity_path_unrepaired')
    if not report['accounting_complete']:issues.append('incomplete_price_funding_or_terminal_account')
    if not report['margin_scenario_verified']:issues.append('incomplete_or_breached_margin_scenario')
    if not report['annual']:issues.append('annual_account_path_unavailable')
    return dict(qualified_historical_scenario=not issues,issues=issues,
        missing_equity_rows=int((~np.isfinite(values)).sum()),
        historical_exchange_tiers_not_verified=True,settlement_mark_still_approximate=True)

def independent_trade_replay(fills,funding,end_balance,initial,terminal):
    # A flat linear-futures account satisfies this identity without copying the
    # incremental open-to-open variation-margin recurrence.
    legs=float((fills.quantity_delta*fills.price).sum()) if len(fills) else 0.
    fees=float(fills.fee.sum()) if len(fills) else 0.
    transfers=float(funding.cashflow.sum()) if len(funding) else 0.
    if not any(terminal):
        reconstructed=initial-legs-fees+transfers
        if not math.isclose(reconstructed,end_balance,abs_tol=1e-5,rel_tol=1e-10):raise AssertionError('Independent final cash disagreement')
        return dict(checked=True,reconstructed_final_balance=reconstructed)
    return dict(checked=False,reason='terminal_contracts_not_flat')

def study(root,out):
    out=Path(out)
    if out.exists():raise FileExistsError('New output required')
    frames,source=load(root);targets,diagnostics=build(frames);out.mkdir(parents=True)
    diagnostics.to_csv(out/'signal_diagnostics.csv',index=False)
    reports=[];ledgers=[]
    def run(name,label,start,end,cost=Costs()):
        r,f,pay,e,c=simulate(frames,targets[name],start,end,cost)
        q=qualify(r,c)
        r.update(model=name,period=label,qualification=q,
            independent_cash=independent_trade_replay(f,pay,r['final_balance'],cost.initial,r['terminal_quantities']))
        key=f'{name}_{label}_{start}'
        f.to_csv(out/f'{key}_fills.csv',index=False);pay.to_csv(out/f'{key}_funding.csv',index=False)
        e.to_csv(out/f'{key}_episodes.csv',index=False);c.to_csv(out/f'{key}_equity.csv.gz',index=False,compression={'method':'gzip','mtime':0})
        reports.append(r);ledgers.append(dict(key=key,fills=f.to_dict('records'),funding=pay.to_dict('records')))
        fields=('model','period','return_pct','cagr_pct','max_mark_close_drawdown_pct','completed_episodes','fees','funding_cashflow','accounting_complete','qualification')
        print('SCENARIO',json.dumps({k:r[k] for k in fields}),flush=True)
    for name in NAMES:
        for label,start,end in [('full','2021-01-01','2026-09-01'),('validation','2024-01-01','2025-01-01'),('later','2025-01-01','2026-09-01')]:run(name,label,start,end)
    for name in (PRIMARY,CONTROL):
        for label,cost in [('double_costs',Costs(fee=.001,slip=.0002)),('extra_hour',Costs(delay=1)),
            ('capital1000',Costs(initial=1000)),('zero_explicit_costs',Costs(fee=0,slip=0))]:
            for period,start in [('full','2021-01-01'),('later','2025-01-01')]:run(name,period+'_'+label,start,'2026-09-01',cost)
    for label,start in [('full','2021-01-01'),('later','2025-01-01')]:run(PRIMARY,label+'_gross2','2021-01-01' if label=='full' else start,'2026-09-01',Costs(gross=2))
    for name in (PRIMARY,CONTROL):
        for start,end in [('2021-01-01','2022-01-01'),('2022-01-01','2023-01-01'),('2023-01-01','2024-01-01'),('2025-01-01','2026-01-01'),
            ('2022-07-01','2023-07-01'),('2023-07-01','2024-06-30'),('2024-07-01','2025-07-01')]:run(name,'fresh365',start,end)
    assert len(reports)==62
    get=lambda name,period:next(r for r in reports if r['model']==name and r['period']==period)
    pf,pl=get(PRIMARY,'full'),get(PRIMARY,'later');stress=get(PRIMARY,'later_double_costs')
    positive=lambda r:r['qualification']['qualified_historical_scenario'] and r['return_pct'] is not None and r['return_pct']>0
    gates=dict(qualified_positive_full=positive(pf),qualified_positive_later=positive(pl),
        doubled_cost_later_positive=positive(stress),full_drawdown_better_than25=pf['max_mark_close_drawdown_pct']>=-25,
        at_least100_later_episodes=pl['completed_episodes']>=100,
        every_full_calendar_positive=bool(pf['annual']) and all(y['return_pct']>0 for y in pf['annual'] if y['full_year']))
    source_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}
    result=dict(id='relative-futures-20260906',primary=PRIMARY,comparator=CONTROL,source=source,rows=reports,
        primary_admission=gates,primary_admitted=all(gates.values()),models=NAMES,source_sha256=source_hashes,
        ledger_sha256=digest(ledgers),known_inactive_nan_valuation_defect_unpatched=True,
        report_quarantine_is_not_a_valuation_fix=True,no_previous_blocked_code_reimplemented=True,
        stable500proven=False,live_ready=False,real_orders=0,
        limitations=['New bidirectional instruments but already examined economic dates; not pristine OOS.',
            'Fixed BTC/ETH choice is not proof of cointegration or always market-neutral risk.',
            'Hourly fills are scenarios, entries presume both legs available together; real legging risk not validated.',
            'Funding uses realized rates and approximate hour-open marks, not exact settlement valuations.',
            'Simultaneous high/low margin stress is not a synchronized path or historical exchange liquidation tier.',
            'An attempted inactive-leg valuation repair was blocked and not retried. Unpriced paths remain quarantined, not repaired.',
            'Cash PnL, funding and fees are separate; longer-held derivative contracts do not deduct spot notional.',
            'No real margin authority or live exchange trading, taxes/custody/infrastructure/USDT risks omitted.'])
    save(out/'results.json',result)
    keys=('model','period','start','end_exclusive','return_pct','cagr_pct','max_mark_close_drawdown_pct','simultaneous_mark_extrema_stress_pct',
        'completed_episodes','order_fills','active_entry_days','fees','funding_cashflow','gross_price_pnl',
        'accounting_complete','margin_scenario_verified','positive_months','negative_months','zero_months')
    pd.DataFrame([dict(**{k:r[k] for k in keys},qualified=r['qualification']['qualified_historical_scenario'],missing_equity_rows=r['qualification']['missing_equity_rows']) for r in reports]).to_csv(out/'comparison.csv',index=False)
    pd.DataFrame([dict(model=r['model'],period=r['period'],start=r['start'],**y) for r in reports for y in r['annual']]).to_csv(out/'annual.csv',index=False)
    verify=dict(result_sha256=digest(result),ledger_sha256=result['ledger_sha256'],reports=len(reports),
        qualified=sum(r['qualification']['qualified_historical_scenario'] for r in reports),live_orders=0)
    save(out/'verification.json',verify);print('VERIFY',json.dumps(verify),flush=True)
    print('ADMISSION',json.dumps(gates),flush=True)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();study(a.data,a.out)
