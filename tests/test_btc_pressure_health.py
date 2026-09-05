"""Engineering tests; synthetic paths are not profitability evidence."""
from dataclasses import replace
import numpy as np
import pytest
from research.btc_pressure.feed_health import SourceObservation,quiet_hold
from research.btc_pressure.health_study import HealthTape,HealthReplay,prior_indices,trade_witnesses,audit_ledger
from research.btc_pressure.account_study import BASE,timeline
from test_btc_pressure_account import session,frame


def sources(now=100000):
    return {'binance':SourceObservation('binance',now-100,now-100,True),
            'bybit-spot':SourceObservation('bybit-spot',now-5100,now-30,True)}


def decide(**kw):
    data=dict(now_ms=100000,feature_reason='stale_trade',perp_trade_ms=99900,
              execution_book_ms=99990,execution_max_age_ms=1500,sources=sources(),other_features_ready=True)
    data.update(kw);return quiet_hold(**data)


def test_quote_witnessed_quiet_is_not_entry_permission():
    d=decide();assert d.retain_protected_position and not d.permits_new_entry
    assert d.quiet_sources==('bybit-spot',)


@pytest.mark.parametrize('reason',['stale_book','bar_warmup_or_gap','thin_trade_history','flow_warmup'])
def test_other_missing_features_cannot_be_overridden(reason):
    assert not decide(feature_reason=reason).retain_protected_position


@pytest.mark.parametrize('age',[0,-1,5001,float('nan')])
def test_stale_future_or_unknown_perpetual_cannot_retain(age):
    assert not decide(perp_trade_ms=100000-age).retain_protected_position


@pytest.mark.parametrize('age',[0,-1,5001,float('nan')])
def test_quote_must_be_strictly_prior_and_fresh(age):
    s=sources();s['bybit-spot']=replace(s['bybit-spot'],quote_received_ms=100000-age)
    assert not decide(sources=s).retain_protected_position


def test_periodic_quote_does_not_reset_trade_support():
    s=sources();s['bybit-spot']=replace(s['bybit-spot'],trade_received_ms=40000)
    assert decide(sources=s).reason=='spot_flow_support_expired'


def test_bad_quote_invalidates_prior_good_witness():
    s=sources();s['bybit-spot']=replace(s['bybit-spot'],quote_valid=False)
    assert not decide(sources=s).retain_protected_position


def test_perpetual_or_wrong_spot_cannot_substitute():
    s=sources();s['bybit-spot']=replace(s['bybit-spot'],source='bybit_perp')
    assert not decide(sources=s).retain_protected_position


def test_missing_source_is_unknown_not_healthy():
    s=sources();del s['bybit-spot']
    assert not decide(sources=s).retain_protected_position


def test_stale_execution_book_always_blocks():
    assert not decide(execution_book_ms=98000).retain_protected_position


def test_later_quote_never_changes_earlier_witness():
    a=np.array([10,999999,1000000,1100000]);t=[1000]
    assert prior_indices(a,t)[0]==1
    assert prior_indices(np.r_[a,2000000],t)[0]==1


def test_reordered_clock_rejected():
    with pytest.raises(ValueError):prior_indices([20,10],[1])


def test_late_trade_not_backfilled_as_fresh_observation():
    a=trade_witnesses(np.array([1000000,10000000]),np.array([900000,1000000]),[11000])
    assert a[0]==1000.


def synthetic_tape(s,witness=True):
    times=np.array([x[0] for x in s.trace]);perp=times-100
    return HealthTape(times,perp,{'binance':times-100.,'bybit-spot':times-5100.},
        {'binance':times-30.,'bybit-spot':times-30.},
        {'binance':np.ones(len(times),bool),'bybit-spot':np.full(len(times),witness,bool)}, {}, np.ones(len(times),bool))


def quiet_session():
    s=session();s.trace.insert(1,(100500,None,'stale_trade'))
    s.timeline_type,s.timeline_index,s.timeline_time=timeline(s.book_local,s.trade_local,s.ticker_local,[x[0] for x in s.trace])
    return s


def test_retained_position_still_exits_on_unchanged_stop():
    s=quiet_session();r=HealthReplay(s,synthetic_tape(s),policy='quote_witnessed_hold').run()
    assert r['retained_position_count']==1
    req=[e for e in r['events'] if e['type']=='exit_requested']
    assert req[0]['reason']=='stop'
    assert r['completed_trade_count']==1 and r['closed_trades'][0]['net']<0
    assert audit_ledger(r)['cashflows_reconciled']


def test_original_control_remains_original_exit():
    s=quiet_session();r=HealthReplay(s,synthetic_tape(s),policy='original').run()
    assert r['retained_position_count']==0
    req=[e for e in r['events'] if e['type']=='exit_requested']
    assert req[0]['reason']=='feature_quality_lost'


def test_no_quote_witness_falls_back_to_original_exit():
    s=quiet_session();r=HealthReplay(s,synthetic_tape(s,False),policy='quote_witnessed_hold').run()
    assert r['retained_position_count']==0
    assert next(e for e in r['events'] if e['type']=='exit_requested')['reason']=='feature_quality_lost'


def test_silence_does_not_generate_entry_or_add_order():
    s=quiet_session();r=HealthReplay(s,synthetic_tape(s),policy='quote_witnessed_hold').run()
    assert r['submitted_orders']==1 and r['raw_signals']==1
    assert any(e['type']=='cancel_requested' for e in r['events'])


def test_same_fills_commission_stress_retains_losses():
    s=quiet_session();r=HealthReplay(s,synthetic_tape(s)).run()
    assert r['same_fills_double_commission_net']==pytest.approx(r['net_closed']-r['fees_closed'])
    assert not r['same_fills_stress_is_executable_account']


def test_expired_signal_cannot_be_rescued_by_more_quotes():
    s=quiet_session();t=synthetic_tape(s);t.trade_ms['bybit-spot'][:]-=60000
    r=HealthReplay(s,t,policy='quote_witnessed_hold').run()
    assert r['retained_position_count']==0


def test_tape_at_requires_exact_frame_time():
    s=quiet_session();t=synthetic_tape(s)
    with pytest.raises(ValueError):t.at(42)


def test_no_annual_performance_claim():
    s=quiet_session();r=HealthReplay(s,synthetic_tape(s),policy='quote_witnessed_hold').run()
    assert r['cagr_pct'] is None and not r['target_achieved'] and not r['live_ready']
    assert not r['quote_witness_is_trade_completeness_proof']


def test_trade_inactivity_reason_cannot_mask_broken_bar_history():
    assert not decide(other_features_ready=False).retain_protected_position


def test_unknown_secondary_prerequisites_fail_closed():
    s=quiet_session();t=synthetic_tape(s);t.other_features_ready=None
    r=HealthReplay(s,t,policy='quote_witnessed_hold').run()
    assert r['retained_position_count']==0


def test_missing_witness_is_json_safe_and_not_a_hold():
    import json
    s=quiet_session();t=synthetic_tape(s);t.quote_ms['bybit-spot'][:]=np.nan
    r=HealthReplay(s,t,policy='quote_witnessed_hold').run()
    assert r['retained_position_count']==0
    json.dumps(r,allow_nan=False)
