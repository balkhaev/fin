import numpy as np
import pandas as pd
import pytest
from research.relative_futures_checks.candidates import episode_statistics


def test_episode_profit_factor_and_loss_streak_are_descriptive():
    e=pd.DataFrame(dict(net=[10.,-4.,-2.,8.,-1.],entry_time=['2025-01-01T00:00:00Z']*5,exit_time=['2025-01-02T00:00:00Z']*5))
    c=pd.DataFrame({'btc_quantity':[0.,1.,1.,0.],'eth_quantity':[0.,-1.,-1.,0.]})
    r=episode_statistics(e,c,{'same_fills_adverse_funding_extra':2.})
    assert r['profit_factor']==pytest.approx(18/7)
    assert r['longest_losing_streak']==2 and r['held_quote_hours']==2
    assert r['largest_five_as_fraction_of_net']==pytest.approx(18/11)
    assert r['mean_episode_hours']==24 and r['descriptive_not_an_alternative_trading_rule']


def test_empty_episode_set_never_becomes_infinite_profit_factor():
    r=episode_statistics(pd.DataFrame(),pd.DataFrame({'btc_quantity':[0.],'eth_quantity':[0.]}),{'same_fills_adverse_funding_extra':0.})
    assert r['profit_factor'] is None and r['mean_episode_hours'] is None and r['completed_episodes']==0


def test_losing_account_has_no_fictitious_positive_profit_share():
    e=pd.DataFrame(dict(net=[1.,-5.],entry_time=['2025-01-01T00:00:00Z']*2,exit_time=['2025-01-02T00:00:00Z']*2))
    r=episode_statistics(e,pd.DataFrame({'btc_quantity':[0.],'eth_quantity':[0.]}),{'same_fills_adverse_funding_extra':0.})
    assert r['largest_five_as_fraction_of_net'] is None
