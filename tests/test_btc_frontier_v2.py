import numpy as np
import pandas as pd
import pytest
from research.btc_frontier_v2.models import Config,grid,aggregate,prepare_bars,signals
from research.btc_frontier_v2.engine import simulate,run,market,Costs,Risk


def fixture(n=20,side=1):
    m=np.column_stack([np.arange(n)*60000+1640995260000,np.full(n,100.),np.full(n,100.02),np.full(n,99.98),
                       np.full(n,100.),np.full(n,100000.),np.zeros(n),np.zeros(n)])
    x=(np.r_[np.int8(side),np.zeros(n-1,np.int8)],np.full(n,.002),np.zeros(n,np.int8),8,2.,False)
    return m,x


def test_grid_precommitted():
    g=grid();assert len(g)==128 and len({c.id for c in g})==128


def test_next_open():
    m,x=fixture();m[1,1]=100.03
    _,t,_=run(m,x)
    assert t.iloc[0].entry_ms==m[1,0]
    assert t.iloc[0].entry==pytest.approx(100.03*1.0001)


def test_stop_first():
    m,x=fixture();m[1,2:4]=[101,99]
    _,t,_=run(m,x)
    assert t.iloc[0].reason==1 and t.iloc[0].net<0


def test_stop_gap():
    m,x=fixture();m[2,1:5]=[99,99.1,98.9,99]
    _,t,_=run(m,x)
    assert t.iloc[0].exit==pytest.approx(99*.9999)


def test_fees_funding_reconcile():
    m,x=fixture();m[3,6:]=[.001,1]
    s,t,_=run(m,x)
    assert t.iloc[0].funding==pytest.approx(t.iloc[0].quantity*.1)
    assert s['final_equity']==pytest.approx(10000+t.net.sum())


def test_no_entry_on_funding_timestamp():
    m,x=fixture();m[1,7]=1
    _,t,_=run(m,x);assert len(t)==0


def test_latency():
    m,x=fixture();_,t,_=run(m,x,costs=Costs(latency=1))
    assert t.iloc[0].entry_ms==m[2,0]


def test_max_hold():
    m,x=fixture();_,t,_=run(m,x)
    assert t.iloc[0].exit_ms-t.iloc[0].entry_ms==480000


def test_short_funding_sign():
    m,x=fixture(side=-1);m[3,6:]=[.001,1]
    _,t,_=run(m,x);assert t.iloc[0].funding<0


def test_no_averaging_and_lot_exposure():
    m,x=fixture();x[0][:]=1
    _,t,_=run(m,x)
    assert len(t)<=2
    assert np.all(t.entry*t.quantity<=t.entry_equity*2)
    assert np.allclose((t.quantity/.001).round(),t.quantity/.001)


def test_breakers_do_not_restart():
    m,x=fixture(n=300);x[0][:]=1;m[:,2]=101;m[:,3]=99
    s,t,_=run(m,x,risk=Risk(drawdown=.01,daily=.02))
    assert s['halted_at'] is not None
    assert s['return_pct']<0 and len(t)<20


def test_closed_channel_exit_precedes_new_range():
    m,x=fixture();x[2][2]=1;m[3,2:4]=[110,90]
    _,t,_=run(m,x);assert t.iloc[0].reason==4 and t.iloc[0].exit_ms==m[3,0]


def test_close_trailing_cannot_see_next_bar():
    m,x=fixture();x=(*x[:3],20,0.,True)
    m[2,1:5]=[100,100.3,99.99,100.2]
    m[3,1:5]=[100.2,100.25,100.,100.1]
    _,t,_=run(m,x)
    assert t.iloc[0].exit_ms>=m[3,0]


def test_parameter_guards():
    for kw in ({'exposure':3},{'fraction':.01},{'drawdown':.5}):
        with pytest.raises(ValueError):Risk(**kw)
    with pytest.raises(ValueError):Costs(fee=-1)


def synthetic(n=2000):
    idx=pd.date_range('2023-01-01 00:01',periods=n,freq='min',tz='UTC')
    rng=np.random.default_rng(43);c=100+np.cumsum(rng.normal(0,.05,n))
    o=np.r_[c[0],c[:-1]]
    return pd.DataFrame(dict(timestamp=idx.view('int64')//1000000-60000,open=o,high=np.maximum(o,c)+.01,
      low=np.minimum(o,c)-.01,close=c,volume=np.full(n,10.),quote_volume=10*c,
      taker_buy_volume=np.full(n,6.)),index=idx)


def test_prefix_causality_all_families():
    d=synthetic();f=pd.DataFrame(dict(calc_time=[d.timestamp.iloc[0]],last_funding_rate=[.001]))
    pre=d.iloc[:1802]
    for family in ('breakout','trend_pullback','impulse_follow','impulse_fade','range_revert','squeeze','trend_channel','funding_revert'):
        c=Config(family,5,12,'fast')
        full=signals(d,{5:prepare_bars(aggregate(d,5),f)},c)
        part=signals(pre,{5:prepare_bars(aggregate(pre,5),f)},c)
        for a,b in zip(full[:3],part[:3]):np.testing.assert_allclose(a[:len(pre)],b,equal_nan=True)


def test_partial_aggregate_excluded():
    d=synthetic(11);b=aggregate(d,5)
    assert len(b)==2 and b.index[-1]==d.index[9]


def test_future_funding_is_not_feature():
    d=synthetic(200);t=d.timestamp.iloc[150]
    f=pd.DataFrame(dict(calc_time=[t],last_funding_rate=[.001]))
    b=prepare_bars(aggregate(d,5),f)
    assert b.loc[b.index<pd.to_datetime(t,unit='ms',utc=True),'last_funding'].isna().all()


def test_no_signal_is_cash():
    m,x=fixture();x[0][:]=0
    s,t,_=run(m,x);assert s['return_pct']==0 and len(t)==0


def test_missing_funding_rejected():
    d=synthetic(500)
    f=pd.DataFrame(dict(minute_time=[],last_funding_rate=[]))
    with pytest.raises(ValueError,match='Funding coverage'):market(d,f)


def test_router_change_closes_position():
    m,x=fixture();x[2][2]=2
    _,t,_=run(m,x)
    assert t.iloc[0].reason==4


def test_end_of_period_liquidates_and_reconciles():
    m,x=fixture(5);x=(*x[:3],100,2.,False)
    s,t,_=run(m,x)
    assert t.iloc[0].reason==6 and s['final_equity']==pytest.approx(10000+t.net.sum())


def test_future_changes_do_not_change_earlier_fills():
    m,x=fixture(20);_,a,_=run(m,x)
    m[15:,1:5]*=2
    _,b,_=run(m,x)
    pd.testing.assert_frame_equal(a,b)


def test_fresh_loader_missing_manifest_fails(tmp_path):
    from research.btc_frontier_v2.study import load_fresh
    (tmp_path/'manifest.json').write_text('[]')
    with pytest.raises(ValueError,match='manifest'):load_fresh(tmp_path,None,None)
