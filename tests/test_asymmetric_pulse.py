"""Synthetic correctness fixtures, not evidence of actual trading returns."""
import numpy as np
import pandas as pd
import pytest
from research.annual_rotation.data import SYMBOLS
from research.asymmetric_pulse.policy import (
    PRIMARY,NAMES,CONTROLS,build,event_states,risk_estimate,rank_target,schedule,segmented_ema,
)
from research.asymmetric_pulse.study import admitted,select,months


def frames(n=340):
    index=pd.date_range('2020-01-01',periods=n,freq='D',tz='UTC');result={}
    for k,s in enumerate(SYMBOLS):
        t=np.arange(n);c=100*np.exp(t*.002+.04*np.sin(t/9+k))
        result[s]=pd.DataFrame(dict(open=c,close=c,high=c*1.015,low=c*.985,
            volume=np.full(n,1e7),quote_volume=np.full(n,1e9)),index=index)
    return result


def event_fixture():
    entry=np.zeros((8,2),bool);entry[1]=True;valid=np.ones_like(entry,bool)
    close=np.full((8,2),100.);low=np.full((8,2),90.)
    return entry,valid,close,low


def test_registry_fixed_and_schedule_explicit():
    assert len(NAMES)==11 and len(CONTROLS)==4 and len(set(NAMES+CONTROLS))==15
    assert schedule(PRIMARY).every==1 and schedule('asymmetric_blend_weekly').every==7
    with pytest.raises(ValueError):schedule('winner_after_results')


def test_event_can_persist_without_repeated_trigger():
    a,b,c,d=event_fixture();state,count=event_states(a,b,c,d,42)
    assert count==2 and state[1:].all() and not state[0].any()


def test_event_maximum_holding_time_is_signal_time_not_future_label():
    a,b,c,d=event_fixture();state,_=event_states(a,b,c,d,3)
    assert state[1:4].all() and not state[4:].any()


def test_signal_exit_cannot_reenter_same_closed_day():
    a,b,c,d=event_fixture();c[3]=80;a[3]=True
    state,count=event_states(a,b,c,d,42)
    assert not state[3:].any() and count==2


def test_signal_peak_drawdown_is_observable_only():
    a,b,c,d=event_fixture();c[2]=120;c[3]=105;d[:]=np.nan
    state,_=event_states(a,b,c,d,7,.10)
    assert state[2].all() and not state[3:].any()


def test_gap_resets_event_state_not_fabricates_a_price():
    a,b,c,d=event_fixture();b[3]=False;c[3]=np.nan
    state,_=event_states(a,b,c,d,42)
    assert not state[3:].any()


def test_future_event_rows_do_not_change_prior_state():
    a,b,c,d=event_fixture();full,_=event_states(a,b,c,d,42)
    part,_=event_states(a[:5],b[:5],c[:5],d[:5],42)
    np.testing.assert_array_equal(full[:5],part)


def test_risk_floor_stays_positive_in_one_sided_rally():
    x=np.column_stack([np.linspace(.001,.06,60),np.linspace(.002,.03,60)])
    w=np.array([.5,.5]);symmetric=risk_estimate(w,x)
    downside=risk_estimate(w,x,True)
    assert symmetric>0 and downside==pytest.approx(.25*symmetric)


def test_downside_metric_cannot_be_called_variance_bound():
    x=np.tile(np.array([[-.04,.03],[.02,-.03]]),(30,1));w=np.array([.5,.5])
    risk=risk_estimate(w,x,True)
    assert risk>0 and risk>=.25*risk_estimate(w,x)


def test_incomplete_risk_history_refuses_sizing():
    assert np.isinf(risk_estimate(np.array([1.]),np.zeros((59,1)),True))
    assert np.isinf(risk_estimate(np.array([1.]),np.full((60,1),np.nan),True))


def test_top_slots_do_not_redistribute_cash_into_one_winner():
    score=np.zeros((80,9));good=np.zeros((80,9),bool);good[:,0]=True
    returns=np.tile(np.linspace(-.01,.01,80)[:,None],(1,9))
    w,_=rank_target(score,good,returns,3,'none')
    np.testing.assert_allclose(w.sum(axis=1),1/3)
    assert not w[:,1:].any()


def test_risk_target_never_borrows():
    rng=np.random.default_rng(73);r=rng.normal(0,.08,(120,9));score=rng.uniform(0,1,r.shape)
    w,risk=rank_target(score,np.ones(r.shape,bool),r,3,'downside')
    assert (w>=0).all() and (w.sum(axis=1)<=1+1e-12).all()
    assert risk.max()<=.5+1e-12


def test_excluded_coin_cannot_take_a_rank_slot():
    r=np.zeros((80,9));score=np.ones_like(r);score[:,-1]=100
    w,_=rank_target(score,np.ones(r.shape,bool),r,3,'none','DOGEUSDT')
    assert not w[:,-1].any() and np.allclose(w.sum(axis=1),1)


def test_targets_have_no_hidden_short_or_multiaccount_leverage():
    targets,trace,counts=build(frames())
    assert set(targets)==set(NAMES+CONTROLS)
    for name,w in targets.items():
        assert np.isfinite(w).all() and (w>=0).all() and (w.sum(axis=1)<=1+1e-10).all(),name
    np.testing.assert_array_equal(targets[PRIMARY],targets['asymmetric_blend_weekly'])
    assert targets[PRIMARY].sum()>0 and counts['event_starts_are_not_filled_orders']


def test_prefix_causality_of_every_new_policy():
    f=frames();short={s:d.iloc[:305] for s,d in f.items()}
    a,_,_=build(f);b,_,_=build(short)
    for name in a:np.testing.assert_allclose(a[name][:305],b[name],atol=1e-12,rtol=1e-11)


def test_gap_does_not_reuse_trend_entry_support():
    f=frames();f['BTCUSDT'].iloc[260]=np.nan
    t,_,_=build(f)
    for name in ('early_total','early_downside','early_full','leader_downside','residual_downside','btc_early_downside'):
        assert not t[name][260:,0].any(),name


def test_allocation_exclusion_propagates_into_blends_and_controls():
    t,_,_=build(frames(),exclude='DOGEUSDT')
    for w in t.values():assert not w[:,-1].any()


def test_ema_resets_after_missing_observation():
    f=pd.DataFrame({'x':[1.,2.,3.,np.nan,4.,5.,6.]})
    r=segmented_ema(f,3)
    assert np.isnan(r.x.iloc[3:6]).all() and np.isfinite(r.x.iloc[6])


def test_no_dropped_dates_or_changed_cohort():
    f=frames()
    with pytest.raises(ValueError):build({s:d.drop(d.index[20]) for s,d in f.items()})
    del f['DOGEUSDT']
    with pytest.raises(ValueError):build(f)


def qualifying(name='a'):
    return dict(policy=name,accounting_complete=True,cagr_pct=20.,max_close_drawdown_pct=-15.,
                worst_rolling_365_pct=-5.,rebalance_days=30)


def test_selection_is_training_only_and_deterministic():
    a,b=qualifying('a'),qualifying('b');assert select([b,a])=='a'
    b['cagr_pct']=30;assert select([a,b])=='b'


@pytest.mark.parametrize('key,value',[
    ('accounting_complete',False),('cagr_pct',0.),('cagr_pct',None),
    ('max_close_drawdown_pct',-35.01),('worst_rolling_365_pct',-15.01),('rebalance_days',23),
])
def test_gate_cannot_accept_incomplete_or_high_risk_winner(key,value):
    r=qualifying();r[key]=value
    assert not admitted(r) and select([r]) is None


def test_months_compound_and_zero_is_not_a_win():
    c=pd.DataFrame({'time':['2024-02-01 00:00:00+00:00','2024-03-01 00:00:00+00:00'],
        'equity':[11000.,11000.]})
    m=months(c,10000)
    assert m[0]['return_pct']==pytest.approx(10) and m[1]['return_pct']==0


def test_full_daily_model_is_not_a_live_executor():
    assert PRIMARY=='asymmetric_blend'
    # All execution/accounting belongs to unchanged existing simulate, not event_states.
    a,b,c,d=event_fixture();_,n=event_states(a,b,c,d,42)
    assert n==2
