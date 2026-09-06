"""Execution-boundary regressions; no autonomous portfolio claim."""
import pytest
from dataclasses import replace
from finruntime.opportunities.execution import request_for,execute_request
from finruntime.opportunities.features import HOUR
from test_btc_opportunity_components import frame,tick,account,T


@pytest.mark.parametrize('patch',[
    {'observed_ms':T+HOUR+1},
    {'observed_ms':T+HOUR-30001},
    {'time_ms':T+HOUR-1,'observed_ms':T+HOUR-1},
    {'quality':'stale'},
    {'price':float('nan')},
    {'price':0},
    {'capacity':-1},
    {'source':''},
])
def test_invalid_quote_refused_before_native_execution(patch):
    with pytest.raises(ValueError):request_for(account(),frame(),dict(tick(),**patch),1.,'invalid')


def test_quarter_allocation_is_enforced_not_just_registry_metadata():
    with pytest.raises(ValueError):request_for(account(),frame(),tick(),3.,'too_large')


def test_existing_position_cannot_be_averaged_up_or_down():
    a,_,_=execute_request(request_for(account(),frame(),tick(),1.,'entry'))
    q=dict(tick(),time_ms=T+2*HOUR,observed_ms=T+2*HOUR,price=90.)
    with pytest.raises(ValueError):request_for(a,frame(),q,1.1,'averaging')


def test_entry_requires_healthy_and_recent_feature_support():
    with pytest.raises(ValueError):request_for(account(),frame(healthy=False),tick(),1.,'invalid')
    q=dict(tick(),time_ms=T+4*HOUR,observed_ms=T+4*HOUR)
    with pytest.raises(ValueError):request_for(account(),frame(),q,1.,'expired')


def test_fresh_quote_can_reduce_exposure_with_lost_features():
    a,_,_=execute_request(request_for(account(),frame(),tick(),1.,'entry'))
    q=dict(tick(),time_ms=T+2*HOUR,observed_ms=T+2*HOUR)
    b,fills,_=execute_request(request_for(a,frame(healthy=False),q,0.,'quality_exit'))
    assert not b.spot_positions and len(fills)==1


def test_entry_lot_grid_and_minimum_are_checked():
    with pytest.raises(ValueError):request_for(account(),frame(),tick(),.000001,'below_min')
    with pytest.raises(ValueError):request_for(account(),frame(),tick(),1.000001,'bad_step')


def test_costs_must_be_finite_and_nonnegative():
    with pytest.raises(ValueError):request_for(account(),frame(),tick(),1.,'invalid',fee_bps=-1)
    with pytest.raises(ValueError):request_for(account(),frame(),tick(),1.,'invalid',slip_bps=float('nan'))
