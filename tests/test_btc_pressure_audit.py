"""Regression fixtures for failures found during the annual-evidence audit."""
from dataclasses import replace
import pytest
from research.btc_pressure.adapters import Event
from research.btc_pressure.paper import Settings
from test_btc_pressure import broker, opened, book_event, proposal


def test_lower_base_risk_does_not_increase_at_deeper_drawdown():
    b=broker(settings=Settings(risk=.0005))
    b.cash=960.;at_four=b.risk_fraction()
    b.cash=940.;at_six=b.risk_fraction()
    assert at_six<=at_four


def test_ttl_ack_does_not_depend_on_receiving_event_at_expiry():
    b=broker();assert b.propose(proposal(passive=True))
    b.on_event(book_event(100250))
    b.on_event(book_event(115300))
    assert b.pending is None


def test_delayed_settlement_debits_position_held_at_event_not_current_position():
    b=opened();q=b.position['qty'];b.next_funding=100500
    b.on_event(book_event(100500));b.request_exit('test')
    b.on_event(book_event(100750));assert b.position is None
    before=b.cash
    b.on_event(Event(100900,100500,'bybit_perp','funding',dict(mark=100.,rate=.001)))
    assert b.cash==pytest.approx(before-q*.1)
    assert b.trades[0]['funding']==pytest.approx(q*.1)
    assert b.cash==pytest.approx(b.s.capital+sum(t['net'] for t in b.trades))


def test_funding_duplicate_does_not_charge_twice():
    b=opened();e=Event(100300,100300,'bybit_perp','funding',dict(mark=100.,rate=.001))
    b.on_event(e);cash=b.cash;b.on_event(e)
    assert b.cash==cash


def test_conflicting_funding_duplicate_blocks_evidence():
    b=opened();b.on_event(Event(100300,100300,'bybit_perp','funding',dict(mark=100.,rate=.001)))
    b.on_event(Event(100301,100300,'bybit_perp','funding',dict(mark=100.,rate=.002)))
    assert b.incomplete and b.halted


def test_funding_uses_quantity_before_partial_close():
    b=opened();q=b.position['qty'];b.next_funding=100500
    b.on_event(book_event(100500));b.request_exit('test')
    b.on_event(book_event(100750,qty=.1));assert b.position['qty']==pytest.approx(q-.1)
    before=b.cash;b.on_event(Event(100900,100500,'bybit_perp','funding',dict(mark=100.,rate=.001)))
    assert b.cash==pytest.approx(before-q*.1)


def test_unresolved_settlement_blocks_new_position_and_debits_old_trade():
    b=opened();q=b.position['qty'];b.next_funding=100500
    b.on_event(book_event(100500));b.request_exit('test');b.on_event(book_event(100750))
    b.next_funding=28800000;b.on_event(book_event(161000))
    assert not b.propose(proposal(161000,side=-1));b.on_event(book_event(161250))
    assert b.position is None
    b.on_event(Event(161300,100500,'bybit_perp','funding',dict(mark=100.,rate=.001)))
    assert b.trades[0]['funding']==pytest.approx(q*.1) and b.position is None
    assert b.report()['ledger_reconciled']


def test_missing_settlement_remains_unresolved_after_position_closes():
    b=opened();b.next_funding=100500;b.on_event(book_event(100500))
    b.request_exit('test');b.on_event(book_event(100750))
    assert b.report()['execution_incomplete'] and b.report()['unresolved_funding']==[100500]


def test_closed_seconds_never_use_next_second_trade():
    import pandas as pd
    from research.btc_pressure.event_study import buckets
    a=pd.DataFrame(dict(timestamp=[1,999999],local_timestamp=[1,999999],side=['buy','sell'],price=[100.,100.],amount=[2.,1.]))
    b=pd.concat([a,pd.DataFrame(dict(timestamp=[1000000],local_timestamp=[1000000],side=['sell'],price=[50.],amount=[100.]))])
    x=buckets(a,0,n=5);y=buckets(b,0,n=5)
    assert x['flow10'][0]==y['flow10'][0] and x['flow10'][0]==pytest.approx(1/3)


def test_late_trade_is_not_backfilled_into_confirmed_bar():
    import pandas as pd
    from research.btc_pressure.event_study import confirmed_bars
    a=pd.DataFrame(dict(timestamp=[1,1000000],local_timestamp=[1,9000000],price=[100.,999.]))
    b=confirmed_bars(a,0)
    assert b.iloc[0]['max']==100.


def test_normalized_liquidation_sell_is_already_sell_pressure():
    import pandas as pd
    from research.btc_pressure.event_study import buckets
    a=pd.DataFrame(dict(timestamp=[1],local_timestamp=[1],side=['sell'],price=[100.],amount=[1000.]))
    assert buckets(a,0,n=5)['flow10'][0]==-1.


def test_signal_markouts_include_spread_fees_and_slippage():
    import pandas as pd
    from research.btc_pressure.event_study import markouts
    book=pd.DataFrame(dict(local_timestamp=[100250000,130500000,400500000,1900500000],**{'bids[0].price':[99.99]*4,'asks[0].price':[100.01]*4}))
    ticker=pd.DataFrame(dict(funding_timestamp=[28800000000]))
    r=markouts([proposal()],book,ticker)
    assert len(r)==3 and all(x['net_bps']<-13 for x in r)


def test_markout_crossing_funding_is_unpriced():
    import pandas as pd
    from research.btc_pressure.event_study import markouts
    book=pd.DataFrame(dict(local_timestamp=[100250000,130500000,400500000,1900500000],**{'bids[0].price':[100.]*4,'asks[0].price':[100.1]*4}))
    ticker=pd.DataFrame(dict(funding_timestamp=[120000000]))
    assert all(r['status']=='funding_unpriced' for r in markouts([proposal()],book,ticker))


def valid_gate():
    from research.btc_pressure.paper import model_fingerprint
    return dict(schema='btc-pressure-gate-v2',model_sha256=model_fingerprint(),
                venue='bybit_perp',settings_sha256=Settings().fingerprint(),training_end_ms=99999,
                synthetic=False,cells={'cascade:1':dict(trades=200,days=30,lower_mean_daily_r=.1)})


def test_calibration_is_bound_to_exact_model():
    from research.btc_pressure.paper import Gate
    a=valid_gate();assert Gate(a).allows(proposal(),'bybit_perp',Settings())[0]
    a['model_sha256']='stale-model'
    assert not Gate(a).allows(proposal(),'bybit_perp',Settings())[0]


@pytest.mark.parametrize('value',[float('inf'),float('nan'),'0.5',True])
def test_nonfinite_or_wrong_type_calibration_cannot_admit(value):
    from research.btc_pressure.paper import Gate
    a=valid_gate();a['cells']['cascade:1']['lower_mean_daily_r']=value
    assert not Gate(a).allows(proposal(),'bybit_perp',Settings())[0]


def test_partial_endpoints_cannot_establish_a_year():
    from research.btc_pressure.event_study import DATES
    import datetime as dt
    # Spanning 365 days by endpoints is not the same as observing those days.
    times=[dt.date.fromisoformat(x) for x in DATES]
    assert (max(times)-min(times)).days>=365 and len(times)==3


def test_late_funding_correction_does_not_certify_historical_drawdown():
    b=opened();b.on_event(Event(100900,100500,'bybit_perp','funding',dict(mark=100.,rate=.001)))
    assert b.report()['funding_history_revised']
    assert not b.report()['funding_time_drawdown_verified']
