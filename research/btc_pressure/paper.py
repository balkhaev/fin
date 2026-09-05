"""One isolated paper account with latency, finite depth and no live order path."""
from __future__ import annotations
from dataclasses import dataclass,asdict
from decimal import Decimal,ROUND_DOWN,ROUND_UP
import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path
from .strategy import Proposal


@dataclass(frozen=True)
class Settings:
    capital: float=1000.
    risk: float=.0025
    exposure: float=2.
    maker_fee: float=.0002
    taker_fee: float=.0005
    slip: float=.0001
    latency_ms: int=250
    ttl_ms: int=15000
    participation: float=.01
    book_age_ms: int=1500

    def __post_init__(self):
        values=asdict(self)
        if not all(math.isfinite(v) for v in values.values()):raise ValueError('Nonfinite setting')
        if self.capital<=0 or not 0<self.risk<=.005 or not 0<self.exposure<=2:
            raise ValueError('Invalid experimental capital/risk/exposure')
        if min(self.maker_fee,self.taker_fee,self.slip)<0 or max(self.maker_fee,self.taker_fee,self.slip)>=.1:
            raise ValueError('Invalid fees/slippage')
        if min(self.latency_ms,self.ttl_ms,self.book_age_ms)<1 or not 0<self.participation<=1:
            raise ValueError('Invalid execution constraints')

    def fingerprint(self):
        return hashlib.sha256(json.dumps(asdict(self),sort_keys=True).encode()).hexdigest()


def round_step(q,step,up=False):
    return float((Decimal(str(q))/Decimal(str(step))).to_integral_value(rounding=ROUND_UP if up else ROUND_DOWN)*Decimal(str(step)))


@lru_cache(maxsize=1)
def model_fingerprint():
    """Bind empirical gates to the exact signal, normalization and fill model."""
    root=Path(__file__).parent
    digest=hashlib.sha256()
    for name in ('adapters.py','strategy.py','paper.py','run.py'):
        raw=(root/name).read_bytes()
        digest.update(name.encode()+b'\0'+hashlib.sha256(raw).digest())
    return digest.hexdigest()


class Gate:
    """Pre-period empirical evidence, not an invented trade win probability."""
    def __init__(self,artifact=None):self.artifact=artifact
    def allows(self,proposal,venue,settings):
        a=self.artifact
        if not a:return False,'no_calibration'
        if a.get('schema')!='btc-pressure-gate-v2' or a.get('model_sha256')!=model_fingerprint():
            return False,'calibration_model_mismatch'
        if a.get('venue')!=venue or a.get('settings_sha256')!=settings.fingerprint():return False,'calibration_identity_mismatch'
        end=a.get('training_end_ms')
        if type(end) is not int or end>=proposal.time:return False,'calibration_from_future'
        if a.get('synthetic') is not False:return False,'synthetic_calibration'
        cell=a.get('cells',{}).get(f'{proposal.family}:{proposal.side}',{})
        n,d,lower=cell.get('trades'),cell.get('days'),cell.get('lower_mean_daily_r')
        valid=(type(n) is int and type(d) is int and type(lower) in (int,float)
               and math.isfinite(lower))
        good=valid and n>=200 and d>=30 and lower>0
        return good,'positive_prior_net_evidence' if good else 'insufficient_prior_net_evidence'


class Broker:
    def __init__(self,settings=Settings(),venue='bybit_perp',mode='observe',calibration=None):
        if venue not in ('bybit_perp','binance_perp') or mode not in ('observe','diagnostic','calibrated'):
            raise ValueError('Invalid venue/mode')
        self.s=settings;self.venue=venue;self.mode=mode;self.gate=Gate(calibration)
        self.cash=self.peak=self.day_start=settings.capital
        self.day=None;self.position=None;self.pending=None;self.exit_order=None
        self.book=None;self.book_time=-1;self.instrument=None;self.next_funding=None
        self.halted=False;self.day_halted=False;self.incomplete=False;self.max_dd=0.
        self.trades=[];self.events=[];self.last_exit=-10**15;self.now=-1
        self.book_serial=0;self.proposals=0;self.funding_seen=set();self.unresolved_funding=set()
        self.last_bad_flow=None;self.last_trailing_minute=-1
        self.depth_size={};self.depth_available={}
        self.position_serial=0
        self.exposure_history=[]  # (receive_ms, position_id, side, quantity) after each fill
        self.funding_obligations={}
        self.funding_values={}
        self.funding_history_revised=False

    def log(self,kind,**data):self.events.append(dict(time=self.now,type=kind,**data))
    def fresh(self):return self.book is not None and 0<=self.now-self.book_time<=self.s.book_age_ms
    def equity(self):
        p=self.position
        if not p:return self.cash
        if not self.fresh():return None
        mark=self.book['bids'][0][0] if p['side']==1 else self.book['asks'][0][0]
        return self.cash+p['side']*p['qty']*(mark-p['entry'])-p['qty']*mark*self.s.taker_fee

    def risk_fraction(self):
        eq=self.equity()
        if eq is None:return 0.
        dd=1-eq/self.peak
        return min(self.s.risk/2,.001) if dd>=.05 else self.s.risk/2 if dd>=.03 else self.s.risk

    def circuit(self):
        eq=self.equity()
        if eq is None:return
        day=self.now//86400000
        if day!=self.day:self.day=day;self.day_start=eq;self.day_halted=False
        self.peak=max(self.peak,eq);dd=1-eq/self.peak;self.max_dd=max(self.max_dd,dd)
        self.halted|=dd>=.07
        self.day_halted|=eq<=self.day_start*.99
        if self.halted or self.day_halted:
            self.cancel('risk_halt');self.request_exit('risk_halt')

    def cancel(self,reason):
        if self.pending and self.pending.get('cancel_at') is None:
            self.pending['cancel_at']=self.now+self.s.latency_ms
            self.log('cancel_requested',reason=reason)

    def request_exit(self,reason):
        if self.position and self.exit_order is None:
            self.cancel('reduce_only_exit')
            self.exit_order=dict(active=self.now+self.s.latency_ms,reason=reason,last_book=-1)
            self.log('exit_requested',reason=reason)

    def propose(self,p:Proposal):
        if p.time!=self.now:raise ValueError('Proposal must use current receive clock')
        self.proposals+=1
        self.log('proposal',family=p.family,side=p.side)
        if self.position:
            if p.side!=self.position['side']:
                self.request_exit('opposite_signal');return False
            if p.family=='spot_trend' and self.position['family']!='spot_trend':
                if self.mode=='calibrated' and not self.gate.allows(p,self.venue,self.s)[0]:
                    self.log('promotion_blocked',reason='uncalibrated_mechanism');return False
                pos=self.position;pos['family']='spot_trend';pos['expires']=self.now+p.hold_ms
                pos['stop']=max(pos['stop'],p.stop) if p.side==1 else min(pos['stop'],p.stop)
                self.log('promoted_without_adding_quantity')
            return False
        if self.mode=='observe':self.log('entry_blocked',reason='observe_only');return False
        if self.mode=='calibrated':
            allow,reason=self.gate.allows(p,self.venue,self.s)
            if not allow:self.log('entry_blocked',reason=reason);return False
        if self.pending or self.halted or self.day_halted or self.incomplete or self.now-self.last_exit<60000:
            return False
        if not self.fresh() or self.instrument is None or self.next_funding is None or self.next_funding<=self.now+5000:
            self.log('entry_blocked',reason='missing_execution_or_contract_context');return False
        tick,step=self.instrument['tick'],self.instrument['qty_step']
        if p.passive:
            best=self.book['bids'][0][0] if p.side==1 else self.book['asks'][0][0]
            price=round_step(best,tick,up=p.side==-1)
        else:
            best=self.book['asks'][0][0] if p.side==1 else self.book['bids'][0][0]
            price=best*(1+p.side*max(.0005,2*self.s.slip))
        stop_distance=p.side*(price-p.stop)
        gain=p.side*(p.target-price)
        fee=self.s.maker_fee if p.passive else self.s.taker_fee
        costs=price*(fee+self.s.taker_fee+2*self.s.slip)
        if stop_distance<=0 or stop_distance/price>.05 or gain<max(stop_distance*1.5,costs*3):
            self.log('entry_blocked',reason='cost_or_stop_gate');return False
        budget=self.cash*self.risk_fraction()
        qty=round_step(min(budget/(stop_distance+costs),self.cash*self.s.exposure/price),step)
        if qty<self.instrument['min_qty'] or qty*price<self.instrument['min_notional']:return False
        self.pending=dict(proposal=p,remaining=qty,limit=price,active=self.now+self.s.latency_ms,
                          expires=self.now+self.s.ttl_ms,cancel_at=None,activated=False,
                          queue=0.,risk_budget=budget)
        self.log('entry_submitted',quantity=qty,price=price,passive=p.passive)
        return True

    def entry_fill(self,qty,price,fee_rate):
        order=self.pending;p=order['proposal'];fee=qty*price*fee_rate
        if self.position is None:
            self.last_bad_flow=None;self.last_trailing_minute=-1
            self.position_serial+=1
            self.position=dict(position_id=self.position_serial,side=p.side,qty=0.,entry=0.,stop=p.stop,target=p.target,
                family=p.family,initial_family=p.family,opened=self.now,expires=self.now+p.hold_ms,
                gross=0.,fees=0.,funding=0.,total_qty=0.,risk_budget=order['risk_budget'])
        pos=self.position
        pos['entry']=(pos['entry']*pos['qty']+price*qty)/(pos['qty']+qty)
        pos['qty']+=qty;pos['total_qty']+=qty;pos['fees']+=fee
        self.cash-=fee;order['remaining']=max(0.,order['remaining']-qty)
        self.exposure_history.append((self.now,pos['position_id'],pos['side'],pos['qty']))
        self.log('entry_fill',quantity=qty,price=price,fee=fee)
        if order['remaining']<self.instrument['qty_step']/2:self.pending=None

    def exit_fill(self,qty,price):
        pos=self.position;qty=min(qty,pos['qty']);fee=qty*price*self.s.taker_fee
        gross=pos['side']*qty*(price-pos['entry'])
        self.cash+=gross-fee;pos['gross']+=gross;pos['fees']+=fee;pos['qty']-=qty
        self.exposure_history.append((self.now,pos['position_id'],pos['side'],max(0.,pos['qty'])))
        self.log('exit_fill',quantity=qty,price=price,fee=fee,reason=self.exit_order['reason'])
        if pos['qty']<1e-10:
            net=pos['gross']-pos['fees']-pos['funding']
            self.trades.append(dict(position_id=pos['position_id'],risk_budget=pos['risk_budget'],entry_ms=pos['opened'],exit_ms=self.now,side=pos['side'],
                family=pos['initial_family'],final_family=pos['family'],quantity=pos['total_qty'],
                gross=pos['gross'],fees=pos['fees'],funding=pos['funding'],net=net,
                net_r=net/pos['risk_budget'],reason=self.exit_order['reason']))
            self.position=None;self.exit_order=None;self.last_exit=self.now
            if self.pending:self.pending=None;self.log('entry_remainder_reduced_to_zero')

    def execute_book(self):
        if not self.fresh():return
        if self.exit_order and self.now>=self.exit_order['active'] and self.book_time>=self.exit_order['active']:
            if self.exit_order['last_book']==self.book_serial:return
            self.exit_order['last_book']=self.book_serial
            side=self.position['side'];levels=self.book['bids'] if side==1 else self.book['asks']
            for price,_ in levels:
                if self.position is None:break
                key=('bids' if side==1 else 'asks',price)
                qty=min(self.depth_available.get(key,0.),self.position['qty'])
                if qty>0:
                    self.depth_available[key]-=qty
                    self.exit_fill(qty,price*(1-side*self.s.slip))
            return
        order=self.pending
        if not order or self.now<order['active'] or self.book_time<order['active']:return
        p=order['proposal']
        if p.passive and not order['activated']:
            crossing=order['limit']>=self.book['asks'][0][0] if p.side==1 else order['limit']<=self.book['bids'][0][0]
            if crossing:self.pending=None;self.log('post_only_rejected');return
            levels=self.book['bids'] if p.side==1 else self.book['asks']
            visible=[q for price,q in levels if abs(price-order['limit'])<1e-9]
            if not visible:self.pending=None;self.log('unknown_queue_rejected');return
            order['queue']=visible[0];order['activated']=True
        elif not p.passive:
            levels=self.book['asks'] if p.side==1 else self.book['bids']
            for price,_ in levels:
                key=('asks' if p.side==1 else 'bids',price)
                qty=self.depth_available.get(key,0.)
                fill=price*(1+p.side*self.s.slip)
                if p.side*(fill-order['limit'])>0 or p.side*(fill-p.stop)<=0:break
                q=round_step(min(qty,order['remaining']),self.instrument['qty_step'])
                if q>0:
                    self.depth_available[key]-=q
                    self.entry_fill(q,fill,self.s.taker_fee)
                if self.pending is None:break
            self.pending=None

    def on_event(self,e):
        if e.received<self.now:raise ValueError('Broker receipt clock reversed')
        self.now=e.received
        self.capture_funding_obligation()
        if self.pending:
            if self.now>=self.pending['expires']:
                deadline=self.pending['expires']+self.s.latency_ms
                previous=self.pending.get('cancel_at')
                self.pending['cancel_at']=deadline if previous is None else min(previous,deadline)
            if self.pending.get('cancel_at') is not None and self.now>=self.pending['cancel_at']:
                self.pending=None;self.log('cancel_ack')
        if e.source!=self.venue:
            self.maintenance();self.circuit();return
        data=e.data
        if e.kind=='instrument':self.instrument=dict(data)
        elif e.kind=='context':
            if 'next_funding' in data:
                self.next_funding=int(data['next_funding'])
        elif e.kind=='funding':
            self.apply_funding(e)
        elif e.kind=='gap':
            self.book=None;self.cancel('data_gap')
            if self.position:self.incomplete=True;self.request_exit('data_gap')
        elif e.kind=='book':
            if self.now-e.occurred>5000 or e.occurred>self.now+500:
                self.book=None
            else:
                sizes={(side,price):qty for side in ('bids','asks') for price,qty in data[side]}
                for key in self.depth_size.keys() | sizes.keys():
                    old=self.depth_size.get(key,0.);new=sizes.get(key,0.)
                    self.depth_available[key]=max(0.,self.depth_available.get(key,0.)+new-old)
                self.depth_size=sizes
                self.book=data;self.book_time=self.now;self.book_serial+=1
                self.execute_book()
        elif e.kind=='trade' and self.pending and self.pending['proposal'].passive:
            o=self.pending;p=o['proposal']
            eligible=(o['activated'] and e.occurred>=o['active'] and self.now>=o['active'] and self.now-e.occurred<=5000
                      and e.occurred<=self.now+500 and data['side']==-p.side and p.side*(data['price']-o['limit'])<=0 and self.fresh())
            if eligible:
                remainder=max(0.,data['qty']-o['queue']);o['queue']=max(0.,o['queue']-data['qty'])
                qty=round_step(min(remainder,data['qty']*self.s.participation,o['remaining']),self.instrument['qty_step'])
                if qty>0:self.entry_fill(qty,o['limit'],self.s.maker_fee)
        self.maintenance()
        self.circuit()

    def exposure_at(self, timestamp):
        # Strictly before settlement; fills at identical millisecond have unknown
        # exchange ordering and must not create a certified funding calculation.
        snapshot=None
        for time,pid,side,quantity in reversed(self.exposure_history):
            if time<timestamp:
                snapshot=(pid,side,quantity)
                break
        return snapshot if snapshot and snapshot[2]>1e-12 else None

    def capture_funding_obligation(self):
        t=self.next_funding
        if t is None or t>self.now or t in self.funding_seen or t in self.funding_obligations:
            return
        exposure=self.exposure_at(t)
        if exposure:
            self.funding_obligations[t]=exposure
            self.unresolved_funding.add(t)

    def apply_funding(self,e):
        rate,mark=float(e.data['rate']),float(e.data['mark'])
        if not math.isfinite(rate) or not math.isfinite(mark) or mark<=0 or e.occurred>self.now:
            raise ValueError('Invalid realized funding event')
        values=(rate,mark)
        if e.occurred in self.funding_values:
            if self.funding_values[e.occurred]!=values:
                self.incomplete=True;self.halted=True
                self.log('conflicting_funding_duplicate')
            return
        exposure=self.funding_obligations.get(e.occurred,self.exposure_at(e.occurred))
        if any(time==e.occurred for time,_,_,_ in self.exposure_history):
            self.incomplete=True
            self.log('funding_fill_timestamp_tie_unverified')
        self.funding_values[e.occurred]=values
        self.funding_seen.add(e.occurred)
        self.unresolved_funding.discard(e.occurred)
        if not exposure:return
        pid,side,qty=exposure
        cost=side*qty*mark*rate
        self.cash-=cost
        if self.position and self.position['position_id']==pid:
            self.position['funding']+=cost
        else:
            trade=next((t for t in self.trades if t['position_id']==pid),None)
            if trade is None:
                self.incomplete=True;self.halted=True
                raise ValueError('Funding exposure has no owning trade')
            trade['funding']+=cost
            trade['net']=trade['gross']-trade['fees']-trade['funding']
            trade['net_r']=trade['net']/trade['risk_budget']
        # Realized cash is corrected, but historical peaks and decisions made
        # before receipt must not be retrospectively presented as certified.
        if e.occurred<self.now:
            self.funding_history_revised=True
        self.log('funding_paid',cost=cost,position_id=pid,settlement_ms=e.occurred)

    def maintenance(self):
        if any(t+5000<self.now for t in self.unresolved_funding):
            self.incomplete=True;self.request_exit('funding_not_verified')
        if self.position:
            p=self.position
            if not self.fresh():
                self.incomplete=True;self.request_exit('stale_execution_book')
            else:
                price=self.book['bids'][0][0] if p['side']==1 else self.book['asks'][0][0]
                if p['side']*(price-p['stop'])<=0:self.request_exit('stop')
                elif p['family']!='spot_trend' and p['side']*(price-p['target'])>=0:self.request_exit('target')
                elif self.now>=p['expires']:self.request_exit('time')
                eq=self.equity()
                if eq is not None and p['qty']*price>max(0.,eq)*self.s.exposure:self.request_exit('exposure_cap')

    def manage_frame(self,f):
        p=self.position
        if not p:return
        flow=f.spot_bias if p['family']=='spot_trend' else f.perp_flow
        invalid=p['side']*flow<-.1
        if invalid:
            if self.last_bad_flow is None:self.last_bad_flow=f.time
            elif f.time-self.last_bad_flow>=10000:self.request_exit('flow_invalidated')
        else:self.last_bad_flow=None
        if p['family']=='spot_trend' and f.time//60000!=self.last_trailing_minute:
            stop=f.price-p['side']*2*f.atr
            p['stop']=max(p['stop'],stop) if p['side']==1 else min(p['stop'],stop)
            self.last_trailing_minute=f.time//60000

    def report(self):
        realized=sum(t['net'] for t in self.trades)
        p=self.position
        balance=self.s.capital+realized+(p['gross']-p['fees']-p['funding'] if p else 0)
        if not math.isclose(balance,self.cash,abs_tol=1e-7):raise AssertionError('Cash ledger does not reconcile')
        eq=self.equity()
        return dict(mode=self.mode,venue=self.venue,model_sha256=model_fingerprint(),settings=asdict(self.s),settings_sha256=self.s.fingerprint(),
                    capital=self.s.capital,cash=self.cash,marked_equity=eq,
                    marked_return_pct=(eq/self.s.capital-1)*100 if eq is not None else None,
                    max_marked_drawdown_pct=self.max_dd*100,closed_trades=self.trades,
                    open_quantity=p['qty'] if p else 0.,pending_entry=self.pending is not None,
                    open_position_at_end=p is not None,execution_incomplete=self.incomplete or bool(self.unresolved_funding),
                    unresolved_funding=sorted(self.unresolved_funding),proposal_count=self.proposals,
                    cagr_pct=None,annual_target_established=False,live_ready=False,
                    ledger_reconciled=True,funding_history_revised=self.funding_history_revised,
                    funding_time_drawdown_verified=not self.funding_history_revised,events=self.events)
