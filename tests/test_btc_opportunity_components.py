"""Components only: no claim that an autonomous opportunity controller exists yet."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import math
import numpy as np
import pandas as pd
import pytest
from finruntime.canonical import sha256_id
from finruntime.registry import assert_mode, get_strategy
from finruntime.opportunities import FAMILIES, STRATEGY_ID
from finruntime.opportunities.features import Frame, HOUR, scan, build_frames
from finruntime.opportunities.evidence import qualify, DAY
from finruntime.opportunities.execution import request_for, execute_request, utc
from finruntime.portfolio.accounting import PaperAccountState
from finruntime.operations import PaperCyclePaths

T=1735776000000
MODEL='test-evidence-v1'


def frame(**kw):
    values=dict(time_ms=T,close=100.,atr_hour=.4,atr_day=3.,atr_four=1.,ema20=101.,
        mean20=103.,std20=1.,rsi2=5.,efficiency=.1,daily_up=True,breakout=True)
    values.update(kw)
    return Frame(**values)


def records(n=20):
    out=[]
    for i in range(n):
        t=T-(n-i)*5*DAY
        out.append(dict(trade_id=str(i),family='trend_pullback',model_id=MODEL,
            entry_ms=t-HOUR,exit_ms=t,net_fraction=.02 if i%4 else -.005,fee_fraction=.002))
    return out


def tick():
    return dict(time_ms=T+HOUR,observed_ms=T+HOUR,price=100.,capacity=10.,
                source='synthetic_unit_quote',archive_proxy=True)


def account():
    return PaperAccountState.empty(strategy_id=STRATEGY_ID,as_of_utc=utc(T),starting_cash='1000')


def test_four_distinct_hypotheses_not_four_accounts_of_profit():
    result=scan(frame())
    assert {o.family for o in result}==set(FAMILIES)
    assert len(result)==4 and all(o.stop_fraction>0 and o.hold_hours>0 for o in result)


def test_unhealthy_features_make_no_opportunity():
    assert scan(frame(healthy=False))==[]


def test_daily_regime_blocks_trend_families():
    assert {o.family for o in scan(frame(daily_up=False))}=={'range_rebound'}


def test_nonfinite_or_future_clock_shape_rejected():
    with pytest.raises(ValueError):scan(frame(close=float('nan')))
    with pytest.raises(ValueError):scan(frame(time_ms=T+1))
    with pytest.raises(ValueError):scan(frame(efficiency=2))


def test_missing_history_is_not_a_positive_gate():
    r=qualify([], 'daily_trend',T,MODEL)
    assert not r['eligible'] and r['n']==0 and not r['statistical_proof']


def test_engineering_positive_fixture_can_qualify_not_market_proof():
    r=qualify(records(),'trend_pullback',T,MODEL)
    assert r['eligible'] and r['n']==20 and r['months']>=2
    assert r['conservative_mean']>0 and r['double_fee_mean']>0


def test_future_and_same_tick_closures_cannot_change_prior_gate():
    base=records();x=qualify(base,'trend_pullback',T,MODEL)
    added=[dict(base[0],trade_id='future',entry_ms=T,exit_ms=T+DAY,net_fraction=100.),
           dict(base[0],trade_id='same_tick',entry_ms=T-HOUR,exit_ms=T,net_fraction=100.)]
    assert qualify(base+added,'trend_pullback',T,MODEL)==x


def test_duplicate_or_foreign_model_calibration_rejected():
    with pytest.raises(ValueError):qualify(records()+records()[:1],'trend_pullback',T,MODEL)
    with pytest.raises(ValueError):qualify(records(),'trend_pullback',T,'foreign')


def test_costs_can_remove_apparent_edge():
    r=records()
    for x in r:x.update(net_fraction=.001,fee_fraction=.002)
    result=qualify(r,'trend_pullback',T,MODEL)
    assert not result['eligible'] and 'double_commission_mean_not_positive' in result['reasons']


def test_old_records_expire_and_rolling_evidence_is_bounded():
    r=records(70);x=qualify(r,'trend_pullback',T,MODEL)
    assert x['n']==60
    assert qualify(r,'trend_pullback',T+400*DAY,MODEL)['n']==0


def test_native_runtime_is_paper_only_and_does_not_inherit_old_profit():
    assert_mode(STRATEGY_ID,'paper')
    with pytest.raises(ValueError):assert_mode(STRATEGY_ID,'live')
    assert get_strategy(STRATEGY_ID).parameters['historical_metrics_inherited'] is False


def test_native_buy_and_sell_reconcile_fees_without_another_broker():
    req=request_for(account(),frame(),tick(),1.,'synthetic_entry')
    a,fills,_=execute_request(req)
    assert float(a.spot_positions['BTCUSDT'])==pytest.approx(1)
    assert float(a.cash)==pytest.approx(1000-100.05-.10005)
    later=dict(tick(),time_ms=T+2*HOUR,observed_ms=T+2*HOUR,price=110.)
    b,exits,_=execute_request(request_for(a,frame(),later,0.,'synthetic_exit'))
    assert not b.spot_positions and len(fills)==len(exits)==1
    expected=1000-100.05-.10005+109.945-.109945
    assert float(b.cash)==pytest.approx(expected)
    assert float(b.fees_paid)==pytest.approx(.10005+.109945)


def test_native_partial_execution_does_not_invent_quote_capacity():
    q=dict(tick(),capacity=.2)
    a,fills,outcomes=execute_request(request_for(account(),frame(),q,1.,'synthetic_partial'))
    assert float(a.spot_positions['BTCUSDT'])==pytest.approx(.2)
    assert fills[0]['status']=='partial'


def test_durable_native_cycle_retry_is_exactly_once(tmp_path):
    paths=PaperCyclePaths.under(tmp_path,STRATEGY_ID)
    req=request_for(account(),frame(),tick(),1.,'synthetic_entry')
    first=execute_request(req,paths);second=execute_request(req,paths)
    assert first==second
    stored=PaperAccountState(**json.loads(paths.account_state.read_text()))
    assert stored.account_hash==first[0].account_hash
    assert len(list((paths.root/'cycles').glob('*/COMMITTED.json')))==1


def test_native_state_cannot_be_overwritten_by_a_conflicting_request(tmp_path):
    paths=PaperCyclePaths.under(tmp_path,STRATEGY_ID)
    execute_request(request_for(account(),frame(),tick(),1.,'entry'),paths)
    with pytest.raises(ValueError):
        execute_request(request_for(account(),frame(),dict(tick(),price=101.),2.,'conflicting'),paths)


def test_native_hold_does_not_rebalance_or_add_coins():
    a,_,_=execute_request(request_for(account(),frame(),tick(),1.,'entry'))
    q=dict(tick(),time_ms=T+2*HOUR,observed_ms=T+2*HOUR,price=90.)
    b,fills,_=execute_request(request_for(a,frame(),q,1.,'hold_same_quantity'))
    assert not fills and b.spot_positions==a.spot_positions


def sample_data(n=1600):
    idx=pd.date_range('2024-01-01',periods=n,freq='h',tz='UTC')
    x=np.arange(n);p=100+x*.01+np.sin(x/7)
    return pd.DataFrame(dict(open=p,high=p+.2,low=p-.2,close=p+.01,volume=np.full(n,1000.)),index=idx)


def test_features_and_opportunities_are_prefix_causal():
    d=sample_data();a=build_frames(d);b=build_frames(d.iloc[:1501])
    assert a[:1501]==b
    assert [scan(x) for x in a[:1501]]==[scan(x) for x in b]


def test_missing_hour_resets_indicator_support():
    d=sample_data();d.iloc[1300]=np.nan
    frames=build_frames(d)
    assert not any(f.healthy for f in frames[1325:])


def test_missing_hours_cannot_be_silently_dropped():
    d=sample_data()
    with pytest.raises(ValueError):build_frames(d.drop(d.index[50]))


def test_breakout_pulse_only_at_four_hour_close():
    for f in build_frames(sample_data()):
        if f.breakout:assert datetime.fromtimestamp(f.time_ms/1000,timezone.utc).hour%4==0
