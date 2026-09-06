import copy
import numpy as np
import pandas as pd
from test_relative_futures import trend_data
from research.relative_portfolio.study import build,PRIMARY,CONTROL,NAMES


def test_combination_is_one_bounded_signed_target():
    targets,trace=build(trend_data(6200))
    assert set(targets)==set(NAMES) and trace.daily_history_complete.any()
    bull=trace.btc_daily_bull.to_numpy()
    np.testing.assert_equal(targets[PRIMARY][bull],targets[CONTROL][bull])
    np.testing.assert_equal(targets[PRIMARY][~bull],targets['relative_half_bear_only'][~bull])
    for value in targets.values():assert (np.abs(value).sum(axis=1)<=.5+1e-12).all()


def test_unfinished_day_does_not_leak_final_close_into_earlier_signals():
    frames=trend_data(6200)
    a,ta=build(frames)
    cut=6007  # deliberately not at UTC midnight
    b,tb=build({s:f.iloc[:cut] for s,f in frames.items()})
    for name in a:np.testing.assert_allclose(a[name][:cut],b[name],atol=1e-12,rtol=1e-12)
    pd.testing.assert_frame_equal(ta.iloc[:cut],tb)


def test_future_hour_mutation_leaves_past_daily_gate_identical():
    f=trend_data(6200);g=copy.deepcopy(f);cut=6007
    for d in g.values():d.loc[d.index[cut:],['open','close','high','low']]*=100
    a,ta=build(f);b,tb=build(g)
    for name in a:np.testing.assert_allclose(a[name][:cut],b[name][:cut],atol=1e-12,rtol=1e-12)
    pd.testing.assert_frame_equal(ta.iloc[:cut],tb.iloc[:cut])


def test_incomplete_daily_history_is_not_a_bear_regime_permission():
    f=trend_data(6200);f['BTCUSDT'].loc[f['BTCUSDT'].index[6000],'close']=np.nan
    targets,trace=build(f)
    # The incomplete day only becomes relevant after its publication boundary.
    boundary=f['BTCUSDT'].index[6000].floor('D')+pd.Timedelta(days=1)
    invalid=pd.to_datetime(trace.signal_available)>=boundary
    assert not trace.daily_history_complete[invalid].any()
    for value in targets.values():assert not value[invalid].any()
