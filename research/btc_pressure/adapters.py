"""Venue-specific normalization. Prices from liquidation feeds are NOT fill prices."""
from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Event:
    received: int
    occurred: int
    source: str
    kind: str
    data: dict


def number(x, positive=False):
    value=float(x)
    if not math.isfinite(value) or (positive and value<=0):
        raise ValueError('Nonfinite/nonpositive number')
    return value


class Book:
    """Bybit deltas are absolute quantities, not additions; seq is not consecutive."""
    def __init__(self):
        self.bids={};self.asks={};self.sequence=-1;self.update=-1;self.valid=False

    def invalidate(self):
        self.valid=False;self.bids.clear();self.asks.clear()

    def apply(self, data, snapshot):
        u,seq=int(data['u']),int(data.get('seq',data['u']))
        if not snapshot and not self.valid:
            raise ValueError('Delta without snapshot')
        if not snapshot and (seq<self.sequence or u<self.update):
            self.invalidate();raise ValueError('Reversed orderbook sequence')
        if not snapshot and seq==self.sequence and u==self.update:
            return False
        if snapshot:
            self.bids={};self.asks={}
        for key,side in (('b',self.bids),('a',self.asks)):
            for price,qty in data.get(key,[]):
                p,q=number(price,True),number(qty)
                if q<0: self.invalidate();raise ValueError('Negative book quantity')
                if q: side[p]=q
                else: side.pop(p,None)
        if not self.bids or not self.asks or max(self.bids)>=min(self.asks):
            self.invalidate();raise ValueError('Empty/crossed book')
        self.sequence,self.update,self.valid=seq,u,True
        return True

    def view(self):
        return dict(bids=sorted(self.bids.items(),reverse=True)[:50],
                    asks=sorted(self.asks.items())[:50],sequence=self.sequence)


class Normalizer:
    def __init__(self):
        self.last_seq=0;self.last_received=-1
        self.books={};self.seen=OrderedDict()

    def _unique(self,key):
        if key in self.seen:return False
        self.seen[key]=True
        if len(self.seen)>100000:self.seen.popitem(last=False)
        return True

    def feed(self, raw):
        seq,now=int(raw['seq']),int(raw['received_ms'])
        if seq!=self.last_seq+1 or now<self.last_received:
            raise ValueError('Missing/reordered raw receipt; do not sort by exchange timestamp')
        self.last_seq,self.last_received=seq,now
        source,kind,p=raw['source'],raw['kind'],raw['payload']
        venue='binance_perp' if source=='binance_book' else source
        def event(k,d,ts=None):return Event(now,now if ts is None else int(ts),venue,k,d)
        if kind in ('error','disconnect','timeout','capture_end'):
            if venue in self.books:self.books[venue].invalidate()
            return [event('gap',{'reason':kind,'channel':'book' if source=='binance_book' else 'all'})]
        if kind in ('connected','heartbeat'):
            return [event('health',{'status':kind,'liquidations':False})]
        if kind=='settlement':
            if not p.get('realized') or not p.get('mark_is_settlement'):
                raise ValueError('Predicted funding is not a settlement')
            if not self._unique((venue,'funding',int(p['time']))):return []
            return [event('funding',dict(rate=number(p['rate']),mark=number(p['mark'],True)),p['time'])]
        if kind=='rest':return self._rest(source,p,now)
        if kind!='message':raise ValueError('Unknown raw envelope')
        p=p.get('data',p) if 'stream' in p else p
        out=[event('health',{'status':'message','liquidations':venue=='binance_perp' and source!='binance_book'})]
        if venue.startswith('bybit'):
            if p.get('op')=='subscribe':
                if p.get('success') is not True:raise ValueError('Subscription rejected')
                return [event('health',{'status':'subscribed','liquidations':venue=='bybit_perp'})]
            topic=p.get('topic','');d=p.get('data',{});ts=p.get('ts',now)
            if topic and topic.rsplit('.',1)[-1]!='BTCUSDT':raise ValueError('Unexpected topic symbol')
            if topic.startswith('orderbook.'):
                book=self.books.setdefault(venue,Book())
                if book.apply(d,p.get('type')=='snapshot' or int(d['u'])==1):
                    out.append(event('book',book.view(),p.get('cts',ts)))
            elif topic.startswith('publicTrade.'):
                for x in d:
                    if x.get('s')!='BTCUSDT':raise ValueError('Unexpected symbol')
                    if x['S'] not in ('Buy','Sell'):raise ValueError('Unknown trade direction')
                    if self._unique((venue,'trade',str(x['i']))):
                        out.append(event('trade',dict(side=1 if x['S']=='Buy' else -1,
                            price=number(x['p'],True),qty=number(x['v'],True)),x['T']))
            elif topic.startswith('allLiquidation.'):
                for index,x in enumerate(d):
                    if x['S'] not in ('Buy','Sell'):raise ValueError('Unknown liquidated position side')
                    key=(venue,'liquidation',ts,index,x['T'],x['S'],x['v'],x['p'])
                    if self._unique(key):
                        out.append(event('liquidation',dict(side=-1 if x['S']=='Buy' else 1,
                            qty=number(x['v'],True),reference_price=number(x['p'],True),
                            coverage='venue_all',price_kind='bankruptcy_not_execution'),x['T']))
            elif topic.startswith('kline.'):
                for x in d:
                    if x.get('confirm') is True:
                        out.append(event('bar',self._bar(int(x['start'])+60000,x['open'],x['high'],x['low'],x['close'],x['volume'],x['turnover']),int(x['start'])+60000))
            elif topic.startswith('tickers.'):
                fields={}
                for old,new in (('markPrice','mark'),('openInterest','open_interest'),('fundingRate','predicted_funding'),('nextFundingTime','next_funding')):
                    if old in d:fields[new]=number(d[old])
                if fields:out.append(event('context',fields,ts))
            return out
        if p.get('s','BTCUSDT')!='BTCUSDT':raise ValueError('Unexpected Binance symbol')
        tag=p.get('e')
        if tag in ('aggTrade','trade'):
            if type(p['m']) is not bool:raise ValueError('Aggressor flag must be boolean')
            if self._unique((venue,'trade',p.get('a',p.get('t')))):
                out.append(event('trade',dict(side=-1 if p['m'] else 1,price=number(p['p'],True),qty=number(p['q'],True)),p['T']))
        elif tag=='bookTicker' or all(k in p for k in ('b','B','a','A','u')):
            b,a=number(p['b'],True),number(p['a'],True)
            if b>=a:raise ValueError('Crossed BBO')
            out.append(event('book',dict(bids=[(b,number(p['B'],True))],asks=[(a,number(p['A'],True))],sequence=int(p['u']),depth_kind='L1'),p.get('T',p.get('E',now))))
        elif tag=='forceOrder':
            x=p['o']
            if x['S'] not in ('BUY','SELL'):raise ValueError('Unknown liquidation order direction')
            qty=number(x.get('z',0))
            if qty>0 and self._unique((venue,'liq',x['T'],x['S'],x['q'],x.get('z'))):
                out.append(event('liquidation',dict(side=1 if x['S']=='BUY' else -1,qty=qty,
                    reference_price=number(x.get('ap') or x['p'],True),coverage='sampled_1s',price_kind='observed_order_not_our_fill'),x['T']))
        elif tag=='kline' and p['k'].get('x') is True:
            x=p['k'];end=int(x['t'])+60000
            out.append(event('bar',self._bar(end,x['o'],x['h'],x['l'],x['c'],x['v'],x['q']),end))
        elif tag=='markPriceUpdate':
            out.append(event('context',dict(mark=number(p['p'],True),predicted_funding=number(p['r']),next_funding=int(p['T'])),p['E']))
        return out

    @staticmethod
    def _bar(end,o,h,l,c,v,q):
        o,h,l,c=[number(x,True) for x in (o,h,l,c)];v,q=number(v),number(q)
        if not l<=min(o,c)<=max(o,c)<=h or min(v,q)<0 or end%60000:
            raise ValueError('Invalid closed minute')
        return dict(end=end,open=o,high=h,low=l,close=c,volume=v,quote_volume=q)

    def _rest(self,source,p,now):
        if source.endswith('_bars'):
            venue=source[:-5]
            if source.startswith('bybit'):
                if p.get('retCode')!=0:raise ValueError('REST error')
                rows=sorted(p['result']['list'],key=lambda x:int(x[0]))
            else:rows=p
            events=[]
            for x in rows:
                end=int(x[0])+60000
                if end<=now:
                    quote=x[6] if source.startswith('bybit') else x[7]
                    events.append(Event(now,end,venue,'bar',self._bar(end,*x[1:6],quote)))
            return events
        if source=='bybit_instrument':
            if p.get('retCode')!=0:raise ValueError('REST instrument error')
            x=p['result']['list'][0]
            if x['symbol']!='BTCUSDT':raise ValueError('Wrong instrument')
            lot=x['lotSizeFilter']
            d=dict(qty_step=number(lot['qtyStep'],True),min_qty=number(lot['minOrderQty'],True),
                min_notional=number(lot['minNotionalValue'],True),tick=number(x['priceFilter']['tickSize'],True),
                funding_interval_ms=int(x['fundingInterval'])*60000)
            return [Event(now,now,'bybit_perp','instrument',d)]
        if source=='binance_instrument':
            x=next(x for x in p['symbols'] if x['symbol']=='BTCUSDT')
            filters={x['filterType']:x for x in x['filters']};lot=filters['LOT_SIZE']
            d=dict(qty_step=number(lot['stepSize'],True),min_qty=number(lot['minQty'],True),
                min_notional=number(filters['MIN_NOTIONAL']['notional'],True),tick=number(filters['PRICE_FILTER']['tickSize'],True))
            return [Event(now,now,'binance_perp','instrument',d)]
        return []
