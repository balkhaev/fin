"""Original 10s/5s BTC Pullback Flow rules and conservative event paper broker.

This module has no HTTP client, API keys, signing or live order path. Input is
chronologically ordered, same-venue closed 1m bars, BBO and aggressive trades.
Queue position is only a conservative L1 estimate, NOT exchange fill proof.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Deque


@dataclass(frozen=True)
class Parameters:
    maker: float = .0002
    taker: float = .0005
    slippage: float = .0001
    risk_fraction: float = .001
    max_exposure: float = 1.
    daily_loss: float = .01
    max_drawdown: float = .04
    quote_max_age_ms: int = 2000
    latency_ms: int = 250
    ttl_ms: int = 15000
    hold_ms: int = 480000
    cooldown_ms: int = 60000
    tick: float = .1
    lot: float = .001
    participation: float = .01

    def __post_init__(self):
        numeric = [self.maker,self.taker,self.slippage,self.risk_fraction,
                   self.max_exposure,self.daily_loss,self.max_drawdown,
                   self.tick,self.lot,self.participation]
        if not all(math.isfinite(x) for x in numeric):
            raise ValueError('Parameters must be finite')
        if not (0 <= self.maker < .1 and 0 <= self.taker < .1 and 0 <= self.slippage < .1):
            raise ValueError('Invalid costs')
        if not (0 < self.risk_fraction <= .01 and 0 < self.max_exposure <= 3):
            raise ValueError('Invalid risk/exposure')
        if not (0 < self.daily_loss < 1 and 0 < self.max_drawdown < 1):
            raise ValueError('Invalid circuit breakers')
        if self.tick <= 0 or self.lot <= 0 or not 0 < self.participation <= 1:
            raise ValueError('Invalid contract constraints')
        if min(self.quote_max_age_ms,self.latency_ms,self.ttl_ms,self.hold_ms,self.cooldown_ms) < 1:
            raise ValueError('Time limits must be positive')


@dataclass(frozen=True)
class Quote:
    ts: int
    bid: float
    ask: float
    bid_qty: float
    ask_qty: float

    def __post_init__(self):
        if not (self.ts >= 0 and all(math.isfinite(x) for x in
                (self.bid,self.ask,self.bid_qty,self.ask_qty)) and
                0 < self.bid < self.ask and min(self.bid_qty,self.ask_qty) >= 0):
            raise ValueError('Invalid or crossed quote')


@dataclass(frozen=True)
class Trade:
    ts: int
    price: float
    qty: float
    buyer_aggressor: bool

    def __post_init__(self):
        if self.ts < 0 or not all(math.isfinite(x) and x > 0 for x in (self.price,self.qty)):
            raise ValueError('Invalid trade')
        if type(self.buyer_aggressor) is not bool:
            raise ValueError('Aggressor direction must be explicit boolean')


@dataclass(frozen=True)
class Bar:
    end: int
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float

    def __post_init__(self):
        if not all(math.isfinite(x) for x in (self.high,self.low,self.close,self.volume,self.quote_volume)):
            raise ValueError('Invalid bar')
        if not (self.end > 0 and self.end % 60000 == 0 and 0 < self.low <= self.close <= self.high):
            raise ValueError('Invalid OHLC or bar boundary')
        if self.volume < 0 or self.quote_volume < 0:
            raise ValueError('Negative volume')


@dataclass(frozen=True)
class Signal:
    ts: int
    side: int
    limit: float
    target: float
    stop_fraction: float


class Indicators:
    def __init__(self):
        self.bars: Deque[Bar] = deque(maxlen=20)
        self.previous_close: float | None = None
        self.atr: float | None = None
        self.ema20: float | None = None
        self.ema50: float | None = None
        self.ema_history: Deque[float] = deque(maxlen=4)
        self.minutes = 0
        self.fives = 0
        self.end = 0

    def update(self, bar: Bar, now: int):
        if bar.end > now:
            raise ValueError('Unclosed/future bar')
        if self.end and bar.end != self.end + 60000:
            raise ValueError('Duplicate or missing minute: reset and rewarm required')
        tr = bar.high - bar.low
        if self.previous_close is not None:
            tr = max(tr,abs(bar.high-self.previous_close),abs(bar.low-self.previous_close))
        self.atr = tr if self.atr is None else self.atr + (tr-self.atr)/14
        self.previous_close = bar.close
        self.minutes += 1
        self.end = bar.end
        self.bars.append(bar)
        if bar.end % 300000 == 0 and self.minutes >= 5:
            self.fives += 1
            self.ema20 = bar.close if self.ema20 is None else self.ema20 + 2/21*(bar.close-self.ema20)
            self.ema50 = bar.close if self.ema50 is None else self.ema50 + 2/51*(bar.close-self.ema50)
            self.ema_history.append(self.ema20)

    def context(self, now: int):
        if self.fives < 53 or len(self.bars) != 20 or len(self.ema_history) < 4:
            return None
        if not (self.end <= now < self.end + 65000):
            return None
        volume = sum(x.volume for x in self.bars)
        if volume <= 0 or self.atr is None or self.atr <= 0:
            return None
        vwap = sum(x.quote_volume for x in self.bars)/volume
        trend = 1 if self.ema20 > self.ema50 and self.ema_history[-1] > self.ema_history[0] else (
            -1 if self.ema20 < self.ema50 and self.ema_history[-1] < self.ema_history[0] else 0)
        return vwap, self.atr, trend


class FlowSignal:
    def __init__(self, params=Parameters()):
        self.params = params
        self.indicators = Indicators()
        self.ticks: Deque[Trade] = deque()
        self.start: int | None = None
        self.last_ts = -1
        self.used = {1:False,-1:False}
        self.next_evaluation = 0

    def on_trade(self, trade: Trade, quote: Quote | None) -> Signal | None:
        if trade.ts < self.last_ts:
            raise ValueError('Trade time reversed')
        if self.last_ts >= 0 and trade.ts-self.last_ts > 2000:
            # A feed outage must not masquerade as an order-flow reversal.
            self.ticks.clear()
            self.start = trade.ts
        self.last_ts = trade.ts
        if self.start is None:
            self.start = trade.ts
        self.ticks.append(trade)
        while self.ticks and self.ticks[0].ts <= trade.ts-20000:
            self.ticks.popleft()
        if quote is None or not 0 <= trade.ts-quote.ts <= self.params.quote_max_age_ms:
            return None
        context = self.indicators.context(trade.ts)
        if context is None or trade.ts-self.start < 20000:
            return None
        vwap, atr, trend = context
        if trade.price >= vwap:
            self.used[1] = False
        if trade.price <= vwap:
            self.used[-1] = False
        if trade.ts < self.next_evaluation:
            return None
        self.next_evaluation = trade.ts + 100  # deterministic CPU bound, 10Hz decisions
        side = 1 if trade.price <= vwap-atr else -1 if trade.price >= vwap+atr else 0
        if side == 0 or side != trend or self.used[side]:
            return None
        current = [t for t in self.ticks if t.ts > trade.ts-10000]
        previous = [t for t in self.ticks if t.ts <= trade.ts-10000]
        recent5 = [t for t in self.ticks if t.ts > trade.ts-5000]
        preceding10 = [t for t in self.ticks if trade.ts-15000 < t.ts <= trade.ts-5000]
        if not all((current,previous,recent5,preceding10)):
            return None
        curr_buy = sum(t.qty for t in current if t.buyer_aggressor)/sum(t.qty for t in current)
        prev_buy = sum(t.qty for t in previous if t.buyer_aggressor)/sum(t.qty for t in previous)
        confirmed = ((curr_buy > .6 and prev_buy < .5 and
                     min(t.price for t in recent5) >= min(t.price for t in preceding10)) if side==1 else
                     (curr_buy < .4 and prev_buy > .5 and
                     max(t.price for t in recent5) <= max(t.price for t in preceding10)))
        if not confirmed:
            return None
        raw = quote.bid if side==1 else quote.ask
        # Passive price rounding. Never change post-only to a market order.
        limit = (math.floor(raw/self.params.tick) if side==1 else math.ceil(raw/self.params.tick))*self.params.tick
        sf = max(.001,atr/trade.price)
        gain = side*(vwap/limit-1)
        cost = self.params.maker + self.params.taker + 2*self.params.slippage
        if sf > .002 or gain < 2*sf or gain < 3*cost:
            return None
        # Require a new excursion after every issued signal, even an unfilled one.
        self.used[side] = True
        return Signal(trade.ts,side,limit,vwap,sf)


@dataclass
class PassiveOrder:
    side: int
    limit: float
    remaining: float
    queue_ahead: float
    active_at: int
    cancel_at: int
    activated: bool = False

    def fill(self, trade: Trade, params: Parameters) -> float:
        if not self.activated or not self.active_at <= trade.ts < self.cancel_at:
            return 0.
        opposite = (not trade.buyer_aggressor) if self.side==1 else trade.buyer_aggressor
        reaches = trade.price <= self.limit if self.side==1 else trade.price >= self.limit
        if not opposite or not reaches:
            return 0.
        # Never infer queue cancellations from BBO size shrinking.
        after_queue = max(0.,trade.qty-self.queue_ahead)
        self.queue_ahead = max(0.,self.queue_ahead-trade.qty)
        available = min(after_queue,trade.qty*params.participation,self.remaining)
        qty = math.floor((available+1e-12)/params.lot)*params.lot
        self.remaining = max(0.,self.remaining-qty)
        return qty


class PaperBroker:
    def __init__(self, params=Parameters(), capital=10000.):
        if not math.isfinite(capital) or capital <= 0:
            raise ValueError('Invalid capital')
        self.p = params
        self.cash = self.initial = self.peak = self.day_start = capital
        self.day = -1
        self.halted = False
        self.day_halted = False
        self.execution_incomplete = False
        self.pending: PassiveOrder | None = None
        self.signal: Signal | None = None
        self.qty = 0.
        self.total_qty = 0.
        self.entered: int | None = None
        self.entry = 0.
        self.side = 0
        self.target: PassiveOrder | None = None
        self.fees = self.funding = self.gross = 0.
        self.last_exit = -10**15
        self.trades: list[dict] = []
        self.events: list[dict] = []

    def equity(self, price: float) -> float:
        return self.cash + self.side*self.qty*(price-self.entry)

    def valid_quote(self, now: int, quote: Quote | None) -> bool:
        return quote is not None and 0 <= now-quote.ts <= self.p.quote_max_age_ms

    def submit(self, signal: Signal, quote: Quote):
        if (self.halted or self.day_halted or self.execution_incomplete or self.qty or self.pending
                or signal.ts-self.last_exit < self.p.cooldown_ms or not self.valid_quote(signal.ts,quote)):
            return False
        if signal.side not in (-1,1) or not all(math.isfinite(x) for x in (signal.limit,signal.target,signal.stop_fraction)):
            raise ValueError('Invalid signal')
        if not .001 <= signal.stop_fraction <= .002:
            raise ValueError('Invalid stop')
        if (signal.side==1 and signal.limit>=quote.ask) or (signal.side==-1 and signal.limit<=quote.bid):
            return False
        # Last 10m + the whole settlement minute excluded. Future funding rates unused.
        minute = signal.ts//60000 % 480
        if minute == 0 or minute >= 470:
            return False
        costs = self.p.maker+self.p.taker+2*self.p.slippage
        gain = signal.side*(signal.target/signal.limit-1)
        if gain < max(2*signal.stop_fraction,3*costs):
            return False
        notional = min(self.cash*self.p.max_exposure,
                       self.cash*self.p.risk_fraction/(signal.stop_fraction+costs))
        qty = math.floor(notional/signal.limit/self.p.lot)*self.p.lot
        if qty<=0:
            return False
        self.signal = signal
        queue = quote.bid_qty if signal.side==1 else quote.ask_qty
        self.pending = PassiveOrder(signal.side,signal.limit,qty,queue,
                                    signal.ts+self.p.latency_ms,
                                    signal.ts+self.p.ttl_ms+self.p.latency_ms)
        self.events.append(dict(ts=signal.ts,type='submit_post_only',qty=qty,price=signal.limit))
        return True

    def activate(self, now: int, quote: Quote | None):
        order = self.pending
        if order is None:
            return
        if now >= order.cancel_at:
            self.events.append(dict(ts=now,type='cancel_ack'))
            self.pending = None
            return
        if not order.activated and now >= order.active_at:
            if not self.valid_quote(now,quote):
                self.pending = None
                self.events.append(dict(ts=now,type='stale_activation_reject'))
                return
            crossing = order.limit>=quote.ask if order.side==1 else order.limit<=quote.bid
            if crossing:
                self.pending = None
                self.events.append(dict(ts=now,type='post_only_reject'))
                return
            # Use the larger observed queue; unknown cancellations are never credited.
            order.queue_ahead = max(order.queue_ahead,quote.bid_qty if order.side==1 else quote.ask_qty)
            order.activated = True

    def close_qty(self, now: int, qty: float, price: float, maker: bool, reason: str):
        qty = min(qty,self.qty)
        gross = self.side*qty*(price-self.entry)
        fee = qty*price*(self.p.maker if maker else self.p.taker)
        self.cash += gross-fee
        self.gross += gross
        self.fees += fee
        self.qty = max(0.,self.qty-qty)
        self.events.append(dict(ts=now,type='exit_fill',qty=qty,price=price,reason=reason))
        if self.qty < self.p.lot/2:
            self.trades.append(dict(entry_ms=self.entered,exit_ms=now,side=self.side,
                                   entry=self.entry,quantity=self.total_qty,gross=self.gross,
                                   fees=self.fees,funding_cost=self.funding,
                                   net=self.gross-self.fees-self.funding,reason=reason))
            self.qty = self.total_qty = 0.
            self.side = 0
            self.pending = self.target = None
            self.last_exit = now
            self.fees = self.gross = self.funding = 0.
            self.entered = None

    def on_funding(self, ts: int, rate: float, mark: float):
        if not math.isfinite(rate) or not math.isfinite(mark) or mark <= 0:
            raise ValueError('Invalid funding event')
        if self.qty:
            cost = self.side*self.qty*mark*rate
            self.cash -= cost
            self.funding += cost
            self.events.append(dict(ts=ts,type='funding',cost=cost))

    def on_trade(self, trade: Trade, quote: Quote | None):
        if self.day != trade.ts//86400000:
            self.day = trade.ts//86400000
            self.day_start = self.equity(trade.price)
            self.day_halted = False
        self.activate(trade.ts,quote)
        old_target = self.target
        if self.pending and self.pending.activated:
            q = self.pending.fill(trade,self.p)
            if q > 0:
                self.entry = self.pending.limit
                self.side = self.pending.side
                self.qty += q
                self.total_qty += q
                if self.entered is None:
                    self.entered = trade.ts
                fee = q*self.entry*self.p.maker
                self.cash -= fee
                self.fees += fee
                self.events.append(dict(ts=trade.ts,type='entry_fill',qty=q,price=self.entry))
                # Target queue is unknown away from BBO. Require a trade THROUGH target;
                # this model does not manufacture a queue from missing depth.
                target_price = (math.ceil(self.signal.target/self.p.tick) if self.side==1 else
                                math.floor(self.signal.target/self.p.tick))*self.p.tick
                self.target = PassiveOrder(-self.side,target_price,self.qty,0.,
                                           trade.ts+self.p.latency_ms,10**18,True)
                if self.pending.remaining < self.p.lot/2:
                    self.pending = None
        eq = self.equity(trade.price)
        self.peak = max(self.peak,eq)
        dd_hit = eq <= self.peak*(1-self.p.max_drawdown)
        day_hit = eq <= self.day_start*(1-self.p.daily_loss)
        self.halted |= dd_hit
        self.day_halted |= day_hit
        if self.qty:
            stop_price = self.entry*(1-self.side*self.signal.stop_fraction)
            stop_hit = trade.price<=stop_price if self.side==1 else trade.price>=stop_price
            timed = trade.ts-self.entered >= self.p.hold_ms
            if stop_hit or timed or dd_hit or day_hit:
                if not self.valid_quote(trade.ts,quote):
                    self.execution_incomplete = self.halted = True
                    self.pending = None
                    self.events.append(dict(ts=trade.ts,type='unpriceable_exit_stale_quote'))
                    return
                # Price gaps and spread are not hidden. This models an immediate
                # protective exit; actual latency/order cancellation is NOT certified.
                raw = min(trade.price,quote.bid) if self.side==1 else max(trade.price,quote.ask)
                price = raw*(1-self.side*self.p.slippage)
                reason = 'risk' if dd_hit or day_hit else 'stop' if stop_hit else 'time'
                self.close_qty(trade.ts,self.qty,price,False,reason)
            elif old_target is not None and old_target is self.target:
                through = trade.price>old_target.limit if self.side==1 else trade.price<old_target.limit
                if through:
                    q = old_target.fill(trade,self.p)
                    if q > 0:
                        self.close_qty(trade.ts,q,old_target.limit,True,'target')
        marked = self.equity(trade.price)
        self.peak = max(self.peak,marked)
        self.halted |= marked <= self.peak*(1-self.p.max_drawdown)
        self.day_halted |= marked <= self.day_start*(1-self.p.daily_loss)
        if self.halted or self.day_halted:
            self.pending = None


def replay_jsonl(path: Path, out: Path, params=Parameters()):
    signal_engine, broker = FlowSignal(params), PaperBroker(params)
    quote = None
    previous = -1
    metadata = None
    last_price = None
    with path.open() as stream:
        for line in stream:
            event = json.loads(line)
            kind = event.pop('type')
            if kind == 'metadata':
                if metadata is not None or previous >= 0:
                    raise ValueError('Metadata must be first and unique')
                metadata = event
                if not metadata.get('funding_complete') or not metadata.get('same_venue'):
                    raise ValueError('Missing funding/venue evidence; exact replay blocked')
                continue
            if metadata is None:
                raise ValueError('Evidence metadata required')
            now = event['ts']
            if now < previous:
                raise ValueError('Events are not chronological')
            previous = now
            if kind == 'bar':
                data = dict(event); data.pop('ts')
                signal_engine.indicators.update(Bar(**data),now)
            elif kind == 'quote':
                quote = Quote(**event)
                broker.activate(now,quote)
            elif kind == 'funding':
                broker.on_funding(now,event['rate'],event['mark'])
            elif kind == 'trade':
                trade = Trade(**event)
                last_price = trade.price
                broker.on_trade(trade,quote)
                signal = signal_engine.on_trade(trade,quote)
                if signal:
                    broker.submit(signal,quote)
            else:
                raise ValueError(f'Unknown event: {kind}')
    if metadata is None:
        raise ValueError('Empty replay')
    report = dict(metadata=metadata,parameters=asdict(params),initial_equity=broker.initial,
                  final_marked_equity=broker.equity(last_price) if last_price is not None else broker.cash,
                  open_quantity=broker.qty,closed_trades=broker.trades,events=broker.events,
                  execution_incomplete=broker.execution_incomplete,live_ready=False,
                  actual_fills_validated=False,queue_model='L1 queue estimate; target trade-through; bounded participation',
                  limitations=['No exchange submission.', 'BBO is not full order-book queue.',
                               'Immediate modeled protective exit; no exchange latency/cancellation-race certification.'])
    out.write_text(json.dumps(report,indent=2,allow_nan=False))
    return report


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--events',required=True,type=Path)
    ap.add_argument('--out',required=True,type=Path)
    a=ap.parse_args()
    replay_jsonl(a.events,a.out)
