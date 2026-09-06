"""Tests for the saved target policy only. No claim about the blocked evaluator."""
import numpy as np
import pandas as pd
import pytest
from research.rotation_stability.policy import (
    POLICIES,PRIMARY,risk_scale,build,freeze_after_first_drawdown,
)
from research.annual_rotation.data import SYMBOLS


def frames(n=420):
    idx=pd.date_range('2020-01-01',periods=n,freq='D',tz='UTC')
    result={}
    for k,s in enumerate(SYMBOLS):
        t=np.arange(n);p=100*np.exp(.0015*t+.025*np.sin(t/11+k))
        result[s]=pd.DataFrame(dict(open=p,close=p,high=p*1.01,low=p*.99,
            volume=np.full(n,1e7),quote_volume=np.full(n,1e9)),index=idx)
    return result


def primary():return next(p for p in POLICIES if p.name==PRIMARY)


def test_fixed_policies_and_primary_are_unique():
    assert len(POLICIES)==11 and len({p.name for p in POLICIES})==11
    assert primary().volatility==.20 and primary().gross==.60 and primary().asset_cap==.20


def test_risk_budget_reduces_not_levers_exposure():
    w=np.array([.5,.3,.2]);cov=np.diag([.01,.008,.009])
    v,before,after=risk_scale(w,cov,primary())
    assert (v>=0).all() and (v<=w).all()
    assert v.sum()<=.60+1e-12 and v.max()<=.20+1e-12
    assert 0<after<=.20+1e-12 and before>after


def test_correlated_assets_do_not_look_like_independent_bets():
    w=np.array([.3,.3,.3]);a,_,_=risk_scale(w,np.eye(3)*.0025,primary())
    b,_,_=risk_scale(w,np.ones((3,3))*.0025,primary())
    assert b.sum()<=a.sum()


def test_zero_covariance_never_enables_infinite_exposure():
    v,_,_=risk_scale(np.array([.3,.3,.3]),np.zeros((3,3)),primary())
    assert not v.any()


def test_missing_covariance_fails_closed():
    v,_,_=risk_scale(np.array([.3,.3,.3]),np.full((3,3),np.nan),primary())
    assert not v.any()


def test_target_aggregation_is_one_funded_budget():
    targets,logs=build(frames())
    for name,w in targets.items():
        assert np.isfinite(w).all() and (w>=0).all()
        assert (w.sum(axis=1)<=1+1e-10).all()
    assert targets[PRIMARY].sum()>0
    w=targets[PRIMARY]
    assert (w.sum(axis=1)<=.60+1e-12).all() and w.max()<=.20+1e-12
    assert logs[PRIMARY].forecast_vol_after.max()<=.20+1e-12


def test_future_candles_cannot_change_old_targets():
    f=frames();short={s:d.iloc[:333] for s,d in f.items()}
    full,_=build(f);part,_=build(short)
    for name in full:np.testing.assert_allclose(full[name][:333],part[name],rtol=0,atol=1e-14)


def test_cohort_exclusion_removes_all_allocations_to_that_coin():
    targets,_=build(frames(),exclude='DOGEUSDT')
    for value in targets.values():assert not value[:,-1].any()


def test_excluding_btc_does_not_replace_it_with_a_future_price():
    targets,_=build(frames(),exclude='BTCUSDT')
    for value in targets.values():assert not value[:,0].any()


def test_missing_market_history_blocks_guarded_budget():
    f=frames();f['BTCUSDT'].iloc[250]=np.nan
    targets,_=build(f)
    assert not targets[PRIMARY][250:].any()


def test_fixed_budget_stress_changes_size_not_asset_direction():
    targets,_=build(frames())
    a,b,c=[targets[n] for n in ('guarded_ensemble10',PRIMARY,'guarded_ensemble30')]
    assert (a<=b+1e-12).all() and (b<=c+1e-12).all()


def test_first_drawdown_control_is_permanent_and_preserves_prefix():
    curve=pd.DataFrame({'time':['a','b','c','d'],'equity':[10100.,9700.,9500.,13000.]})
    target=np.ones((10,2))*.25
    changed,info=freeze_after_first_drawdown(target,curve,3)
    assert info['signal_row']==5
    np.testing.assert_array_equal(changed[:5],target[:5])
    assert not changed[5:].any()


def test_future_recovery_does_not_move_first_halt():
    curve=pd.DataFrame({'time':['a','b','c'],'equity':[10100.,9500.,13000.]})
    a,info=freeze_after_first_drawdown(np.ones((6,2))*.2,curve,0)
    curve.loc[2,'equity']=1e9
    b,again=freeze_after_first_drawdown(np.ones((6,2))*.2,curve,0)
    assert info==again
    np.testing.assert_array_equal(a,b)


def test_no_breach_does_not_manufacture_an_exit():
    target=np.ones((8,2))*.2
    result,info=freeze_after_first_drawdown(target,pd.DataFrame({'equity':[10000.,10100.],'time':['a','b']}),0)
    assert info is None
    np.testing.assert_array_equal(target,result)


def test_unpriced_equity_cannot_define_a_stop():
    with pytest.raises(ValueError):
        freeze_after_first_drawdown(np.ones((5,2))*.2,pd.DataFrame({'equity':[10000.,np.nan],'time':['a','b']}),0)


@pytest.mark.parametrize('w',[[-.1,.3],[.8,.8],[float('nan'),0]])
def test_invalid_allocation_rejected(w):
    with pytest.raises(ValueError):risk_scale(np.array(w),np.eye(2),primary())
