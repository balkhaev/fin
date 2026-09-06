import numpy as np
import pandas as pd
from test_relative_futures import trend_data
from research.relative_futures.signals import build as legacy_build
from research.opportunity_runner.study import states,build


def test_168_no_trail_reproduces_original_signal_at_scaled_notional():
    frames=trend_data(5000);legacy,_=legacy_build(frames)
    close=pd.DataFrame({s:f.close for s,f in frames.items()})
    np.testing.assert_array_equal(states(close),legacy['pair_momentum720']*.75)


def test_prefix_causality_for_all_lifecycle_variants():
    frames=trend_data(5000);a=build(frames);b=build({s:f.iloc[:4507] for s,f in frames.items()})
    for name in a:np.testing.assert_array_equal(a[name][:4507],b[name])


def test_future_prices_do_not_change_past_trailing_levels():
    frames=trend_data(5000);a=build(frames)
    for f in frames.values():f.loc[f.index[4507:],'close']*=50
    b=build(frames)
    for name in a:np.testing.assert_array_equal(a[name][:4507],b[name][:4507])


def test_episode_size_never_rises_in_same_sign_run():
    values=build(trend_data(5000))
    for name,w in values.items():
        assert (np.abs(w).sum(axis=1)*2<=1.5+1e-12).all()
        np.testing.assert_array_equal(w.sum(axis=1),0.)
        same=np.all(np.sign(w[1:])==np.sign(w[:-1]),axis=1)&(np.abs(w[1:]).sum(axis=1)>0)
        np.testing.assert_allclose(w[1:][same],w[:-1][same])
