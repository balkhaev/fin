"""Synthetic causality/data invariants; fixtures do not demonstrate profit."""
import copy
import numpy as np
import pandas as pd
import pytest
from research.annual_rotation.data import SYMBOLS
from research.flow_learning.data import validate_extra
from research.flow_learning.learning import prepare,forecasts,targets,train_indices,fit_ridge,predict,POLICIES


def fake(n=1000):
    idx=pd.date_range('2020-01-01',periods=n,freq='D',tz='UTC');out={}
    rng=np.random.default_rng(711)
    for k,s in enumerate(SYMBOLS):
        t=np.arange(n);c=100*np.exp(.0004*t+.05*np.sin(t/9+k)+np.cumsum(rng.normal(0,.005,n)))
        q=1e8*(1+.15*np.sin(t/7+k));ratio=.5+.05*np.sin(t/5+k)
        out[s]=pd.DataFrame(dict(open=c,close=c*1.001,high=c*1.01,low=c*.99,volume=q/c,
            quote_volume=q,buy_quote=q*ratio,buy_volume=q/c*ratio,trades=np.full(n,100000)),index=idx)
    return out


@pytest.mark.parametrize('column,value',[('buy_quote',2e12),('buy_volume',2e12),('trades',1.5),('trades',-1),('volume',np.nan),('quote_volume',-10),('buy_quote',-1)])
def test_malformed_extra_values_rejected(column,value):
    d=fake(5)['BTCUSDT'];d[column]=value
    with pytest.raises(ValueError):validate_extra(d)


def test_zero_trades_cannot_hide_positive_volume():
    d=fake(5)['BTCUSDT'];d.trades=0
    with pytest.raises(ValueError):validate_extra(d)


def test_realistic_extra_fixture_has_bounded_aggressor_amounts():
    for d in fake(5).values():validate_extra(d)


def test_fixed_horizon_and_maturity_embargo():
    idx=pd.date_range('2020-01-01',periods=1100,freq='D',tz='UTC');m=pd.Timestamp('2022-05-01',tz='UTC')
    ti=train_indices(idx,m)
    assert (idx[ti]+pd.Timedelta(days=9)<=m-pd.Timedelta(days=7)).all()
    assert idx[ti[-1]]==m-pd.Timedelta(days=16)
    assert idx[ti[0]]>=m-pd.Timedelta(days=730)


def test_matching_support_for_all_three_models():
    f=prepare(fake())
    assert set(f.arrays)=={'price','flow','stale_flow'}
    for a in f.arrays.values():assert np.isfinite(a[f.valid]).all()
    assert f.valid.any()


def test_stale_flow_uses_past_not_permuted_future_rows():
    f=prepare(fake())
    # 13 price features precede 8 flow features, then 9 ID columns.
    p=13;n=8
    np.testing.assert_equal(f.arrays['stale_flow'][63:,:,p:p+n],f.arrays['flow'][:-63,:,p:p+n])


def test_prefix_features_cannot_depend_on_later_prices():
    d=fake();a=prepare(d);b=prepare({s:x.iloc[:920] for s,x in d.items()})
    for k in a.arrays:np.testing.assert_allclose(a.arrays[k][:920],b.arrays[k],equal_nan=True)
    np.testing.assert_equal(a.valid[:920],b.valid)
    np.testing.assert_allclose(a.labels[:911],b.labels[:911],equal_nan=True)


def test_forecasts_and_target_prefix_are_causal():
    d=fake();a=prepare(d);b=prepare({s:x.iloc[:920] for s,x in d.items()})
    pa,aa=forecasts(a);pb,ab=forecasts(b)
    assert aa[:len(ab)]==ab
    for k in pa:np.testing.assert_allclose(pa[k][:920],pb[k],atol=1e-12,rtol=1e-12,equal_nan=True)
    for name in POLICIES:np.testing.assert_allclose(targets(a,pa,name)[:920],targets(b,pb,name),atol=1e-12,rtol=1e-12)


def test_future_label_mutation_cannot_change_previous_fits():
    a=prepare(fake());b=copy.deepcopy(a);month=pd.Timestamp('2022-05-01',tz='UTC')
    future=a.dates+pd.Timedelta(days=9)>month-pd.Timedelta(days=7)
    b.labels[future]=9999
    pa,_=forecasts(a);pb,_=forecasts(b)
    before=a.dates<pd.Timestamp('2022-06-01',tz='UTC')
    for k in pa:np.testing.assert_allclose(pa[k][before],pb[k][before],atol=1e-12,rtol=1e-12,equal_nan=True)


def test_trade_targets_do_not_consume_labels():
    a=prepare(fake());p,_=forecasts(a);before=targets(a,p,'flow7_weekly')
    a.labels[:]=1e6
    np.testing.assert_equal(before,targets(a,p,'flow7_weekly'))


def test_all_fitted_labels_mature_before_cutoff():
    _,audits=forecasts(prepare(fake()))
    for a in audits:
        if a['status']=='fitted':
            assert a['latest_label_maturity']<=a['cutoff']<a['month']
            assert a['distinct_signal_days']>=365 and a['sample_count']>=1500


def test_training_normalization_does_not_depend_on_inference_batch():
    x=np.arange(60).reshape(20,3).astype(float);y=np.sin(np.arange(20));m=fit_ridge(x,y)
    old=predict(m,x[-1:])
    np.testing.assert_allclose(old,predict(m,np.r_[x[-1:],np.full((5,3),1e20)])[:1],atol=1e-14,rtol=1e-14)
    np.testing.assert_equal(m['center'],x.mean(axis=0))


def test_regularization_handles_constant_columns():
    m=fit_ridge(np.ones((30,3)),np.ones(30)*.2)
    assert np.isfinite(predict(m,np.ones((5,3)))).all()


def test_insufficient_history_keeps_cash():
    a=prepare(fake(500));p,audits=forecasts(a)
    assert not audits
    assert not targets(a,p,'flow7_weekly').any()


def test_risk_caps_and_btc_only_allocation():
    a=prepare(fake());p={k:np.ones(a.valid.shape) for k in a.arrays}
    for name in POLICIES:
        w=targets(a,p,name);assert (w>=0).all() and (w.sum(axis=1)<=1+1e-10).all()
        if not name.endswith('bold'):assert (w.sum(axis=1)<=.6+1e-10).all()
        if name.endswith('btc'):assert not w[:,1:].any()
        elif not name.endswith('bold'):assert w.max()<=.2+1e-10


def test_trading_exclusion_does_not_modify_predictions():
    a=prepare(fake());p,_=forecasts(a);before=copy.deepcopy(p)
    w=targets(a,p,'flow7_weekly','DOGEUSDT');assert not w[:,-1].any()
    for k in p:np.testing.assert_equal(p[k],before[k])


def test_monthly_model_fits_use_one_shared_sample_mask():
    _,audits=forecasts(prepare(fake()))
    assert all(set(a['fits'])=={'price','flow','stale_flow'} for a in audits if a['status']=='fitted')


def test_missing_or_duplicate_time_never_silently_filled():
    f=fake(250)
    with pytest.raises(ValueError):prepare({s:d.drop(d.index[10]) for s,d in f.items()})
    del f['DOGEUSDT']
    with pytest.raises(ValueError):prepare(f)


def test_evaluation_period_does_not_score_labels_maturing_in_next_period():
    from research.flow_learning.study import information_test
    f=prepare(fake(1100));p,_=forecasts(f)
    a,_=information_test(f,p,'2022-01-01','2022-06-01')
    assert pd.Timestamp(a['last_matured_signal'],tz='UTC')+pd.Timedelta(days=9)<pd.Timestamp('2022-06-01',tz='UTC')
    g=copy.deepcopy(f)
    g.labels[g.dates+pd.Timedelta(days=9)>=pd.Timestamp('2022-06-01',tz='UTC')]=1e9
    b,_=information_test(g,p,'2022-01-01','2022-06-01')
    assert a==b
