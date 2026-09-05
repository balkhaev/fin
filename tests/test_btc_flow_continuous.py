"""Synthetic engineering fixtures are not market-performance evidence."""
import numpy as np
import pandas as pd
import pytest
from research.btc_flow_continuous.model import grid,Config,features,signals
from research.btc_flow_continuous.data import normalize_time
from research.btc_flow_continuous.engine import simulate,Costs


def fixture(n=30,side=1):
    time=np.arange(n)*60000+1704070800000
    m=np.column_stack([time,np.full(n,100.),np.full(n,100.02),np.full(n,99.98),np.full(n,100.),np.full(n,100000.),np.zeros(n),np.zeros(n),np.full(n,100.),np.full(n,100.1),np.full(n,99.9)])
    sig=np.zeros(n,np.int8);sig[0]=side
    sf=np.full(n,.002)
    return m,sig,sf


def replay(m,sig,sf,rr=2.,hold=15,trailing=False,fee=.0005,slip=.0001,latency=0):
    return simulate(m,sig,sf,rr,hold,trailing,fee,slip,latency)


def test_grid_exactly_54():
    assert len(grid())==54 and len({x.id for x in grid()})==54


def test_microseconds_to_milliseconds():
    assert normalize_time([1735689600000000])[0]==1735689600000
    assert normalize_time([1640995200000])[0]==1640995200000


def test_mixed_units_rejected():
    with pytest.raises(ValueError):normalize_time([1640995200000,1735689600000000])


def test_no_signal_is_cash():
    m,s,sf=fixture();s[:]=0
    e,t,*_=replay(m,s,sf)
    assert len(t)==0 and np.all(e==1000)


def test_entry_next_open_only():
    m,s,sf=fixture();m[1,1]=100.01
    e,t,*_=replay(m,s,sf)
    assert t[0,0]==m[1,0] and t[0,3]==pytest.approx(100.01*1.0001)


def test_costs_and_ledger():
    m,s,sf=fixture();e,t,*_=replay(m,s,sf)
    assert t[0,7]>0 and e[-1]==pytest.approx(1000+t[:,9].sum())
    assert t[0,9]==pytest.approx(t[0,2]*t[0,5]*(t[0,4]-t[0,3])-t[0,7]-t[0,8])


def test_ambiguous_bar_stop_first():
    m,s,sf=fixture();m[1,2:4]=[101,99]
    _,t,*_=replay(m,s,sf);assert t[0,10]==1 and t[0,9]<0


def test_unfavorable_gap_honored():
    m,s,sf=fixture();m[2,1:5]=[99.,99.1,98.9,99.]
    _,t,*_=replay(m,s,sf);assert t[0,4]==pytest.approx(99*.9999)


@pytest.mark.parametrize('side',[1,-1])
def test_real_funding_charge_sign(side):
    m,s,sf=fixture(side=side);m[3,6:9]=[.001,1.,101.]
    e,t,*_=replay(m,s,sf)
    assert t[0,8]==pytest.approx(side*t[0,5]*101*.001)
    assert e[-1]==pytest.approx(1000+t[:,9].sum())


def test_funding_minute_cannot_open():
    m,s,sf=fixture();m[1,7]=1.
    _,t,*_=replay(m,s,sf);assert len(t)==0


def test_missing_funding_mark_not_treated_as_free():
    m,s,sf=fixture();m[3,6:9]=[.001,1.,np.nan]
    result=replay(m,s,sf);assert result[3]==1


def test_adverse_funding_diagnostic_does_not_change_fills():
    m,s,sf=fixture();m[3,6:9]=[.001,1.,100.]
    a=replay(m,s,sf);m[3,9]=110.;b=replay(m,s,sf)
    np.testing.assert_allclose(a[0],b[0])
    np.testing.assert_allclose(a[1][:,:12],b[1][:,:12])
    assert b[1][0,12]>a[1][0,12]


def test_extra_minute_latency():
    m,s,sf=fixture();_,t,*_=replay(m,s,sf,latency=1)
    assert t[0,0]==m[2,0]


def test_end_position_closes_no_hidden_unrealized_profit():
    m,s,sf=fixture(5);e,t,*_=replay(m,s,sf,hold=1000)
    assert t[0,10]==5 and e[-1]==pytest.approx(1000+t[:,9].sum())


def test_hard_drawdown_halt_not_reset_next_day():
    m,s,sf=fixture();s[:]=1;m[2,1:5]=[50.,50.1,49.9,50.]
    m[3:,0]+=86400000
    result=replay(m,s,sf)
    assert result[2]>0 and len(result[1])==1


def test_quantity_is_lot_rounded_and_entry_cap():
    m,s,sf=fixture();_,t,*_=replay(m,s,sf)
    assert np.allclose(t[:,5]/.001,np.round(t[:,5]/.001))
    assert t[0,5]*t[0,3]<=2000


def test_cost_stress_keeps_entry_gate():
    m,s,sf=fixture();a=replay(m,s,sf);b=replay(m,s,sf,fee=.001)
    assert len(a[1])==len(b[1])==1 and a[1][0,0]==b[1][0,0]
    assert b[1][0,5]<=a[1][0,5]


def test_trailing_cannot_apply_within_same_candle():
    m,s,sf=fixture();m[1,1:5]=[100.,100.35,99.99,100.3]
    _,t,*_=replay(m,s,sf,rr=0,hold=100,trailing=True)
    assert t[0,1]>=m[2,0]+60000


def frame_data(n=300):
    rng=np.random.default_rng(40);c=100+np.cumsum(rng.normal(0,.01,n))
    d=pd.DataFrame(dict(close=c,open=np.r_[c[0],c[:-1]],high=c+.03,low=c-.03,volume=np.full(n,100.),quote_volume=c*100,
       buy_quote=c*rng.uniform(20,80,n),spot_close=c*.999,spot_quote_volume=c*100,spot_buy_quote=c*rng.uniform(20,80,n)))
    return d


def test_all_features_prefix_causal():
    d=frame_data();a=features(d);b=features(d.iloc[:250])
    for key in a:
        if key=='flow':
            for window in a[key]:
                for x,y in zip(a[key][window],b[key][window]):np.testing.assert_allclose(x[:250],y,equal_nan=True)
        else:np.testing.assert_allclose(a[key][:250],b[key],equal_nan=True)


def test_spot_gap_is_not_forward_filled_into_signals():
    d=frame_data();d.loc[100,'spot_close']=np.nan;f=features(d)
    assert not f['ready'][100:161].any()
    for cfg in grid():assert not signals(d,f,cfg)[0][100:161].any()


def test_future_values_do_not_change_prior_signals():
    d=frame_data();a=features(d);p=d.iloc[:250];b=features(p)
    for cfg in grid():np.testing.assert_array_equal(signals(d,a,cfg)[0][:250],signals(p,b,cfg)[0])


@pytest.mark.parametrize('kwargs',[{'fee':-.1},{'slip':np.nan},{'latency':-1},{'latency':True}])
def test_invalid_costs_rejected(kwargs):
    with pytest.raises(ValueError):Costs(**kwargs)
