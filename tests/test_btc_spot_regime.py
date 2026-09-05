"""Synthetic engineering tests; none are evidence of trading profitability."""
from dataclasses import replace
import numpy as np
import pandas as pd
import pytest
from research.btc_spot_regime.data import timestamps,periods
from research.btc_spot_regime.engine import Settings,run
from research.btc_spot_regime.signals import build,aggregate,NAMES,PRIMARY


def data(n=48):
    idx=pd.date_range('2024-01-01',periods=n,freq='h',tz='UTC')
    return pd.DataFrame(dict(open=np.full(n,100.),high=np.full(n,100.1),low=np.full(n,99.9),
        close=np.full(n,100.),volume=np.full(n,100000.),observed=np.ones(n,bool)),index=idx)


def replay(d,sig=None,settings=None):
    sig=np.ones(len(d),np.int8) if sig is None else sig
    return run(d,sig,str(d.index[0].tz_localize(None)),str((d.index[-1]+pd.Timedelta(hours=1)).tz_localize(None)),settings or Settings())


def test_exact_source_month_count_and_boundaries():
    x=periods();assert len(x)==104 and x[0]=='2018-01' and x[-1]=='2026-08'


def test_timestamp_normalization():
    assert timestamps([1735689600000000])[0]==1735689600000
    with pytest.raises(ValueError):timestamps([1735689600000,1735689600000000])
    with pytest.raises(ValueError):timestamps([1735689600000001])


def test_ten_fixed_policies_and_primary():
    s=build(data());assert set(s)==set(NAMES) and len(s)==10 and PRIMARY=='ensemble_1d'
    for x in s.values():assert np.isin(x,[0,1]).all()


def test_no_signal_keeps_cash():
    r,t,f,c=replay(data(),np.zeros(48,np.int8))
    assert r['return_pct']==0 and len(t)==len(f)==0 and (c.equity==1000).all()


def test_known_doubling_net_cashflow_no_costs():
    d=data();d.iloc[2:,d.columns.get_indexer(['open','high','low','close'])]=[200,200.1,199.9,200]
    r,t,f,c=replay(d,settings=Settings(fee=0,slip=0))
    assert r['final_cash']==pytest.approx(2000) and r['return_pct']==pytest.approx(100)
    assert len(t)==1 and len(f)==2 and r['open_quantity']==0


def test_no_fill_until_full_hour_after_signal():
    sig=np.zeros(48,np.int8);sig[2:]=1
    r,t,f,_=replay(data(),sig)
    assert f.iloc[0].time_ms==int(pd.Timestamp('2024-01-01 03:00',tz='UTC').timestamp()*1000)


def test_fees_both_sides_and_independent_notional_sum():
    r,t,f,_=replay(data())
    signed=np.where(f.side.eq('buy'),-1.,1.)
    cash=1000+(signed*f.quantity*f.price-f.fee).sum()
    assert r['final_cash']==pytest.approx(cash) and r['fees']==pytest.approx(f.fee.sum())
    assert len(f)==2 and f.fee.min()>0 and r['return_pct']<0


def test_cash_never_negative_no_averaging():
    r,t,f,c=replay(data())
    assert c.cash.min()>=-1e-8 and len(f)==2
    assert t.quantity.iloc[0]*t.entry.iloc[0]+f.fee.iloc[0]<=1000


def test_quarter_allocation_has_smaller_exposure():
    a=replay(data())[2];b=replay(data(),settings=Settings(allocation=.25))[2]
    assert b.quantity.iloc[0]<=a.quantity.iloc[0]*.251


def test_protection_cannot_guarantee_gap_bound_and_does_not_restart():
    d=data();d.iloc[3:,d.columns.get_indexer(['open','high','low','close'])]=[60,60.1,59.9,60]
    r,t,f,_=replay(d,settings=Settings(drawdown_stop=.07))
    assert len(t)==1 and t.reason.iloc[0]=='drawdown_gap' and r['return_pct']<-30
    assert r['halted_at'] is not None and len(f)==2


def test_intrabar_drawdown_trigger_prior_closed_peak():
    d=data();d.iloc[3,d.columns.get_loc('low')]=80
    r,t,f,_=replay(d,settings=Settings(drawdown_stop=.07))
    assert t.reason.iloc[0]=='drawdown_stop' and r['return_pct']==pytest.approx(-7)
    assert r['round_trips']==1


def test_held_gap_invalidates_result_not_forward_filled():
    d=data();d.iloc[3,d.columns.get_indexer(['open','high','low','close','volume'])]=np.nan
    r,t,_,_=replay(d)
    assert not r['accounting_complete'] and r['return_pct'] is None
    assert r['held_data_gap'] and t.reason.iloc[0]=='data_gap_recovery'


def test_terminal_missing_quote_preserves_open_exposure():
    d=data();d.iloc[-1,d.columns.get_indexer(['open','high','low','close','volume'])]=np.nan
    r,*_=replay(d)
    assert r['open_quantity']>0 and r['return_pct'] is None


def test_liquidity_insufficient_does_not_invent_fills():
    d=data();d['volume']=.001
    r,t,*_=replay(d)
    assert len(t)==0 and r['liquidity_rejected']>0


def test_short_period_not_annualized():
    r,*_=replay(data());assert r['cagr_pct'] is None


def test_full_period_boundary_is_liquidated_with_costs():
    r,t,f,_=replay(data());assert t.reason.iloc[0]=='period_end'
    assert f.time_ms.iloc[-1]==int(pd.Timestamp('2024-01-03',tz='UTC').timestamp()*1000)


def test_all_policy_prefixes_unchanged_by_future_candles():
    d=data(6500);p=100+np.arange(len(d))*.01+np.sin(np.arange(len(d))*.1)
    for k,offset in [('open',0),('close',0),('high',.1),('low',-.1)]:d[k]=p+offset
    a=build(d);b=build(d.iloc[:6001])
    for n in NAMES:np.testing.assert_array_equal(a[n][:6001],b[n])


def test_partial_daily_aggregate_never_becomes_closed():
    d=data(25);b=aggregate(d,24)
    assert b.close.iloc[0]==100 and pd.isna(b.close.iloc[1])


def test_gap_resets_slow_indicator_support():
    d=data(6500);p=100+np.arange(len(d))*.01
    for k,offset in [('open',0),('close',0),('high',.1),('low',-.1)]:d[k]=p+offset
    d.iloc[5200,d.columns.get_indexer(['open','high','low','close','volume'])]=np.nan
    s=build(d)
    assert not s['momentum_1d_63_200'][5250:].any()


def test_commission_stress_same_flat_prices_cannot_improve_net():
    a=replay(data())[0];b=replay(data(),settings=Settings(fee=.002,slip=.001))[0]
    assert b['return_pct']<a['return_pct']


@pytest.mark.parametrize('kw',[dict(allocation=2),dict(fee=-1),dict(slip=float('nan')),dict(delay=0),dict(delay=True),dict(capital=0)])
def test_invalid_parameters(kw):
    with pytest.raises(ValueError):Settings(**kw)


def test_same_fills_stress_is_not_new_strategy():
    r,*_=replay(data())
    assert r['same_fills_double_commission_net']==pytest.approx(r['net']-r['fees'])
    assert r['live_ready'] is False and r['funding_applicable'] is False
