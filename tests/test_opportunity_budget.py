import hashlib
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from research.opportunity_budget.policy import (build, freeze_and_refresh, risk_allocate,
    Allocation, PRIMARY, MODELS, BASIS)
from test_relative_futures import trend_data


def test_reference_account_not_modified_or_reimplemented():
    path = Path(__file__).parents[1]/'research/relative_futures/account.py'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == 'b67c939a829c0c2366964ed8ac97747f5747b8664fceee679ff0dd3b0a023cee'


def test_same_sign_state_does_not_pyramid_on_rising_request():
    desired = np.tile([.5, 0.], (200, 1)); desired[100:, 0] = 1.5
    frozen, refreshed = freeze_and_refresh(desired)
    assert not refreshed.any()
    np.testing.assert_array_equal(frozen, np.tile([.5, 0.], (200, 1)))


def test_reduction_is_four_signal_hours_flat_not_instant_filled_resize():
    desired = np.tile([1.5, 0.], (200, 1)); desired[100:, 0] = .5
    frozen, refreshed = freeze_and_refresh(desired)
    assert refreshed.sum() == 1 and refreshed[100]
    assert not frozen[100:104].any()
    np.testing.assert_array_equal(frozen[104:], desired[104:])


def test_no_refresh_is_explicit_control():
    desired = np.tile([1.5, 0.], (200, 1)); desired[100:, 0] = .5
    frozen, refreshed = freeze_and_refresh(desired, False)
    assert not refreshed.any() and (frozen[:, 0] == 1.5).all()


def test_early_cut_not_mistaken_for_future_information():
    desired = np.tile([.5, -.2], (100, 1)); desired[80:] = [.1, -.05]
    full, _ = freeze_and_refresh(desired); prefix, _ = freeze_and_refresh(desired[:83])
    np.testing.assert_array_equal(full[:83], prefix)


def test_zero_sleeves_keep_cash_even_with_minimum_confidence_budget():
    cov = np.tile(np.eye(2)*.0001, (10, 1, 1))
    target, vol, conf = risk_allocate(np.zeros((10, 3)), np.ones((10, 3)), cov, cov, (1,1,1), Allocation())
    assert not target.any() and not conf.any()


def test_leverage_is_smaller_for_higher_predicted_risk():
    cov = np.tile(np.eye(2)*.0001, (10,1,1)); sides = np.tile([1,0,0], (10,1))
    a, *_ = risk_allocate(sides, np.ones_like(sides), cov, cov, (1,0,0), Allocation())
    b, *_ = risk_allocate(sides, np.ones_like(sides), cov*4, cov*4, (1,0,0), Allocation())
    np.testing.assert_allclose(b, a/2)


def test_equal_opposite_instrument_exposures_are_netted_not_double_funded():
    cov = np.tile(np.eye(2)*.000001, (10,1,1))
    sides=np.tile([1,1,1], (10,1)); conf=np.ones_like(sides)
    result,*_=risk_allocate(sides,conf,cov,cov,(1,1,1),Allocation(cap=2))
    assert (np.abs(result).sum(axis=1)<=2+1e-12).all()
    assert np.isfinite(result).all()
    np.testing.assert_array_equal(BASIS[2], [-.5,.5])

@pytest.mark.parametrize('kw',[{'annual_risk':0},{'cap':2.1},{'annual_risk':np.nan}])
def test_invalid_risk_configuration_is_rejected(kw):
    with pytest.raises(ValueError): Allocation(**kw)


def test_all_targets_causal_and_leverage_bounded():
    frames=trend_data(5000); a,da=build(frames)
    cut=4507;b,db=build({s:d.iloc[:cut] for s,d in frames.items()})
    assert set(a)==set(MODELS)
    for name in a:
        np.testing.assert_allclose(a[name][:cut],b[name],atol=1e-12,rtol=1e-12)
        if name not in ('old_pair_1x','old_pair_15x','old_pair_2x'):
            cap=2 if name=='multi_risk30_cap2' else 1.5
            assert (np.abs(a[name]).sum(axis=1)*2<=cap+1e-12).all()
    for name in da.model.unique():
        pd.testing.assert_frame_equal(da[da.model==name].iloc[:cut].reset_index(drop=True),db[db.model==name].reset_index(drop=True))


def test_future_prices_cannot_change_earlier_targets():
    frames=trend_data(5000);a,_=build(frames);cut=4507
    for d in frames.values(): d.loc[d.index[cut:],['open','high','low','close']]*=100
    b,_=build(frames)
    for name in a: np.testing.assert_array_equal(a[name][:cut],b[name][:cut])


def test_missing_price_disables_new_composition_until_complete_support():
    frames=trend_data(5000);frames['ETHUSDT'].iloc[4000,frames['ETHUSDT'].columns.get_loc('close')]=np.nan
    targets,_=build(frames)
    for name in targets:
        if not name.startswith('old_pair'): assert not targets[name][4000:].any()
