"""Predictive component tests; no account or profitability assertions."""
import copy
import json
import numpy as np
import pandas as pd
import pytest
from research.clock_phase.data import SYMBOLS,parse
from research.clock_phase.learning import prepare,forecasts,training_rows,PARAMS,HORIZONS,MODELS
from research.clock_phase.information import evaluate,block_interval
from test_clock_phase_data import raw_row,raw_zip


def frames(n=5800):
    idx=pd.date_range('2022-07-01',periods=n,freq='h',tz='UTC');result={};rng=np.random.default_rng(440)
    for k,s in enumerate(SYMBOLS):
        t=np.arange(n);price=100*np.exp(.00002*t+.006*np.sin(t/9+k)+np.cumsum(rng.normal(0,.001,n)))
        q=1e6*(1+.1*np.sin(t/7+k));oi=.5+.08*np.sin(t/5+k)
        result[s]=pd.DataFrame(dict(open=price,close=price*1.0001,high=price*1.004,low=price*.996,
            volume=q/price,quote_volume=q,buy_quote=q*oi,trades=np.full(n,500),
            boundary_quote=q*.09,boundary_buy=q*.09*(.5+.1*np.sin(t/3)),
            placebo_quote=q*.08,placebo_buy=q*.08*(.5+.1*np.cos(t/4)),
            price2=price*1.0002,volume2=np.full(n,10000.),price17=price*1.0003,volume17=np.full(n,10000.),
            bar_ok=np.ones(n,bool),minute_count=np.full(n,60)),index=idx)
    return result


def test_partial_candle_mask_preserves_timestamp_and_audit():
    row=raw_row();row[6]=row[0]+41646
    d=parse(raw_zip([row]),mask_partial=True)
    assert d.index[0]==pd.Timestamp('2024-01-01',tz='UTC') and pd.isna(d.close.iloc[0])
    assert d.attrs['partial_candles']==[{'open_ms':row[0],'duration_ms':41646}]


def test_too_long_candle_cannot_be_silently_masked():
    row=raw_row();row[6]=row[0]+60000
    with pytest.raises(ValueError):parse(raw_zip([row]),mask_partial=True)


def test_all_models_have_identical_valid_rows():
    p=prepare(frames())
    assert set(p.x)==set(MODELS) and p.valid.any()
    for x in p.x.values():assert np.isfinite(x[p.valid]).all()
    assert p.x['boundary'].shape==p.x['placebo'].shape
    assert p.x['boundary'].shape[-1]==p.x['base'].shape[-1]+4


def test_feature_prefix_is_independent_of_future_minute_data():
    f=frames();a=prepare(f);b=prepare({s:d.iloc[:5200] for s,d in f.items()})
    for name in MODELS:np.testing.assert_allclose(a.x[name][:5200],b.x[name],equal_nan=True)
    np.testing.assert_equal(a.valid[:5200],b.valid)
    for h in HORIZONS:np.testing.assert_allclose(a.y[h][:5200-h-1],b.y[h][:5200-h-1],equal_nan=True)


def test_label_uses_delayed_actual_open_not_previous_close():
    f=frames();p=prepare(f);t=300;h=4
    expected=np.log(f['BTCUSDT'].price2.iloc[t+h+1]/f['BTCUSDT'].price2.iloc[t+1])/(p.volatility[t,0]*np.sqrt(h))
    assert p.y[h][t,0]==pytest.approx(expected)


def test_future_missing_price_invalidates_label_not_prior_features():
    f=frames();original=prepare(f);f['BTCUSDT'].loc[f['BTCUSDT'].index[305],'price2']=np.nan
    p=prepare(f)
    assert np.isnan(p.y[4][300,0])
    np.testing.assert_allclose(original.x['boundary'][300],p.x['boundary'][300],equal_nan=True)


def test_maturity_has_full_day_embargo_for_both_horizons():
    p=prepare(frames());month=pd.Timestamp('2023-01-01',tz='UTC')
    for h in HORIZONS:
        ti=training_rows(p,month,h)
        assert (p.index[ti]+pd.Timedelta(hours=1+h,minutes=2)<=month-pd.Timedelta(hours=24)).all()
        assert p.index[ti[0]]>=month-pd.Timedelta(days=180)


def test_forecast_prefix_and_model_text_hashes_reproduce():
    f=frames();a=prepare(f);b=prepare({s:d.iloc[:5200] for s,d in f.items()})
    pa,aa=forecasts(a);pb,ab=forecasts(b)
    assert aa[:len(ab)]==ab
    for key in pa:np.testing.assert_array_equal(pa[key][:5200],pb[key])


def test_future_labels_cannot_change_current_month_fit():
    a=prepare(frames());b=copy.deepcopy(a);month=pd.Timestamp('2023-01-01',tz='UTC')
    for h in HORIZONS:
        too_late=a.index+pd.Timedelta(hours=h+1,minutes=2)>month-pd.Timedelta(hours=24)
        b.y[h][too_late]=1000.
    pa,aa=forecasts(a,end_month=month);pb,ab=forecasts(b,end_month=month)
    assert aa==ab
    for key in pa:np.testing.assert_array_equal(pa[key],pb[key])


def test_random_early_stop_and_parameter_search_disabled():
    assert PARAMS['early_stopping'] is False and PARAMS['max_iter']==80
    assert PARAMS['max_depth']==3 and PARAMS['random_state']==20260906


def test_no_future_labels_cross_information_period_boundary():
    p=prepare(frames());prediction={key:np.zeros(p.valid.shape) for key in [(m,h) for m in MODELS for h in HORIZONS]}
    a,_,_=evaluate(p,prediction,'2023-01-01','2023-02-01',4)
    q=copy.deepcopy(p)
    future=q.index+pd.Timedelta(hours=5,minutes=2)>=pd.Timestamp('2023-02-01',tz='UTC')
    q.y[4][future]=1e6
    b,_,_=evaluate(q,prediction,'2023-01-01','2023-02-01',4)
    assert a==b


def test_conditional_rows_are_not_compounded_account_returns():
    p=prepare(frames());prediction={(m,h):np.full(p.valid.shape,10.) for m in MODELS for h in HORIZONS}
    result,_,samples=evaluate(p,prediction,'2023-01-01','2023-02-01',4)
    assert len(samples)>0
    for row in result['conditional_observations']:
        assert row['portfolio_profit_computed'] is False and row['overlapping_signal_observations_not_trades']


def test_bootstrap_reproducible_and_empty_input_explicit():
    assert block_interval([]) is None
    assert block_interval([1.,2.,3.])==block_interval([1.,2.,3.])


def test_bad_hour_grid_not_repaired():
    f=frames();f={s:d.drop(d.index[5]) for s,d in f.items()}
    with pytest.raises(ValueError):prepare(f)
