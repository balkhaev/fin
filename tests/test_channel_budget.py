import numpy as np
import pytest
from research.channel_budget.study import compose, MODELS, PRIMARY


def test_fixed_weights_net_actual_instruments_not_independent_profits():
    pair=np.array([[-.5,.5],[.5,-.5]])
    directional=np.array([[1.,0.],[-1.,0.]])
    r=compose(pair,directional)
    assert tuple(r)==MODELS and PRIMARY=='mix25'
    np.testing.assert_array_equal(r['mix25']*2, np.array([[-.125,.375],[.125,-.375]]))
    np.testing.assert_array_equal(r['relative125']*2,pair*1.25)


def test_all_portfolio_entry_gross_budgets_are_bounded():
    a=np.tile([-.5,.5],(50,1));b=np.tile([.5,.5],(50,1));r=compose(a,b)
    limits={'relative125':1.25,'daily25':.25,'daily50':.5,'mix25':1.,'mix50':1.}
    for name,w in r.items():assert (np.abs(w).sum(axis=1)*2<=limits[name]+1e-12).all()


def test_future_requests_cannot_change_prefix_composition():
    a=np.tile([-.5,.5],(50,1));b=np.tile([1.,0.],(50,1))
    full=compose(a,b);prefix=compose(a[:17],b[:17])
    for name in full:np.testing.assert_array_equal(full[name][:17],prefix[name])


def test_invalid_shapes_nan_or_excess_exposure_are_rejected():
    a=np.tile([-.5,.5],(4,1))
    for b in (a[:2],np.full((4,2),np.nan),a*3):
        with pytest.raises(ValueError):compose(a,b)
