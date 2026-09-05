"""Synthetic mechanics tests for the one-account archive replay, not performance."""
from dataclasses import asdict, replace
from pathlib import Path
import json

import numpy as np
import pandas as pd
import pytest

from research.btc_pressure.account_study import (
    AccountReplay, BASE, CONTRACT, DATES, SCENARIOS, Session, timeline,
    validate_manifest, source_identity,
)
from research.btc_pressure.strategy import Frame, Proposal
from research.btc_pressure.event_study import frames


def frame(t, price=100.):
    return Frame(t,price,.2,101.,98.,1,.5,.5,1.,1.,price+.1,price-.1,price+.1,price-.1,0)


def session(passive=True, trade_time_us=100400000, boundary=False, future_gap=False):
    start=99000
    trace=[(100000,frame(100000),'ready'),(101000,frame(101000,98.),'ready'),
           (102000,frame(102000,98.),'ready'),(103000,frame(103000,98.),'ready')]
    p=Proposal(100000,'spot_trend' if passive else 'cascade',1,100.,99.,104.,300000,passive,'unit fixture')
    book_ms=np.array([99500,100250,100750,101250,101500,102000],dtype=np.int64)
    values=[]
    for k,t in enumerate(book_ms):
        bid=99.9 if t<100750 else 98.0
        ask=bid+.1
        values.append([v for side in ([(bid-i*.1,.1) for i in range(5)],[(ask+i*.1,.1) for i in range(5)]) for pair in side for v in pair])
    end=160000 if boundary else 200000
    trace.append((end-1000,None,'stale_trade'))
    b_local=book_ms*1000
    t_local=np.array([trade_time_us],dtype=np.int64)
    c_local=np.array([99250000],dtype=np.int64)
    types,ix,times=timeline(b_local,t_local,c_local,[x[0] for x in trace])
    return Session('synthetic',start,end,trace,{p.time:p},b_local,b_local.copy(),np.array(values),
                   t_local,t_local.copy(),np.array([[99.9,1.,-1.]]),
                   c_local,c_local.copy(),np.array([28800000000.]),types,ix,times,{})


def test_scenarios_and_primary_predeclared():
    assert list(SCENARIOS)==['original_passive','double_slippage','one_second_latency','double_commission','forced_taker_comparator']
    assert SCENARIOS['original_passive'][0].capital==1000
    assert all(s.exposure<=2 for s,_ in SCENARIOS.values())


def test_event_clock_orders_boundary_before_same_time_input():
    typ,ix,t=timeline(np.array([1000000]),np.array([1000000]),np.array([1000000]),[1000])
    assert list(typ)==[0,1,2,3] and list(t)==[1000000]*4


def test_input_ties_preserve_order_within_stream():
    typ,ix,t=timeline(np.array([1000000,1000000]),np.array([],dtype=np.int64),np.array([],dtype=np.int64),[])
    assert list(ix)==[0,1]


def test_complete_synthetic_passive_round_trip_reconciles():
    a=AccountReplay(session(),BASE);r=a.run()
    assert r['completed_trade_count']==1
    assert r['closed_trades'][0]['quantity']==pytest.approx(.01)
    assert r['net_closed']<0 and r['cash']==pytest.approx(1000+r['net_closed'])
    assert r['accounting_complete'] and r['ledger_reconciled']
    assert r['cagr_pct'] is None and not r['target_achieved']


def test_passive_fill_cannot_precede_latency():
    a=AccountReplay(session(trade_time_us=100249000),BASE);r=a.run()
    assert r['completed_trade_count']==0 and r['event_counts'].get('entry_fill',0)==0


def test_same_receive_book_trade_tie_does_not_invent_maker_queue_order():
    a=AccountReplay(session(trade_time_us=100250000),BASE);r=a.run()
    assert r['event_counts'].get('entry_fill',0)==0
    assert r['skipped_execution_events']['ambiguous_maker_trade_book_tie']==1


def test_exit_is_after_trigger_and_includes_gap_and_fees():
    r=AccountReplay(session(),BASE).run()
    trade=r['closed_trades'][0]
    entries=[e for e in r['events'] if e['type']=='entry_fill']
    exits=[e for e in r['events'] if e['type']=='exit_fill']
    assert exits[0]['time']>=100750+BASE.latency_ms
    assert exits[0]['price']<entries[0]['price'] and trade['fees']>0


def test_boundary_rule_blocks_new_submission():
    r=AccountReplay(session(boundary=True),BASE).run()
    assert r['submitted_orders']==0 and r['completed_trade_count']==0


def test_one_position_not_sum_of_independent_signals():
    s=session();s.trace.insert(1,(100500,frame(100500),'ready'))
    p=s.proposals[100000]
    s.proposals[100500]=replace(p,time=100500)
    s.timeline_type,s.timeline_index,s.timeline_time=timeline(s.book_local,s.trade_local,s.ticker_local,[x[0] for x in s.trace])
    r=AccountReplay(s,BASE).run()
    assert r['submitted_orders']==1 and r['completed_trade_count']==1


def test_market_comparator_does_not_modify_frozen_signal_tape():
    s=session();before=[asdict(p) for p in s.proposals.values()]
    a=AccountReplay(s,BASE,True);r=a.run()
    assert [asdict(p) for p in s.proposals.values()]==before
    assert all(not x['passive'] for x in a.decisions)


def test_stress_keeps_same_signal_settings_and_exposure():
    for key,(settings,taker) in SCENARIOS.items():
        s=session();a=AccountReplay(s,settings,taker);r=a.run()
        assert r['raw_signals']==1 and r['capital']==1000
        assert r['max_observed_notional_equity']<2.01


def test_missing_end_book_retains_unresolved_exposure():
    s=session();s.book_local=s.book_local[:2];s.book_exchange=s.book_exchange[:2];s.book_values=s.book_values[:2]
    s.timeline_type,s.timeline_index,s.timeline_time=timeline(s.book_local,s.trade_local,s.ticker_local,[x[0] for x in s.trace])
    r=AccountReplay(s,BASE).run()
    assert r['open_position_at_end'] and not r['accounting_complete']
    assert r['session_net_return_pct'] is None


def test_data_identity_does_not_accept_missing_archive(tmp_path):
    (tmp_path/'manifest.json').write_text('{"files":[]}')
    with pytest.raises(ValueError,match='Missing'):validate_manifest(tmp_path,(DATES[0],))


def test_manifest_duplicate_rejected_before_file_access(tmp_path):
    row=dict(date=DATES[0],venue='bybit',kind='trades',status='downloaded')
    (tmp_path/'manifest.json').write_text(json.dumps({'files':[row,row]}))
    with pytest.raises(ValueError,match='Duplicate'):validate_manifest(tmp_path,(DATES[0],))


def fixture_frames():
    # Actual individual prints are synthetic only for causality testing.
    n=4500;ts=np.arange(n,dtype=np.int64)*1000000+1
    p=100+np.sin(np.arange(n)/35)*.2+np.arange(n)*.0001
    trades=pd.DataFrame(dict(timestamp=ts,local_timestamp=ts,side=np.where(np.arange(n)%3,'buy','sell'),price=p,amount=np.ones(n)))
    book=pd.DataFrame(dict(timestamp=ts,local_timestamp=ts))
    for k in range(5):
        book[f'bids[{k}].price']=p-.01-k*.01
        book[f'asks[{k}].price']=p+.01+k*.01
        book[f'bids[{k}].amount']=1.
        book[f'asks[{k}].amount']=1.
    liq=trades.iloc[0:0].copy()
    return trades,book,liq


def test_observer_does_not_change_original_signals_or_counters():
    t,b,l=fixture_frames();trace=[]
    a,c=frames(t,[t,t],b,l,0)
    x,y=frames(t,[t,t],b,l,0,observer=lambda *args:trace.append(args))
    assert a==x and c==y and len(trace)==86400


def test_frame_export_is_prefix_causal():
    t,b,l=fixture_frames();a=[];x=[];cut=4200000000
    frames(t,[t,t],b,l,0,observer=lambda *args:a.append(args))
    prefix=t[t.local_timestamp<cut];bp=b[b.local_timestamp<cut]
    frames(prefix,[prefix,prefix],bp,l,0,observer=lambda *args:x.append(args))
    assert a[:4200]==x[:4200]


def test_freeze_signal_tape_not_training_on_account_results():
    s=session();a=AccountReplay(s,BASE).run();b=AccountReplay(s,replace(BASE,slip=.001)).run()
    assert a['raw_signals']==b['raw_signals']
    assert not a['performance_proven'] and a['historical_contract_filters_verified'] is False


def test_source_identity_is_deterministic_and_binds_new_replay():
    assert len(source_identity())==64 and source_identity()==source_identity()
