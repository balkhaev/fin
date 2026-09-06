"""Signal aggregation tests; archived trading reference is not imported."""
import copy
import numpy as np
import pandas as pd
import pytest
from research.channel_scale.study import completed_bars, channel_targets


def frames(n=3000):
    index = pd.date_range('2020-01-01', periods=n, freq='h', tz='UTC')
    p = 100 + np.arange(n) * .1
    return {s: pd.DataFrame({'open': p, 'high': p + 1, 'low': p - 1, 'close': p + .2}, index=index)
            for s in ('BTCUSDT', 'ETHUSDT')}


def fake_original(close, high, low, valid):
    # A deliberately simple causal stand-in verifies publication timing only.
    return np.where(valid, np.where(close.index.day % 2 == 0, 1., -1.), 0.)


def test_bars_index_is_release_not_start_and_values_are_actual_aggregation():
    f = frames(25)['BTCUSDT']; b = completed_bars(f, 24)
    assert b.index[0] == pd.Timestamp('2020-01-02', tz='UTC')
    assert b.iloc[0].open == f.iloc[0].open and b.iloc[0].close == f.iloc[23].close
    assert b.iloc[0].high == f.iloc[:24].high.max()
    assert b.iloc[1].isna().all()


def test_missing_constituent_cannot_create_valid_complete_bar():
    f = frames(48)['BTCUSDT']; f.iloc[5, f.columns.get_loc('high')] = np.nan
    b = completed_bars(f, 24)
    assert b.iloc[0].isna().all() and b.iloc[1].notna().all()


def test_unfinished_day_cannot_affect_earlier_released_signals():
    f = frames(); a, _ = channel_targets(f, fake_original)
    cut = 2807
    b, _ = channel_targets({s: d.iloc[:cut] for s, d in f.items()}, fake_original)
    for name in a:
        np.testing.assert_array_equal(a[name][:cut], b[name])
        assert (np.abs(a[name]).sum(axis=1) <= 1.).all()


def test_future_price_change_does_not_change_past_aggregate_states():
    f = frames(); g = copy.deepcopy(f); cut = 2807
    for d in g.values():
        d.iloc[cut:] *= 5
    a, _ = channel_targets(f, fake_original); b, _ = channel_targets(g, fake_original)
    for name in a:
        np.testing.assert_array_equal(a[name][:cut], b[name][:cut])


def test_signal_before_seventy_three_daily_closes_is_zero():
    target, _ = channel_targets(frames(), fake_original)
    assert not target['btc_h24'][:73 * 24 - 1].any()
    assert target['btc_h24'][73 * 24 - 1, 0] != 0


def test_unregistered_clock_and_removed_hour_are_rejected():
    f = frames()['BTCUSDT']
    with pytest.raises(ValueError):
        completed_bars(f, 3)
    with pytest.raises(ValueError):
        completed_bars(f.drop(f.index[20]), 24)
