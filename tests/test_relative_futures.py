import copy
import numpy as np
import pandas as pd
import pytest
from research.relative_futures.data import SYMBOLS
from research.relative_futures.account import Costs,simulate
from research.relative_futures.signals import build,NAMES


def data(n=120):
    idx=pd.date_range('2021-01-01',periods=n,freq='h',tz='UTC');out={}
    for k,s in enumerate(SYMBOLS):
        price=np.full(n,100.+k*100)
        out[s]=pd.DataFrame(dict(open=price,high=price*1.001,low=price*.999,close=price,
            volume=np.full(n,1e8),mark_open=price,mark_high=price*1.001,mark_low=price*.999,mark_close=price,
            funding_event=np.isin(idx.hour,[0,8,16]),funding_known=np.ones(n,bool),funding_rate=np.zeros(n)),index=idx)
    return out

def replay(frames,weights,cost=Costs()):
    idx=frames['BTCUSDT'].index
    return simulate(frames,weights,str(idx[0].date()),str((idx[-1]+pd.Timedelta(hours=1)).date()),cost)

def pair_weights(n=120):
    w=np.zeros((n,2));w[:20]=[-.5,.5];return w

def test_flat_cash_has_no_artificial_trades_or_return():
    r,f,_,_,_=replay(data(),np.zeros((120,2)))
    assert r['return_pct']==0 and r['accounting_complete'] and len(f)==0

def test_two_leg_roundtrip_pays_each_leg_each_direction():
    r,f,_,e,_=replay(data(),pair_weights())
    assert len(f)==4 and len(e)==1
    independent=10000.-(f.quantity_delta*f.price+f.fee).sum()
    assert r['final_balance']==pytest.approx(independent)
    assert r['final_balance']<10000 and r['fees']>9.9

def test_signed_relative_price_profit_not_added_notionals():
    d=data();d['ETHUSDT'].loc[d['ETHUSDT'].index[10]:,['open','high','low','close','mark_open','mark_high','mark_low','mark_close']]*=1.1
    r,f,_,e,_=replay(d,pair_weights(),Costs(fee=0,slip=0))
    assert r['final_balance']==pytest.approx(10500.)
    assert e.net.sum()==pytest.approx(500.)

def test_equal_relative_moves_cancel_at_initial_equal_dollar_positions():
    d=data()
    for f in d.values():f.loc[f.index[10]:,['open','high','low','close','mark_open','mark_high','mark_low','mark_close']]*=1.1
    r,_,_,_,_=replay(d,pair_weights(),Costs(fee=0,slip=0))
    assert r['final_balance']==pytest.approx(10000.)

def test_funding_on_long_is_debit_short_is_credit():
    d=data();d['ETHUSDT']['funding_rate']=.001
    r,_,fund,_,_=replay(d,pair_weights(),Costs(fee=0,slip=0))
    assert (fund[fund.symbol=='ETHUSDT'].cashflow<0).all()
    d=data();d['BTCUSDT']['funding_rate']=.001
    r,_,fund,_,_=replay(d,pair_weights(),Costs(fee=0,slip=0))
    assert (fund[fund.symbol=='BTCUSDT'].cashflow>0).all()

def test_first_fill_uses_two_closed_signal_rows_delay():
    r,f,_,_,_=replay(data(),pair_weights())
    assert pd.Timestamp(f.time.iloc[0])==pd.Timestamp('2021-01-01 02:00',tz='UTC')
    r,f,_,_,_=replay(data(),pair_weights(),Costs(delay=1))
    assert pd.Timestamp(f.time.iloc[0])==pd.Timestamp('2021-01-01 03:00',tz='UTC')

def test_missing_held_funding_refuses_complete_result():
    d=data();d['ETHUSDT'].loc[d['ETHUSDT'].index[8],'funding_known']=False
    r,*_=replay(d,pair_weights())
    assert r['unpriced_funding_events']>0 and r['return_pct'] is None

def test_entry_requires_both_legs_capacity():
    d=data();d['ETHUSDT']['volume']=0
    r,f,*_=replay(d,pair_weights())
    assert len(f)==0 and r['capacity_rejections']>0

def test_partial_exit_keeps_real_residual_quantities():
    d=data();d['ETHUSDT'].loc[d['ETHUSDT'].index[20]:,'volume']=500
    r,f,_,_,c=replay(d,pair_weights())
    assert r['partial_exit_hours']>0
    assert ((c.btc_quantity==0)&(c.eth_quantity!=0)).any()
    assert r['accounting_complete']

def test_unclosed_terminal_dust_not_written_off():
    d=data();d['ETHUSDT'].loc[d['ETHUSDT'].index[20]:,'volume']=0
    r,*_=replay(d,pair_weights())
    assert not r['accounting_complete'] and r['return_pct'] is None and r['terminal_quantities'][1]!=0

def test_missing_held_mark_invalidates_margin_evidence():
    d=data();d['ETHUSDT'].loc[d['ETHUSDT'].index[8],'mark_close']=np.nan
    r,*_=replay(d,pair_weights())
    assert r['held_mark_gap_hours']>0 and not r['margin_scenario_verified']

def test_missing_unheld_mark_does_not_poison_existing_position():
    d=data();d['ETHUSDT']['mark_close']=np.nan;d['ETHUSDT']['mark_high']=np.nan;d['ETHUSDT']['mark_low']=np.nan
    w=np.zeros((120,2));w[:20,0]=1
    r,*_=replay(d,w)
    assert r['margin_scenario_verified'] and np.isfinite(r['max_mark_close_drawdown_pct'])

def test_actual_adverse_margin_scenario_is_not_a_fake_fill():
    d=data();d['ETHUSDT'].loc[d['ETHUSDT'].index[9],'mark_low']=1
    d['BTCUSDT'].loc[d['BTCUSDT'].index[9],'mark_high']=1000
    r,f,*_=replay(d,pair_weights())
    assert r['maintenance_scenario_breach'] and not r['margin_scenario_verified']
    assert f.price.min()>90 and f.price.max()<210

def test_empty_signal_does_not_earn_funding():
    d=data()
    for f in d.values():f['funding_rate']=.01
    r,_,fund,*_=replay(d,np.zeros((120,2)))
    assert len(fund)==0 and r['return_pct']==0

@pytest.mark.parametrize('kw',[{'gross':3},{'delay':-1},{'fee':-1},{'initial':0}])
def test_invalid_account_settings_fail(kw):
    with pytest.raises(ValueError):Costs(**kw)

def trend_data(n=2400):
    d=data(n);t=np.arange(n)
    for k,f in enumerate(d.values()):
        p=100*np.exp(.0001*t+.08*np.sin(t/80+k)+.008*np.sin(t/7))
        for col in ('open','close','mark_open','mark_close'):f[col]=p
        f['high']=f['mark_high']=p*1.002;f['low']=f['mark_low']=p*.998
    return d

def test_all_signal_prefixes_independent_of_future_prices():
    d=trend_data();a,_=build(d);b,_=build({s:f.iloc[:2100] for s,f in d.items()})
    assert set(a)==set(NAMES)
    for name in NAMES:np.testing.assert_allclose(a[name][:2100],b[name],atol=1e-12,rtol=1e-12)

def test_pair_targets_are_opposite_and_gross_bounded():
    t,_=build(trend_data())
    for name,w in t.items():
        assert (np.abs(w).sum(axis=1)<=1).all()
        if name.startswith('pair'):np.testing.assert_allclose(w.sum(axis=1),0)

def test_funding_is_not_signal_feature():
    d=trend_data();a,_=build(d)
    for f in d.values():f['funding_rate']=np.arange(len(f))*100
    b,_=build(d)
    for name in a:np.testing.assert_array_equal(a[name],b[name])

def test_signal_state_forgets_missing_price_support():
    d=trend_data();d['BTCUSDT'].iloc[1600,d['BTCUSDT'].columns.get_loc('close')]=np.nan
    t,_=build(d)
    for w in t.values():assert not w[1600:2321].any()
