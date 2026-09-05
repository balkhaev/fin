import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research.btc_flow.engine import (Bar, FlowSignal, Indicators, Parameters,
                                     PaperBroker, PassiveOrder, Quote, Signal, Trade, replay_jsonl)
from research.btc_flow.study import Config, Costs, Risk, features, simulate, arm_signals


def synthetic_arrays(n=12):
    t = np.arange(n,dtype=np.int64)*60000 + 1640995260000
    return dict(ts=t,o=np.full(n,100.),h=np.full(n,100.02),l=np.full(n,99.99),
                c=np.full(n,100.),volumes=np.full(n,100000.),atr=np.full(n,.1),
                vwap=np.full(n,100.5),signals=np.r_[np.int8(1),np.zeros(n-1,np.int8)],
                funding=np.zeros(n),hold=8,rr=2.,fee_in=.0005,fee_out=.0005,
                slip=.0001,risk=.001,exposure=1.,daily_limit=1.,dd_limit=1.)


def test_no_signal_no_trades():
    a=synthetic_arrays();a['signals'][:]=0
    eq,t,_=simulate(**a)
    assert len(t)==0 and np.all(eq==10000)


def test_next_open_not_signal_close():
    a=synthetic_arrays();a['o'][1]=100.05
    _,t,_=simulate(**a)
    assert t[0,0]==a['ts'][1]
    assert t[0,3]==pytest.approx(100.05*1.0001)


def test_ambiguous_bar_stop_first():
    a=synthetic_arrays();a['h'][1]=101;a['l'][1]=99
    _,t,_=simulate(**a)
    assert t[0,10]==1 and t[0,9]<0


def test_gap_worsens_stop():
    a=synthetic_arrays();a['o'][2]=99;a['l'][2]=98.9;a['h'][2]=99.1;a['c'][2]=99
    _,t,_=simulate(**a)
    assert t[0,4]==pytest.approx(99*.9999)


def test_costs_reconcile_and_nominal_capped():
    a=synthetic_arrays();eq,t,_=simulate(**a)
    assert eq[-1]==pytest.approx(10000+t[:,9].sum())
    assert t[0,7]>0 and t[0,3]*t[0,5]<=10000


def test_funding_not_ignored_on_open_position():
    a=synthetic_arrays();a['funding'][3]=.0001
    eq,t,_=simulate(**a)
    assert t[0,8]==pytest.approx(t[0,5]*100*.0001)
    assert eq[-1]==pytest.approx(10000+t[:,9].sum())


def test_hold_max_eight_minutes():
    a=synthetic_arrays();_,t,_=simulate(**a)
    assert t[0,1]-t[0,0]==8*60000
    assert t[0,10]==3


def test_short_is_mirrored():
    a=synthetic_arrays();a['signals'][0]=-1;a['vwap'][:]=99.5;a['h'][:]=100.01;a['l'][:]=99.98
    _,t,_=simulate(**a)
    assert len(t)==1 and t[0,2]==-1 and t[0,7]>0


def test_target_no_favorable_gap_credit():
    a=synthetic_arrays();a['o'][2]=101;a['l'][2]=100.9;a['h'][2]=101.1;a['c'][2]=101
    _,t,_=simulate(**a)
    assert t[0,4]==pytest.approx(100.5*.9999)


def test_daily_and_global_barriers():
    a=synthetic_arrays(n=8);a['risk']=.01;a['daily_limit']=.0005;a['dd_limit']=.0005
    a['l'][1]=99.8
    eq,t,halted=simulate(**a)
    assert t[0,10]==4 and halted>0 and len(t)==1


def test_no_reentry_same_excursion():
    direction=np.array([1,1,1,0,1],dtype=np.int8)
    eligible=np.array([True,True,True,False,True])
    out=arm_signals(direction,eligible,np.array([99.,99.,99.,100.,99.]),np.full(5,100.))
    assert list(out)==[1,0,0,0,1]


def test_features_are_prefix_causal():
    n=450;idx=pd.date_range('2024-01-01 00:01',periods=n,freq='min',tz='UTC')
    rng=np.random.default_rng(11);c=100+np.cumsum(rng.normal(0,.03,n))
    d=pd.DataFrame(dict(timestamp=idx.view('int64')//1000000-60000,
                        close=c,high=c+.05,low=c-.05,volume=np.full(n,100.),
                        quote_volume=c*100,taker_buy_volume=np.full(n,50.)),index=idx)
    fund=pd.DataFrame(dict(minute_time=[int(d.timestamp.iloc[0])],last_funding_rate=[.0001]))
    full=features(d,fund);pre=features(d.iloc[:333],fund)
    for key in ('trend','flow','atr','stall_long','stall_short'):
        np.testing.assert_allclose(full[key][:333],pre[key],equal_nan=True)
    for w in (20,60,120):
        np.testing.assert_allclose(full['vwaps'][w][:333],pre['vwaps'][w],equal_nan=True)


@pytest.mark.parametrize('kwargs',[{'risk_fraction':0},{'max_exposure':10},{'maker':-.1},{'lot':0},{'daily_loss':0},{'slippage':math.nan}])
def test_invalid_parameters_rejected(kwargs):
    with pytest.raises(ValueError):Parameters(**kwargs)


def test_quote_cannot_cross():
    with pytest.raises(ValueError):Quote(0,101,100,1,1)


def test_trade_direction_explicit():
    with pytest.raises(ValueError):Trade(1,100,1,'false')


def test_unclosed_bar_rejected():
    with pytest.raises(ValueError):Indicators().update(Bar(60000,101,99,100,1,100),59999)


def test_missing_bar_rejected():
    i=Indicators();i.update(Bar(60000,101,99,100,1,100),60000)
    with pytest.raises(ValueError):i.update(Bar(180000,101,99,100,1,100),180000)


def test_indicator_requires_warmup():
    i=Indicators();i.update(Bar(60000,101,99,100,1,100),60000)
    assert i.context(60000) is None


def test_queue_not_filled_by_touch_or_wrong_aggressor():
    p=Parameters(participation=1);o=PassiveOrder(1,100,1,10,1000,20000,True)
    assert o.fill(Trade(1001,100,5,True),p)==0
    assert o.fill(Trade(1002,100,5,False),p)==0
    assert o.queue_ahead==5
    assert o.fill(Trade(1003,100,6,False),p)==pytest.approx(1)


def test_latency_and_ttl():
    p=Parameters(participation=1);o=PassiveOrder(1,100,1,0,1000,2000,True)
    assert o.fill(Trade(999,99,2,False),p)==0
    assert o.fill(Trade(2000,99,2,False),p)==0


def test_partial_fills_bounded_by_participation():
    p=Parameters(participation=.01);o=PassiveOrder(1,100,100,0,0,20000,True)
    assert o.fill(Trade(100,99,10,False),p)==pytest.approx(.1)


def test_post_only_rejection_not_taker_conversion():
    b=PaperBroker();q=Quote(60001,100,100.1,0,0)
    assert b.submit(Signal(60001,1,100,100.5,.001),q)
    b.activate(60300,Quote(60300,99.8,99.9,0,0))
    assert b.pending is None and b.qty==0
    assert b.events[-1]['type']=='post_only_reject'


def opened_broker():
    p=Parameters(tick=.01,participation=1)
    b=PaperBroker(p);q=Quote(60001,100,100.01,0,0)
    assert b.submit(Signal(60001,1,100,100.5,.001),q)
    q=Quote(60300,100,100.01,0,0)
    b.on_trade(Trade(60300,100,1,False),q)
    assert b.qty==1
    return b


def test_partial_fill_protected_immediately_and_reconciles():
    b=opened_broker();b.on_trade(Trade(60400,99.8,10,False),Quote(60400,99.79,99.8,1,1))
    assert b.qty==0 and len(b.trades)==1
    assert b.trades[0]['reason']=='stop'
    assert b.cash==pytest.approx(10000+b.trades[0]['net'])


def test_stale_protective_quote_blocks_performance_claim():
    b=opened_broker();b.on_trade(Trade(65000,99.8,1,False),Quote(60300,100,100.01,0,0))
    assert b.execution_incomplete and b.halted and b.qty>=1


def test_real_funding_sign():
    b=opened_broker();before=b.cash;b.on_funding(60400,.001,100)
    assert b.cash==pytest.approx(before-.1) and b.funding==pytest.approx(.1)


def test_target_requires_trade_through_not_touch():
    b=opened_broker();b.pending=None
    b.on_trade(Trade(61000,100.5,2,True),Quote(61000,100.5,100.51,1,1))
    assert b.qty==1
    b.on_trade(Trade(61100,100.51,2,True),Quote(61100,100.5,100.51,1,1))
    assert b.qty==0 and b.trades[0]['reason']=='target'


def test_replay_requires_evidence_metadata(tmp_path):
    p=tmp_path/'input.jsonl';p.write_text('{"type":"trade","ts":1,"price":100,"qty":1,"buyer_aggressor":true}\n')
    with pytest.raises(ValueError):replay_jsonl(p,tmp_path/'out.json')


def test_flow_signal_exact_windows_and_rearming():
    e=FlowSignal(Parameters(tick=.01));e.indicators.context=lambda now:(100.5,.1,1)
    # Previous 10s sells; current 10s buys; flat prices meet stall condition.
    result=[]
    for k in range(22):
        t=60001+k*1000
        r=e.on_trade(Trade(t,100.,1.,k>=11),Quote(t,100.,100.01,1,1))
        if r:result.append(r)
    assert len(result)==1 and result[0].side==1


def test_feed_gap_requires_new_flow_warmup():
    e=FlowSignal();e.indicators.context=lambda now:(100.5,.1,1)
    for k in range(22):e.on_trade(Trade(60001+k*1000,100,1,False),Quote(60001+k*1000,100,100.1,1,1))
    assert e.on_trade(Trade(90000,100,100,True),Quote(90000,100,100.1,1,1)) is None


def test_ineligible_early_signal_does_not_consume_pullback():
    from research.btc_flow.study import make_signals
    n=5
    d=pd.DataFrame(dict(close=np.array([100.,100.,100.,100.,100.])))
    f=dict(atr=np.full(n,.1),trend=np.ones(n,dtype=np.int8),flow=np.ones(n,dtype=np.int8),
           stall_long=np.ones(n,bool),stall_short=np.ones(n,bool),
           vwaps={20:np.array([100.15,100.2,100.5,100.5,100.5])})
    assert list(make_signals(d,f,Config()))==[0,0,1,0,0]


def test_cost_stress_does_not_tighten_entry_gate():
    a=synthetic_arrays();_,base,_=simulate(**a)
    a['fee_in']=.0008;a['fee_out']=.0008
    _,stress,_=simulate(**a)
    assert len(base)==len(stress)==1
    assert base[0,0]==stress[0,0]


def test_truncated_manifest_is_rejected(tmp_path):
    from research.btc_flow.study import load_data
    (tmp_path/'manifest.json').write_text('[]')
    with pytest.raises(ValueError,match='55 months'):
        load_data(tmp_path)
