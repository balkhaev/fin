"""Synthetic mechanics tests. They are not evidence of financial profitability."""
import copy
import numpy as np
import pandas as pd
import pytest
from research.annual_rotation.data import SYMBOLS
from research.cash_gap.targets import compose, build, cadence, PRIMARY, NAMES
from research.cash_gap.study import gates, run_lengths, relative_growth


def frames(n=650):
    index = pd.date_range('2020-01-01', periods=n, tz='UTC')
    out = {}; t = np.arange(n)
    for k, name in enumerate(SYMBOLS):
        c = 100*np.exp(.0008*t+.25*np.sin(t/65+k/4)+.02*np.sin(t/8+k))
        out[name] = pd.DataFrame(dict(open=c, high=c*1.02, low=c*.98, close=c*1.001,
            volume=np.full(n, 1e7), quote_volume=np.full(n, 1e9)), index=index)
    return out


def test_compose_preserves_every_active_core_row_exactly():
    core=np.array([[.2, .1], [0., 0.], [.1, .2]])
    other=np.array([[0., 1.], [.5, .5], [1., 0.]])
    result=compose(core,other,[False,True,False],.1)
    np.testing.assert_equal(result[[0,2]],core[[0,2]])
    np.testing.assert_equal(result[1],[.05,.05])


def test_cannot_add_idle_sleeve_on_top_of_nonzero_core():
    with pytest.raises(ValueError): compose(np.array([[.2,0.]]),np.array([[0.,1.]]),[True],.1)


@pytest.mark.parametrize('fraction',[-1,0,1.01,np.nan])
def test_invalid_budget_refused(fraction):
    with pytest.raises(ValueError):compose(np.zeros((3,2)),np.ones((3,2))*.5,[True]*3,fraction)


def test_misaligned_regime_refused():
    with pytest.raises(ValueError):compose(np.zeros((3,2)),np.zeros((3,2)),[True],.1)


def test_full_build_core_and_sleeve_are_exclusive_targets():
    t,d=build(frames())
    assert set(t)==set(NAMES)
    idle=d.idle_sleeve_allowed.to_numpy()
    np.testing.assert_equal(t[PRIMARY][~idle],t['core_weekly'][~idle])
    assert not t['core_weekly'][idle].any()
    assert not t['sleeve_trend10_only'][~idle].any()
    assert (t[PRIMARY][idle].sum(axis=1)<=.1+1e-12).all()
    for value in t.values():
        assert np.isfinite(value).all() and (value>=0).all() and (value.sum(axis=1)<=1+1e-10).all()


def test_prefix_causality_of_every_variant():
    f=frames();a,da=build(f);b,db=build({s:d.iloc[:530] for s,d in f.items()})
    for name in a:np.testing.assert_allclose(a[name][:530],b[name],atol=1e-12,rtol=1e-11)
    pd.testing.assert_frame_equal(da.iloc[:530],db)


def test_future_price_mutation_does_not_change_old_decisions():
    f=frames();g=copy.deepcopy(f)
    for d in g.values():d.loc[d.index[530:],['open','high','low','close']]*=100
    a,_=build(f);b,_=build(g)
    for name in a:np.testing.assert_allclose(a[name][:530],b[name][:530],atol=1e-12,rtol=1e-11)


def test_missing_201day_history_never_becomes_extra_permission():
    f=frames();f['ETHUSDT'].iloc[400]=np.nan
    t,d=build(f)
    assert not d.idle_sleeve_allowed.iloc[400:601].any()
    assert not t['sleeve_trend10_only'][400:601].any()
    assert not t['sleeve_btc10_only'][400:601].any()


def test_prior_candidate_target_is_not_scaled_twice():
    from research.rotation_stability.policy import build as old_build
    f=frames();t,_=build(f);old,_=old_build(f)
    np.testing.assert_equal(t['pr132_budget25_every3'],old['ensemble_market_gate'])
    assert cadence('pr132_budget25_every3')==3


def test_daily_is_only_execution_frequency_not_different_target():
    t,_=build(frames())
    np.testing.assert_equal(t[PRIMARY],t['idle_trend10_daily'])
    assert cadence(PRIMARY)==7 and cadence('idle_trend10_daily')==1
    with pytest.raises(ValueError):cadence('post_hoc_winner')


def test_no_dropped_dates_or_assets():
    f=frames(250)
    with pytest.raises(ValueError):build({s:d.drop(d.index[50]) for s,d in f.items()})
    del f['DOGEUSDT']
    with pytest.raises(ValueError):build(f)


def test_flat_run_counts_days_not_number_of_separate_runs():
    assert run_lengths([True,True,False,True,True,True])==3
    assert run_lengths([False,False])==0


def test_admission_cannot_hide_later_underperformance():
    full=dict(accounting_complete=True,cagr_pct=20.,max_close_drawdown_pct=-15.)
    later=dict(accounting_complete=True,return_pct=8.,closed_asset_positions=40)
    cfull=dict(accounting_complete=True,cagr_pct=15.,max_close_drawdown_pct=-14.)
    clater=dict(accounting_complete=True,return_pct=9.,closed_asset_positions=35)
    stress=[dict(accounting_complete=True,return_pct=3.)]*4
    origins=[dict(accounting_complete=True,return_pct=5.)]*19
    result=gates(full,later,cfull,clater,stress,origins,origins)
    assert result['higher_full_CAGR'] and not result['higher_later_return'] and not all(result.values())


def test_relative_log_growth_partition_reconciles_but_is_not_sleeve_PnL():
    p=pd.DataFrame({'time':['2024-01-02T00:00:00Z','2024-01-03T00:00:00Z'],'equity':[10100.,10300.]})
    c=pd.DataFrame({'time':p.time,'equity':[10100.,10200.]})
    d=pd.DataFrame({'signal_date':['2023-12-30T00:00:00Z','2023-12-31T00:00:00Z'], 'core_market_allowed':[True,False]})
    result=relative_growth(p,c,d,'2024-01-01')
    assert result['relative_final_wealth_pct']==pytest.approx((10300/10200-1)*100)
    assert sum(result['partition_log_growth'].values())==pytest.approx(np.log(10300/10200))
    assert result['sum_of_separate_account_profits'] is False
