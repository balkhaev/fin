"""Read-only evidence audit. Reconstruct funding coverage and fills from retained files.

Does not fill missing prices, alter marks or repair the original account engine.
It verifies the DECLARED hourly execution model, not actual venue fills.
"""
from pathlib import Path
import argparse,hashlib,json
import numpy as np
import pandas as pd
from research.relative_futures.data import load, SYMBOLS
from research.opportunity_budget.policy import build as budget_build
from research.opportunity_runner.study import build as runner_build


def audit(root):
    root=Path(root); frames,_=load(root/'reconciled/supplemented/reconciled')
    target_b,_=budget_build(frames); target_r=runner_build(frames)
    target_l={'runner720_1x':target_r['runner720_15']/1.5,'runner720_125x':target_r['runner720_15']*(1.25/1.5)}
    cases=[];checked_fills=checked_funding=checked_entry_groups=0
    for folder,targets in [('opportunity-first',target_b),('runner-first',target_r),('ladder-first',target_l)]:
        reports=json.loads((root/folder/'results.json').read_text())['rows']
        for r in reports:
            key=f"{r['model']}_{r['period']}_{r['start']}"; directory=root/folder
            f=pd.read_csv(directory/(key+'_fills.csv'))
            pay=pd.read_csv(directory/(key+'_funding.csv'))
            episodes=pd.read_csv(directory/(key+'_episodes.csv'))
            f['time']=pd.to_datetime(f.time,utc=True);pay['time']=pd.to_datetime(pay.time,utc=True)
            cost=r['costs']; early_exits=0
            for s in SYMBOLS:
                ff=f[f.symbol==s].copy();d=frames[s]
                if len(ff):
                    obs=d.reindex(pd.DatetimeIndex(ff.time))
                    desired_price=obs.open.to_numpy()*(1+np.sign(ff.quantity_delta.to_numpy())*cost['slip'])
                    np.testing.assert_allclose(ff.price.to_numpy(),desired_price,atol=1e-9,rtol=1e-12)
                    np.testing.assert_allclose(ff.fee.to_numpy(),ff.quantity_delta.abs()*ff.price*cost['fee'],atol=1e-9,rtol=1e-12)
                    np.testing.assert_allclose(ff.quantity_delta/cost['step'],np.round(ff.quantity_delta/cost['step']),atol=1e-7,rtol=0)
                    capacities=d.volume.shift().reindex(ff.time).to_numpy()*cost['participation']
                    assert (ff.quantity_delta.abs().to_numpy()<=capacities+1e-8).all()
                    assert (ff.quantity_delta.abs()*ff.price>=cost['minimum']-1e-8).all()
                # Funding belongs to quantities BEFORE same-timestamp orders.
                transactions=ff.groupby('time').quantity_delta.sum().sort_index()
                cumulative=transactions.cumsum().to_numpy()
                settlement=d[(d.index>=r['start'])&(d.index<r['end_exclusive'])&d.funding_event]
                j=np.searchsorted(transactions.index.asi8,settlement.index.asi8,side='left')-1
                quantity=np.where(j>=0,cumulative[np.maximum(j,0)] if len(cumulative) else 0.,0.)
                held=np.abs(quantity)>1e-8
                expect_time=settlement.index[held]
                actual=pay[pay.symbol==s].sort_values('time')
                assert pd.DatetimeIndex(actual.time).equals(expect_time)
                expected=-quantity[held]*settlement.mark_open.to_numpy()[held]*settlement.funding_rate.to_numpy()[held]
                np.testing.assert_allclose(actual.cashflow.to_numpy(),expected,atol=1e-7,rtol=1e-9)
                np.testing.assert_allclose(actual.quantity.to_numpy(),quantity[held],atol=1e-9,rtol=0)
            previous_balance=cost['initial']
            entries=list(f[f.reason=='entry'].groupby('time',sort=True))
            assert len(entries)==len(episodes)
            for k,(when,block) in enumerate(entries):
                offset=frames[SYMBOLS[0]].index.get_loc(when)-2-cost['delay']
                w=targets[r['model']][offset]
                opening=np.array([frames[s].at[when,'open'] for s in SYMBOLS])
                prices=opening*(1+np.sign(w)*cost['slip'])
                predicted=np.sign(w)*np.floor(np.abs(w)*previous_balance*cost['gross']/prices/cost['step'])*cost['step']
                actual=block.groupby('symbol').quantity_delta.sum().reindex(SYMBOLS,fill_value=0).to_numpy()
                np.testing.assert_allclose(actual,predicted,atol=1e-8,rtol=0)
                previous_balance=float(episodes.iloc[k].end_balance)
            # Descriptive, not causal: exit while requested signs still match entry.
            for ep in episodes.itertuples():
                when=pd.Timestamp(ep.exit_time)
                offset=frames[SYMBOLS[0]].index.get_loc(when)-2-cost['delay']
                intent=np.sign(targets[r['model']][offset]).astype(int).tolist()
                entry_block=f[(f.reason=='entry') & (f.time==pd.Timestamp(ep.entry_time))]
                prior=np.sign(entry_block.groupby('symbol').quantity_delta.sum().reindex(SYMBOLS,fill_value=0).to_numpy()).astype(int).tolist()
                if intent==prior and when < pd.Timestamp(r['end_exclusive'],tz='UTC')-pd.Timedelta(hours=12): early_exits+=1
            checked_fills+=len(f);checked_funding+=len(pay);checked_entry_groups+=len(entries)
            cases.append(dict(model=r['model'],period=r['period'],start=r['start'],fills=len(f),funding=len(pay),entry_groups=len(entries),
                nonterminal_exits_with_unchanged_signs=early_exits))
    # Real-history prefixes at a non-boundary time; no full sample calculations leak.
    cut=frames[SYMBOLS[0]].index.searchsorted(pd.Timestamp('2025-03-17 07:00',tz='UTC'))
    prefix={s:d.iloc[:cut] for s,d in frames.items()}
    bp,_=budget_build(prefix);rp=runner_build(prefix)
    for name in target_b:np.testing.assert_allclose(target_b[name][:cut],bp[name],atol=1e-12,rtol=1e-12)
    for name in target_r:np.testing.assert_array_equal(target_r[name][:cut],rp[name])
    proof=dict(cases=len(cases),fill_rows_checked=checked_fills,funding_rows_checked=checked_funding,
        entry_quantity_groups_checked=checked_entry_groups,real_price_prefixes_checked=13,
        same_timestamp_funding_uses_pre_order_quantity=True,source_prices_not_modified=True,
        fills_match_declared_not_actual_exchange_model=True,rows=cases)
    (root/'INDEPENDENT_EVENT_AUDIT.json').write_text(json.dumps(proof,indent=2))
    print(json.dumps({k:v for k,v in proof.items() if k!='rows'}));return proof

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args();audit(a.root)
