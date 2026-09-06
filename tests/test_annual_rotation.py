"""Synthetic engineering fixtures; no fixture is market-profit evidence."""
from dataclasses import replace
import numpy as np
import pandas as pd
import pytest
from research.annual_rotation.data import SYMBOLS,normalize_time,months,days
from research.annual_rotation.model import Config,PRIMARY,Costs,grid,feature_bank,weights,simulate


def fake(n=300):
    idx=pd.date_range('2020-01-01',periods=n,freq='D',tz='UTC');out={}
    for k,s in enumerate(SYMBOLS):
        p=100+np.arange(n)*.1+np.sin(np.arange(n)/7+k)
        out[s]=pd.DataFrame(dict(open=p,high=p+1,low=p-1,close=p,volume=np.full(n,1e8),quote_volume=np.full(n,1e10)),index=idx)
    return out


def flat(n=10):
    f=fake(n)
    for d in f.values():
        d['open']=d['close']=100.;d['high']=101.;d['low']=99.
    return f


def replay(f,target=None,cost=Costs(),cfg=Config('raw',21,1,1)):
    idx=f[SYMBOLS[0]].index
    if target is None:
        target=np.zeros((len(idx),len(SYMBOLS)));target[:,0]=1
    return simulate(f,target,cfg,str(idx[0].date()),str((idx[-1]+pd.Timedelta(days=1)).date()),cost)


def test_protocol_grid_and_primary():
    assert len(grid())==36 and len({c.id for c in grid()})==36 and PRIMARY in grid()
    assert len(months())==80 and len(days('2026-08'))==31


def test_mixed_units_not_accepted():
    assert normalize_time([1735689600000000])[0]==1735689600000
    with pytest.raises(ValueError):normalize_time([1735689600000,1735689600000000])


def test_features_are_prefix_causal():
    f=fake();short={s:d.iloc[:240] for s,d in f.items()}
    a,b=feature_bank(f),feature_bank(short)
    for key in a:np.testing.assert_allclose(a[key][:240],b[key],equal_nan=True)
    for cfg in grid():np.testing.assert_array_equal(weights(a,cfg)[:240],weights(b,cfg))


def test_gap_requires_whole_lookback_recovery():
    f=fake();f['BTCUSDT'].iloc[150]=np.nan
    b=feature_bank(f)
    assert not np.isfinite(b['raw',126][150:277,0]).any()


def test_weights_never_exceed_funded_capital():
    b=feature_bank(fake())
    for c in grid():
        w=weights(b,c)
        assert (w>=0).all() and (w.sum(axis=1)<=1+1e-10).all()
        assert ((w>0).sum(axis=1)<=c.top).all()


def test_excluded_coin_never_receives_weight():
    b=feature_bank(fake());w=weights(b,PRIMARY,'DOGEUSDT')
    assert not w[:,-1].any()
    with pytest.raises(ValueError):weights(b,PRIMARY,'UNKNOWN')


def test_empty_slots_stay_cash_not_reallocated():
    bank={('raw',21):np.full((5,len(SYMBOLS)),np.nan)};bank['raw',21][:,0]=1
    w=weights(bank,Config('raw',21,3,1));np.testing.assert_allclose(w.sum(axis=1),1/3)


def test_closed_signal_waits_full_day_before_execution():
    f=flat();w=np.zeros((10,len(SYMBOLS)));w[2:,0]=1
    r,fills,_=replay(f,w)
    assert pd.Timestamp(fills.time.iloc[0])==pd.Timestamp('2020-01-05',tz='UTC')


def test_added_delay_cannot_fill_earlier():
    f=flat();_,a,_=replay(f);_,b,_=replay(f,cost=Costs(extra_delay=1))
    assert pd.Timestamp(b.time.iloc[0])==pd.Timestamp(a.time.iloc[0])+pd.Timedelta(days=1)


def test_one_cash_account_not_sum_of_strategies():
    f=flat();w=np.zeros((10,len(SYMBOLS)));w[:4,:3]=1/3;w[4:,3:6]=1/3
    r,ledger,_=replay(f,w);cash=10000.;q={s:0. for s in SYMBOLS}
    for row in ledger.itertuples():
        sign=1 if row.side=='sell' else -1
        cash+=sign*row.notional-row.fee;q[row.symbol]-=sign*row.quantity
        assert cash>=-1e-6 and min(q.values())>=-1e-6
        assert cash==pytest.approx(row.cash_after,abs=1e-6)
    assert cash==pytest.approx(r['final_equity']) and r['ledger_reconciled']


def test_constant_prices_lose_exactly_spread_and_fees():
    r,l,_=replay(flat())
    net=sum((1 if x.side=='sell' else -1)*x.notional-x.fee for x in l.itertuples())
    assert r['return_pct']<0 and r['final_equity']==pytest.approx(10000+net)
    assert r['fees']==pytest.approx(l.fee.sum())


def test_known_last_day_price_double_matches_coins_not_return_addition():
    f=flat();d=f['BTCUSDT'];d.iloc[-1,d.columns.get_indexer(['open','high','low','close'])]=[200,201,199,200]
    r,_,_=replay(f,cost=Costs(fee=0,slip=0))
    assert r['return_pct']==pytest.approx(99.6)
    assert r['final_equity']==pytest.approx(19960.)


def test_no_signals_preserve_cash():
    r,l,_=replay(flat(),np.zeros((10,len(SYMBOLS))))
    assert r['return_pct']==0 and len(l)==0 and r['accounting_complete']


def test_held_missing_price_blocks_valid_return():
    f=flat();f['BTCUSDT'].iloc[5]=np.nan
    r,_,_=replay(f)
    assert not r['accounting_complete'] and r['return_pct'] is None and r['missing_days_while_held']==1


def test_unheld_coin_gap_does_not_create_borrowed_loss():
    f=flat();f['ETHUSDT'].iloc[5]=np.nan
    r,_,_=replay(f)
    assert r['accounting_complete'] and r['missing_days_while_held']==0


def test_terminal_gap_retains_open_quantity_and_invalid_result():
    f=flat();f['BTCUSDT'].iloc[-1]=np.nan
    r,_,_=replay(f)
    assert r['open_assets']==1 and r['return_pct'] is None


def test_no_historical_price_interpolation_for_liquidity():
    f=flat()
    for d in f.values():d['volume']=.001
    r,l,_=replay(f)
    assert l.empty and r['liquidity_rejections']>0 and r['return_pct']==0


def test_short_period_not_annualized():
    r,_,_=replay(flat());assert r['cagr_pct'] is None


def test_year_products_reconcile_continuous_return():
    r,_,_=replay(fake(800),cost=Costs(fee=0,slip=0))
    annual=np.prod([1+x['return_pct']/100 for x in r['annual']])
    assert annual==pytest.approx(1+r['return_pct']/100)
    assert r['annual'][-1]['full_year'] is False


def test_double_costs_on_flat_prices_cannot_improve():
    a,_,_=replay(flat());b,_,_=replay(flat(),cost=Costs(fee=.002,slip=.001))
    assert b['return_pct']<a['return_pct']


def test_quarter_allocation_keeps_cash_and_smaller_notional():
    a,la,_=replay(flat());b,lb,_=replay(flat(),cost=Costs(allocation=.25))
    assert lb.iloc[0].notional<la.iloc[0].notional*.251
    assert lb.cash_after.min()>7000


@pytest.mark.parametrize('kw',[dict(allocation=2),dict(fee=-1),dict(slip=float('nan')),dict(extra_delay=True),dict(extra_delay=-1),dict(initial=0)])
def test_invalid_financial_settings_rejected(kw):
    with pytest.raises(ValueError):Costs(**kw)


def test_no_true_future_profit_flag():
    r,_,_=replay(flat());assert r['stable_500_proven'] is False and r['live_ready'] is False
