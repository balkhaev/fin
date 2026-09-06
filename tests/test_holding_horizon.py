"""Tests of the finite experiment driver; no archived engine imported by test collection."""
import copy
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from research.holding_horizon.reference import load_reference, digest
from research.holding_horizon.study import candidates, assess, HORIZONS, BUDGETS, MODELS, PRIMARY, CONTROL, STARTS


def test_archive_execution_requires_explicit_flag_before_access(tmp_path):
    with pytest.raises(PermissionError, match='allow-archived-reference'):
        load_reference(tmp_path)
    assert 'research.relative_futures.account' not in sys.modules


def test_unknown_archive_is_not_a_fallback(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_reference(tmp_path, acknowledged=True)


def test_no_reference_account_copied_into_main():
    root = Path(__file__).parents[1]
    assert not (root / 'research/relative_futures/account.py').exists()


def test_frozen_five_horizons_and_two_sizes():
    assert tuple(HORIZONS.values()) == (336, 720, 1440, 2160, 1000000000)
    assert BUDGETS == {'100': 1., '125': 1.25}
    assert len(MODELS) == 10 and PRIMARY == 'hold90_125' and CONTROL == 'hold30_125'


def test_only_existing_function_arguments_are_changed():
    calls = []
    close = pd.DataFrame({'BTCUSDT': [10.] * 10, 'ETHUSDT': [20.] * 10})
    def existing(frame, **kwargs):
        assert frame is close
        calls.append(kwargs)
        return np.tile([-.375, .375], (len(frame), 1))
    target = candidates(close, existing)
    assert [k['max_hours'] for k in calls] == list(HORIZONS.values())
    assert all(k['trailing'] is False and k['risk_size'] is False for k in calls)
    for name, value in target.items():
        gross = 1.25 if name.endswith('_125') else 1.
        np.testing.assert_allclose(np.abs(value).sum(axis=1) * 2, gross)
        np.testing.assert_array_equal(value.sum(axis=1), 0.)


def test_all_seven_origins_are_exactly_365_days():
    assert len(STARTS) == 7
    for start, end in STARTS:
        assert (pd.Timestamp(end) - pd.Timestamp(start)).days == 365


def gate_inputs():
    full = {'qualification': {'qualified_historical_scenario': True}, 'return_pct': 110.,
        'cagr_pct': 14., 'max_mark_close_drawdown_pct': -25., 'completed_episodes': 80}
    late = dict(copy.deepcopy(full), return_pct=70., max_mark_close_drawdown_pct=-10., completed_episodes=25)
    p = {'full': full, 'later': late, 'later_double_costs': copy.deepcopy(late), 'later_delay2': copy.deepcopy(late)}
    c = {'full': dict(copy.deepcopy(full), cagr_pct=12.), 'later': dict(copy.deepcopy(late), return_pct=59.)}
    starts = {PRIMARY: {'qualified': 7, 'negative': 1, 'worst_return_pct': -5.}, CONTROL: {'qualified': 7, 'negative': 2, 'worst_return_pct': -10.}}
    return p, c, starts


def test_joint_gate_can_pass_synthetic_metrics_not_market_proof():
    assert all(assess(*gate_inputs()).values())


def test_high_cumulative_gain_cannot_hide_low_CAGR():
    p, c, o = gate_inputs(); p['full']['return_pct'] = 500.; p['full']['cagr_pct'] = 10.
    assert not assess(p, c, o)['full_CAGR_above_control']


def test_sparse_profitable_candidate_fails_frequency_gate():
    p, c, o = gate_inputs(); p['later']['completed_episodes'] = 5
    assert not assess(p, c, o)['at_least20_later_episodes']


def test_unpriced_risk_is_not_accepted():
    p, c, o = gate_inputs(); p['later']['qualification']['qualified_historical_scenario'] = False
    assert not assess(p, c, o)['qualified_primary']


def test_worse_start_or_drawdown_is_not_hidden():
    p, c, o = gate_inputs(); o[PRIMARY]['worst_return_pct'] = -20.
    p['full']['max_mark_close_drawdown_pct'] = -40.
    gates = assess(p, c, o)
    assert not gates['origins_no_worse'] and not gates['full_drawdown_at_most30']


def test_none_and_losing_stresses_are_failures():
    p, c, o = gate_inputs(); p['full']['cagr_pct'] = None
    p['later_double_costs']['return_pct'] = -.01
    gates = assess(p, c, o)
    assert not gates['full_CAGR_above_control'] and not gates['later_stress_positive']


def test_canonical_pin_changes_when_evidence_changes():
    assert digest({'a': 1, 'b': 2}) == digest({'b': 2, 'a': 1})
    assert digest({'a': 1}) != digest({'a': 2})
