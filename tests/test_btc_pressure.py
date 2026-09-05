"""Synthetic engineering fixtures, NOT evidence of market profitability."""
from dataclasses import replace
import gzip
import hashlib
import json
import math
import random
import pytest
from research.btc_pressure.adapters import Book,Event,Normalizer,number
from research.btc_pressure.strategy import Features,Frame,Mechanisms,Proposal
from research.btc_pressure.paper import Broker,Gate,Settings,round_step,model_fingerprint
from research.btc_pressure.run import fit_gate,replay


def envelope(p,source='bybit_perp',seq=1,t=100000,kind='message'):
    return dict(seq=seq,received_ms=t,source=source,kind=kind,payload=p)


def liquidation(side):
    return dict(topic='allLiquidation.BTCUSDT',type='snapshot',ts=100000,
                data=[dict(T=99999,s='BTCUSDT',S=side,v='2',p='999')])


@pytest.mark.parametrize('side,expected',[('Buy',-1),('Sell',1)])
def test_bybit_liquidation_position_side_not_order_side(side,expected):
    e=Normalizer().feed(envelope(liquidation(side)))[-1]
    assert e.kind=='liquidation' and e.data['side']==expected
    assert e.data['price_kind']=='bankruptcy_not_execution' and 'price' not in e.data


@pytest.mark.parametrize('side,expected',[('SELL',-1),('BUY',1)])
def test_binance_liquidations_are_sampled_executed_quantity(side,expected):
    p=dict(e='forceOrder',E=100000,o=dict(T=99999,S=side,q='4',z='1',ap='100',p='90'))
    e=Normalizer().feed(envelope(p,'binance_perp'))[-1]
    assert e.data['coverage']=='sampled_1s' and e.data['qty']==1 and e.data['side']==expected


def test_reject_unknown_liquidation_side():
    with pytest.raises(ValueError):Normalizer().feed(envelope(liquidation('bad')))


def test_trade_dedup_and_receive_order():
    n=Normalizer();p=dict(e='aggTrade',s='BTCUSDT',a=5,m=True,p='100',q='1',T=99999)
    assert n.feed(envelope(p,'binance_spot'))[-1].data['side']==-1
    assert all(e.kind!='trade' for e in n.feed(envelope(p,'binance_spot',2)))
    with pytest.raises(ValueError):n.feed(envelope(p,'binance_spot',4))


def test_receipt_time_cannot_reverse():
    n=Normalizer();n.feed(envelope({},kind='heartbeat'))
    with pytest.raises(ValueError):n.feed(envelope({},seq=2,t=99999,kind='heartbeat'))


def test_trade_boolean_mandatory():
    p=dict(e='aggTrade',a=1,m='false',p='100',q='1',T=99999)
    with pytest.raises(ValueError):Normalizer().feed(envelope(p,'binance_spot'))


@pytest.mark.parametrize('x',[float('nan'),float('inf'),float('-inf')])
def test_nonfinite_rejected(x):
    with pytest.raises(ValueError):number(x)


def snapshot():return dict(u=10,seq=100,b=[['99','2'],['98','3']],a=[['101','4']])


def test_delta_before_snapshot_rejected():
    with pytest.raises(ValueError):Book().apply(snapshot(),False)


def test_book_absolute_delta_and_delete():
    b=Book();b.apply(snapshot(),True)
    b.apply(dict(u=15,seq=170,b=[['99','1'],['98','0']],a=[]),False)
    assert b.bids=={99.:1.}


def test_nonconsecutive_sequence_is_legal():
    b=Book();b.apply(snapshot(),True)
    assert b.apply(dict(u=500,seq=2000,b=[],a=[]),False)


def test_reversed_book_invalidates():
    b=Book();b.apply(snapshot(),True)
    with pytest.raises(ValueError):b.apply(dict(u=9,seq=90,b=[],a=[]),False)
    assert not b.valid


def test_new_snapshot_replaces_previous_levels():
    b=Book();b.apply(snapshot(),True)
    b.apply(dict(u=1,seq=1,b=[['90','1']],a=[['91','1']]),True)
    assert b.bids=={90.:1.} and b.asks=={91.:1.}


def test_crossed_book_rejected():
    with pytest.raises(ValueError):Book().apply(dict(u=1,b=[['101','1']],a=[['100','1']]),True)


def test_rest_preload_excludes_unclosed_minute():
    rows=[['0','100','101','99','100','1','100'],['60000','100','101','99','100','1','100']]
    p=dict(retCode=0,result=dict(list=rows))
    es=Normalizer().feed(envelope(p,'bybit_perp_bars',t=90000,kind='rest'))
    assert len(es)==1 and es[0].occurred==60000 and es[0].received==90000


def test_future_bar_rejected():
    e=Event(59000,60000,'bybit_perp','bar',dict(end=60000))
    with pytest.raises(ValueError):Features().add(e)


def test_missing_bars_reset_warmup():
    f=Features()
    for end in [60000,120000,240000]:f.add(Event(end,end,'bybit_perp','bar',dict(end=end)))
    assert len(f.bars['bybit_perp'])==1


def test_predicted_funding_not_normalized_as_payment():
    p=dict(e='markPriceUpdate',E=100000,p='100',r='.0001',T=28800000)
    events=Normalizer().feed(envelope(p,'binance_perp'))
    assert events[-1].kind=='context' and not any(e.kind=='funding' for e in events)


def test_settlement_requires_realized_price_attestation():
    with pytest.raises(ValueError):Normalizer().feed(envelope(dict(time=100000,rate=.001,mark=100),'bybit_perp',kind='settlement'))


def test_missing_spot_source_is_not_zero_flow():
    f=Features();f.started=0
    frame,reason=f.frame(600000)
    assert frame is None and reason=='missing_or_stale_trade_source'


def frame(t=100000,**kw):
    fields=dict(time=t,price=100.,atr=.2,range_high=100.,range_low=99.,consensus=0,spot_bias=0.,perp_flow=0.,
                bid_refill=1.,ask_refill=1.,high10=100.1,low10=99.9,prior_high10=100.1,prior_low10=99.9,liquidation_side=0)
    fields.update(kw);return Frame(**fields)


def mirror(f):
    return replace(f,price=200-f.price,range_high=200-f.range_low,range_low=200-f.range_high,
       high10=200-f.low10,low10=200-f.high10,prior_high10=200-f.prior_low10,prior_low10=200-f.prior_high10,
       consensus=-f.consensus,spot_bias=-f.spot_bias,perp_flow=-f.perp_flow,liquidation_side=-f.liquidation_side,
       bid_refill=f.ask_refill,ask_refill=f.bid_refill)


@pytest.mark.parametrize('mirrored',[False,True])
def test_spot_trend_requires_breakout_retest_and_reclaim(mirrored):
    frames=[frame(price=100.1,consensus=1),frame(101000,price=100.02,consensus=1),frame(102000,price=100.1,consensus=1)]
    m=Mechanisms();out=[m.on_frame(mirror(f) if mirrored else f) for f in frames]
    assert out[:2]==[None,None] and out[-1].family=='spot_trend' and out[-1].side==(-1 if mirrored else 1)
    assert out[-1].passive


@pytest.mark.parametrize('mirrored',[False,True])
def test_cascade_waits_for_failed_rebound(mirrored):
    frames=[frame(liquidation_side=-1),frame(101000,price=99.,perp_flow=-.8,bid_refill=.5),
            frame(102000,price=99.15,perp_flow=-.8,bid_refill=.5),frame(103000,price=99.04,perp_flow=-.8,bid_refill=.5)]
    m=Mechanisms();out=[m.on_frame(mirror(f) if mirrored else f) for f in frames]
    assert all(x is None for x in out[:-1])
    assert out[-1].family=='cascade' and out[-1].side==(1 if mirrored else -1)


@pytest.mark.parametrize('mirrored',[False,True])
def test_absorption_requires_cascade_stall_refill_then_reclaim(mirrored):
    frames=[frame(liquidation_side=-1),
        frame(101000,price=99.,perp_flow=-.8,low10=98.9,prior_low10=99.,bid_refill=.5),
        frame(102000,price=98.98,perp_flow=-.8,low10=98.98,prior_low10=98.9,high10=99.1,bid_refill=1.5),
        frame(103000,price=99.14,perp_flow=-.5,spot_bias=.3,bid_refill=1.5)]
    m=Mechanisms();out=[m.on_frame(mirror(f) if mirrored else f) for f in frames]
    assert all(x is None for x in out[:-1])
    assert out[-1].family=='absorption' and out[-1].side==(-1 if mirrored else 1)


def test_no_falling_knife_without_cascade():
    assert Mechanisms().on_frame(frame(price=90,perp_flow=-.9,bid_refill=2,spot_bias=.5)) is None


def test_future_frames_do_not_change_prefix():
    fs=[frame(),frame(101000,liquidation_side=-1),frame(102000,price=99)]
    a,b=Mechanisms(),Mechanisms()
    x=[a.on_frame(f) for f in fs]
    y=[b.on_frame(f) for f in fs];b.on_frame(frame(103000,price=200))
    assert x==y


def test_frame_reversal_rejected():
    m=Mechanisms();m.on_frame(frame())
    with pytest.raises(ValueError):m.on_frame(frame())


@pytest.mark.parametrize('kwargs',[dict(capital=0),dict(risk=.1),dict(exposure=5),dict(slip=-1),dict(taker_fee=math.nan),dict(latency_ms=0)])
def test_settings_validation(kwargs):
    with pytest.raises(ValueError):Settings(**kwargs)


def book_event(t,bid=99.99,ask=100.01,qty=100.,source='bybit_perp',seq=None):
    return Event(t,t,source,'book',dict(bids=[(bid,qty)],asks=[(ask,qty)],sequence=t if seq is None else seq))


def broker(mode='diagnostic',settings=None):
    b=Broker(settings or Settings(),mode=mode)
    b.on_event(Event(100000,100000,'bybit_perp','instrument',dict(tick=.01,qty_step=.001,min_qty=.001,min_notional=1.)))
    b.on_event(Event(100000,100000,'bybit_perp','context',dict(next_funding=28800000)))
    b.on_event(book_event(100000))
    return b


def proposal(t=100000,passive=False,side=1,family='cascade'):
    return Proposal(t,family,side,100.,99. if side==1 else 101.,104. if side==1 else 96.,300000,passive,'synthetic engineering fixture')


def opened(side=1):
    b=broker();assert b.propose(proposal(side=side))
    b.on_event(book_event(100250));assert b.position
    return b


def test_observe_and_uncalibrated_modes_cannot_trade():
    for mode in ('observe','calibrated'):
        b=broker(mode);assert not b.propose(proposal()) and b.pending is None


def test_calibration_identity_future_and_synthetic_rejected():
    s=Settings();p=proposal();a=dict(schema='btc-pressure-gate-v2',model_sha256=model_fingerprint(),venue='bybit_perp',settings_sha256=s.fingerprint(),training_end_ms=99999,
         synthetic=False,cells={'cascade:1':dict(trades=200,days=30,lower_mean_daily_r=.1)})
    assert Gate(a).allows(p,'bybit_perp',s)[0]
    for changes in (dict(venue='binance_perp'),dict(training_end_ms=100000),dict(synthetic=True),dict(settings_sha256='bad')):
        assert not Gate(a|changes).allows(p,'bybit_perp',s)[0]


def test_latency_and_ioc_partial_entry():
    b=broker();b.propose(proposal());b.on_event(book_event(100249,qty=.5));assert not b.position
    b.on_event(book_event(100250,qty=.5))
    assert b.position['qty']==pytest.approx(.5) and b.pending is None


def test_no_fill_on_other_venue():
    b=broker();b.propose(proposal());b.on_event(book_event(100500,source='binance_perp'))
    assert b.position is None


def test_no_touch_fill_and_post_only_rejection():
    b=broker();b.propose(proposal(passive=True));b.on_event(book_event(100250))
    assert b.position is None
    b.on_event(Event(100300,100300,'bybit_perp','trade',dict(side=1,price=99.99,qty=200)))
    assert b.position is None
    c=broker();c.propose(proposal(passive=True));c.on_event(book_event(100250,bid=99.8,ask=99.9))
    assert c.pending is None and c.position is None


def test_queue_and_participation_and_partial_protection():
    b=broker();b.on_event(book_event(100000,qty=.1));b.propose(proposal(passive=True));b.on_event(book_event(100250,qty=.1))
    b.on_event(Event(100300,100300,'bybit_perp','trade',dict(side=-1,price=99.99,qty=.1)))
    assert b.position is None
    b.on_event(Event(100400,100400,'bybit_perp','trade',dict(side=-1,price=99.99,qty=1.)))
    assert b.position['qty']==pytest.approx(.01) and b.position['stop']==99


def test_cancel_race_fills_before_ack_but_not_after():
    b=broker(settings=Settings(participation=1.));b.on_event(book_event(100000,qty=.1));b.propose(proposal(passive=True))
    b.on_event(book_event(100250,qty=.1));b.cancel('test')
    b.on_event(Event(100300,100300,'bybit_perp','trade',dict(side=-1,price=99.99,qty=.5)))
    assert b.position['qty']==pytest.approx(.4)
    b.on_event(Event(100500,100500,'bybit_perp','trade',dict(side=-1,price=99.99,qty=5.)))
    assert b.position['qty']==pytest.approx(.4) and b.pending is None


def test_stop_executes_on_future_book_not_stale_stop_price():
    b=opened();b.on_event(book_event(100500,bid=98,ask=98.01))
    assert b.position is not None and b.exit_order
    b.on_event(book_event(100750,bid=97.5,ask=97.51))
    assert b.position is None and b.trades[-1]['reason']=='stop'
    assert b.trades[-1]['net']<0 and b.report()['ledger_reconciled']


def test_displayed_depth_cannot_be_reused_on_duplicate_or_unrelated_update():
    b=opened();original=b.position['qty'];b.request_exit('test')
    b.on_event(book_event(100500,qty=.1,seq=1))
    left=b.position['qty'];assert left==pytest.approx(original-.1)
    b.on_event(book_event(100501,qty=.1,seq=1));assert b.position['qty']==left
    b.on_event(book_event(100502,qty=.1,seq=2));assert b.position['qty']==left
    b.on_event(book_event(100503,qty=.3,seq=3));assert b.position['qty']==pytest.approx(left-.2)


def test_stale_quote_never_generates_fictional_exit():
    b=opened();b.on_event(Event(103000,103000,'bybit_perp','health',{}))
    assert b.incomplete and b.position and b.report()['marked_equity'] is None
    b.on_event(book_event(103250));assert b.position is None


def test_promote_no_added_risk_or_quantity():
    b=opened();qty=b.position['qty'];b.propose(proposal(100250,family='spot_trend'))
    assert b.position['qty']==qty and b.position['family']=='spot_trend' and b.pending is None


def test_opposite_signal_closes_not_instant_flips():
    b=opened();b.propose(proposal(100250,side=-1));assert b.exit_order and b.position['side']==1
    b.on_event(book_event(100500));assert b.position is None and b.pending is None


def test_no_averaging_and_exposure_sizing():
    b=opened();q=b.position['qty'];assert not b.propose(proposal(100250))
    assert b.position['qty']==q and q*b.position['entry']<=b.s.capital*2


def test_realized_funding_and_fees_reconcile():
    b=opened();q=b.position['qty'];cash=b.cash
    b.on_event(Event(100300,100300,'bybit_perp','funding',dict(mark=100.,rate=.001)))
    assert b.cash==pytest.approx(cash-q*.1)
    b.request_exit('test');b.on_event(book_event(100550))
    assert b.cash==pytest.approx(b.s.capital+sum(t['net'] for t in b.trades))
    assert b.report()['ledger_reconciled']


def test_predicted_funding_does_not_charge_cash():
    b=opened();cash=b.cash
    b.on_event(Event(100300,100300,'bybit_perp','context',dict(predicted_funding=.1)))
    assert b.cash==cash


def test_missing_settlement_fails_closed():
    b=opened();b.next_funding=100500
    b.on_event(book_event(105501))
    assert b.incomplete and b.exit_order and 100500 in b.unresolved_funding


def test_drawdown_delevers_and_halts_without_restart():
    b=broker();b.cash=960.;assert b.risk_fraction()==.00125
    b.cash=940.;assert b.risk_fraction()==.001
    b.cash=920.;b.circuit();assert b.halted
    b.on_event(Event(864100000,864100000,'bybit_perp','health',{}));assert b.halted


def test_report_never_annualizes_or_claims_live_ready():
    r=opened().report()
    assert r['cagr_pct'] is None and r['live_ready'] is False and r['annual_target_established'] is False


def test_proposal_invalid_stop_rejected():
    with pytest.raises(ValueError):replace(proposal(),stop=101.)


def test_decimal_rounding_does_not_round_up_risk():
    assert round_step(.019999,.001)==.019 and round_step(100.005,.01,True)==100.01


def test_raw_checksum_tamper_rejected(tmp_path):
    data=tmp_path/'in';data.mkdir();(data/'raw.jsonl.gz').write_bytes(b'wrong')
    (data/'manifest.json').write_text(json.dumps(dict(raw_sha256='bad')))
    with pytest.raises(ValueError,match='checksum'):replay(data,tmp_path/'out')


def test_real_capture_parser_and_gap_fail_closed(tmp_path):
    data=tmp_path/'input';data.mkdir();raw=data/'raw.jsonl.gz'
    rows=[envelope({},kind='connected'),envelope({},seq=2,t=100100,kind='disconnect')]
    with gzip.open(raw,'wt') as f:
        for row in rows:f.write(json.dumps(row)+'\n')
    manifest=dict(schema='btc-pressure-raw-v1',records=2,start_ms=100000,end_ms=100100,errors=[],
                  raw_sha256=hashlib.sha256(raw.read_bytes()).hexdigest())
    (data/'manifest.json').write_text(json.dumps(manifest))
    r=replay(data,tmp_path/'out','diagnostic')
    assert r['closed_trades']==[] and r['annual_test_complete'] is False and r['parse_errors']==[]


def test_gate_rejects_synthetic_and_overlapping_evidence(tmp_path):
    p=tmp_path/'r.json';p.write_text(json.dumps(dict(venue='bybit_perp',settings_sha256='x',input_start_ms=0,
        input_end_ms=1000,synthetic=True)))
    with pytest.raises(ValueError):fit_gate([p],tmp_path/'gate.json')
    with pytest.raises(ValueError,match='Overlapping'):fit_gate([p,p],tmp_path/'gate.json')


@pytest.mark.parametrize('seed',range(12))
def test_random_price_paths_preserve_cash_ledger(seed):
    rng=random.Random(seed);b=opened(side=1 if seed%2 else -1);price=100.
    for i in range(1,50):
        price=max(90.,min(110.,price+rng.uniform(-.4,.4)))
        b.on_event(book_event(100250+i*500,bid=price-.01,ask=price+.01))
        b.report()
    if b.position:
        b.request_exit('test_end');b.on_event(book_event(b.now+500,bid=price-.01,ask=price+.01))
    r=b.report();assert r['ledger_reconciled']
    assert b.cash==pytest.approx(b.s.capital+sum(t['net'] for t in b.trades))


def test_foreign_stream_still_advances_protective_clock():
    b=opened();b.on_event(Event(103000,103000,'bybit_spot','health',{}))
    assert b.incomplete and b.exit_order and b.position


def test_unknown_symbol_book_cannot_contaminate_btc():
    p=dict(topic='orderbook.50.ETHUSDT',type='snapshot',data=snapshot())
    with pytest.raises(ValueError):Normalizer().feed(envelope(p))


def test_late_old_trade_cannot_fill_new_maker_order():
    b=broker();b.on_event(book_event(100000,qty=.01));b.propose(proposal(passive=True));b.on_event(book_event(100250,qty=.01))
    b.on_event(Event(100300,99999,'bybit_perp','trade',dict(side=-1,price=99.99,qty=10)))
    assert b.position is None


def test_insufficient_calibration_counts_fail_closed():
    s=Settings();p=proposal()
    a=dict(venue='bybit_perp',settings_sha256=s.fingerprint(),training_end_ms=99999,synthetic=False,
           cells={'cascade:1':dict(trades=199,days=29,lower_mean_daily_r=100)})
    assert not Gate(a).allows(p,'bybit_perp',s)[0]


def test_more_expensive_execution_does_not_increase_sized_quantity():
    a=broker();b=broker(settings=Settings(taker_fee=.001,slip=.0002))
    assert a.propose(proposal()) and b.propose(proposal())
    assert b.pending['remaining']<=a.pending['remaining']


def test_initial_risk_includes_fees():
    b=broker();assert b.propose(proposal())
    o=b.pending;p=o['proposal'];price=o['limit']
    cost=price*(2*b.s.taker_fee+2*b.s.slip)
    assert o['remaining']*((price-p.stop)+cost)<=b.cash*b.s.risk+1e-9


def test_unfilled_ioc_does_not_become_market_order():
    b=broker();b.propose(proposal());b.on_event(book_event(100250,bid=110,ask=110.01))
    assert b.pending is None and b.position is None


def test_ttl_cancel_ack_clears_passive_remainder():
    b=broker();b.propose(proposal(passive=True));b.on_event(book_event(100250))
    b.on_event(Event(115000,115000,'bybit_spot','health',{}));assert b.pending['cancel_at']==115250
    b.on_event(Event(115250,115250,'bybit_spot','health',{}));assert b.pending is None


def test_short_funding_credit_has_correct_sign():
    b=opened(-1);before=b.cash
    b.on_event(Event(100300,100300,'bybit_perp','funding',dict(mark=100.,rate=.001)))
    assert b.cash>before and b.position['funding']<0


def test_frame_state_resets_after_gap():
    m=Mechanisms();m.on_frame(frame(liquidation_side=-1));assert m.cascade is not None
    m.reset();assert m.cascade is None and m.trend is None


def ready_features():
    f=Features();now=7200000
    for end in range(now-60*60000,now+1,60000):
        f.add(Event(now,end,'bybit_perp','bar',dict(end=end,open=100,high=100.1,low=99.9,close=100,volume=1,quote_volume=100)))
    for sec in range(-610,1):
        t=now+sec*1000
        for source in ('binance_spot','bybit_spot','bybit_perp'):
            f.add(Event(t,t,source,'health',{'liquidations':True}))
            f.add(Event(t,t,source,'trade',dict(price=100.,qty=1.,side=1 if sec%2 else -1)))
    f.add(book_event(now))
    for source in ('binance_spot','bybit_spot'):f.ratios[source].extend([0.]*30)
    return f,now


def test_complete_features_require_both_spot_confirmations():
    f,now=ready_features()
    for source in ('binance_spot','bybit_spot'):
        f.add(Event(now,now,source,'trade',dict(price=100,qty=1000,side=1)))
    a,status=f.frame(now)
    assert status=='ready' and a.consensus==1 and a.atr==pytest.approx(.2)
    f.add(Event(now+1,now+1,'bybit_spot','gap',{}))
    assert f.frame(now+1)[0] is None


def test_feature_liquidations_are_local_not_cross_venue_sums():
    f,now=ready_features()
    f.add(Event(now,now,'binance_perp','liquidation',dict(reference_price=100,qty=2000,side=-1)))
    a,_=f.frame(now);assert a.liquidation_side==0
    f.add(Event(now+1000,now+1000,'bybit_perp','liquidation',dict(reference_price=100,qty=2000,side=-1)))
    a,_=f.frame(now+1000);assert a.liquidation_side==-1


def test_unspecified_data_origin_is_not_assumed_real(tmp_path):
    data=tmp_path/'input';data.mkdir();raw=data/'raw.jsonl.gz'
    with gzip.open(raw,'wt') as stream:stream.write(json.dumps(envelope({},kind='heartbeat'))+'\n')
    m=dict(schema='btc-pressure-raw-v1',records=1,start_ms=100000,end_ms=100000,errors=[],raw_sha256=hashlib.sha256(raw.read_bytes()).hexdigest())
    (data/'manifest.json').write_text(json.dumps(m))
    report=replay(data,tmp_path/'out')
    assert report['synthetic'] is None and report['strategy_return_pct'] is None


def test_calibration_cannot_overlap_even_early_part_of_replay(tmp_path):
    data=tmp_path/'data';data.mkdir()
    (data/'manifest.json').write_text(json.dumps(dict(start_ms=100000)))
    with pytest.raises(ValueError,match='overlaps'):
        replay(data,tmp_path/'out',calibration={'training_end_ms':100001})


def test_calibrated_promotion_requires_own_prior_evidence():
    b=opened();b.mode='calibrated';b.propose(proposal(100250,family='spot_trend'))
    assert b.position['family']=='cascade'


def test_new_idea_resets_flow_invalidation_timer():
    b=broker();b.last_bad_flow=1;b.last_trailing_minute=7
    b.propose(proposal());b.on_event(book_event(100250))
    assert b.last_bad_flow is None and b.last_trailing_minute==-1
