"""Three causal event mechanisms, not a fitted return promise."""
from __future__ import annotations
from collections import defaultdict, deque
from dataclasses import dataclass
import math
from statistics import median
from .adapters import Event


@dataclass(frozen=True)
class Proposal:
    time: int
    family: str
    side: int
    entry: float
    stop: float
    target: float
    hold_ms: int
    passive: bool
    reason: str

    def __post_init__(self):
        if self.family not in ('spot_trend','cascade','absorption') or self.side not in (-1,1):
            raise ValueError('Unknown mechanism/direction')
        if not all(math.isfinite(x) and x>0 for x in (self.entry,self.stop,self.target)):
            raise ValueError('Invalid proposal prices')
        if self.side*(self.entry-self.stop)<=0 or self.side*(self.target-self.entry)<=0 or self.hold_ms<=0:
            raise ValueError('Stop/target on wrong side')


@dataclass(frozen=True)
class Frame:
    time: int
    price: float
    atr: float
    range_high: float
    range_low: float
    consensus: int
    spot_bias: float
    perp_flow: float
    bid_refill: float
    ask_refill: float
    high10: float
    low10: float
    prior_high10: float
    prior_low10: float
    liquidation_side: int


def quantile(values,q):
    a=sorted(values)
    if not a:return 0.
    return a[min(len(a)-1,int((len(a)-1)*q))]


class Flow:
    """Second buckets bound CPU/memory; trade prices/amounts are never synthesized."""
    def __init__(self):self.buckets=deque()
    def add(self,t,price,qty,side):
        second=t//1000
        if not self.buckets or self.buckets[-1][0]!=second:
            self.buckets.append([second,0.,0.,price,price,price])
        b=self.buckets[-1];b[1 if side==1 else 2]+=price*qty
        b[3]=min(b[3],price);b[4]=max(b[4],price);b[5]=price
        while self.buckets and self.buckets[0][0]<second-600:self.buckets.popleft()
    def view(self,t,seconds,offset=0):
        end=t//1000-offset;start=end-seconds
        rows=[x for x in self.buckets if start<x[0]<=end]
        buy=sum(x[1] for x in rows);sell=sum(x[2] for x in rows)
        return dict(flow=(buy-sell)/(buy+sell) if buy+sell else 0.,amount=buy+sell,
                    low=min((x[3] for x in rows),default=math.inf),
                    high=max((x[4] for x in rows),default=-math.inf))


class Features:
    def __init__(self,venue='bybit_perp'):
        self.venue=venue;self.flows=defaultdict(Flow);self.bars=defaultdict(lambda:deque(maxlen=121))
        self.books={};self.health={};self.liquidations=defaultdict(Flow)
        self.liq_live=set();self.ratios=defaultdict(lambda:deque(maxlen=180))
        self.depths=deque(maxlen=60);self.last_sample=-1;self.started=None
        self.last_trade={};self.gaps=0

    def add(self,e:Event):
        now=e.received
        if e.kind=='health':
            self.health[e.source]=now
            if e.data.get('liquidations'):self.liq_live.add(e.source)
            return
        if e.kind=='gap':
            self.books.pop(e.source,None);self.liq_live.discard(e.source);self.health.pop(e.source,None)
            self.flows.pop(e.source,None);self.liquidations.pop(e.source,None)
            self.last_trade.pop(e.source,None);self.ratios[e.source].clear()
            self.started=None;self.gaps+=1
            return
        if e.kind=='bar':
            b=e.data
            if b['end']>now:raise ValueError('Unclosed bar')
            rows=self.bars[e.source]
            if rows and b['end']==rows[-1]['end']:return
            if rows and b['end']<rows[-1]['end']:raise ValueError('Reordered closed bars')
            if rows and b['end']!=rows[-1]['end']+60000:rows.clear()
            rows.append(b);return
        if now-e.occurred>5000 or e.occurred>now+500:
            if e.kind=='book':self.books.pop(e.source,None)
            return
        if e.kind=='trade':
            self.flows[e.source].add(now,e.data['price'],e.data['qty'],e.data['side'])
            self.last_trade[e.source]=now
            if self.started is None:self.started=now
        elif e.kind=='book':self.books[e.source]=(now,e.data)
        elif e.kind=='liquidation':
            self.liquidations[e.source].add(now,e.data['reference_price'],e.data['qty'],e.data['side'])

    def frame(self,now):
        sources=('binance_spot','bybit_spot',self.venue)
        if self.started is None or now-self.started<300000:return None,'flow_warmup'
        if any(now-self.health.get(s,-10**15)>20000 or now-self.last_trade.get(s,-10**15)>5000 for s in sources):
            return None,'missing_or_stale_trade_source'
        if self.venue not in self.books or now-self.books[self.venue][0]>1500:return None,'stale_execution_book'
        bars=self.bars[self.venue]
        if len(bars)<61 or not 0<=now-bars[-1]['end']<65000:return None,'closed_hour_unavailable'
        data=self.books[self.venue][1]
        bid,ask=data['bids'][0][0],data['asks'][0][0];price=(bid+ask)/2
        atr=sum(max(b['high']-b['low'],abs(b['high']-a['close']),abs(b['low']-a['close']))
                for a,b in zip(list(bars)[-15:-1],list(bars)[-14:]))/14
        if atr<=0:return None,'invalid_volatility'
        ratios=[self.flows[s].view(now,300)['flow'] for s in sources[:2]]
        thresholds=[max(.2,quantile(self.ratios[s],.8)) for s in sources[:2]]
        ready=all(len(self.ratios[s])>=30 for s in sources[:2])
        consensus=1 if all(r>q for r,q in zip(ratios,thresholds)) else -1 if all(r < -q for r,q in zip(ratios,thresholds)) else 0
        if not ready:consensus=0
        bq=sum(q for _,q in data['bids'][:5]);aq=sum(q for _,q in data['asks'][:5])
        bbase=median([x[0] for x in self.depths]) if self.depths else bq
        abase=median([x[1] for x in self.depths]) if self.depths else aq
        recent=self.flows[self.venue].view(now,10);previous=self.flows[self.venue].view(now,10,10)
        if not math.isfinite(recent['low']) or not math.isfinite(previous['low']):return None,'thin_trade_history'
        liq=self.liquidations[self.venue].view(now,10)
        history=[self.liquidations[self.venue].view(now,10,x)['amount'] for x in range(10,310,10)]
        burst=liq['amount']>max(50000.,quantile(history,.95)*3)
        active=self.venue in self.liq_live and now-self.health.get(self.venue,-10**15)<20000
        liqside=(1 if liq['flow']>.5 else -1 if liq['flow']<-.5 else 0) if burst and active else 0
        frame=Frame(now,price,atr,max(b['high'] for b in list(bars)[-60:]),min(b['low'] for b in list(bars)[-60:]),
                    consensus,sum(self.flows[s].view(now,60)['flow'] for s in sources[:2])/2,
                    self.flows[self.venue].view(now,30)['flow'],bq/max(bbase,1e-12),aq/max(abase,1e-12),
                    recent['high'],recent['low'],previous['high'],previous['low'],liqside)
        if now//10000!=self.last_sample:
            for s,r in zip(sources[:2],ratios):self.ratios[s].append(abs(r))
            self.last_sample=now//10000
        self.depths.append((bq,aq))
        return frame,'ready'


class Mechanisms:
    def __init__(self):
        self.trend=None;self.cascade=None;self.last_time=-1;self.last_liq=0

    @staticmethod
    def proposal(f,family,side,stop,target=None):
        distance=max(abs(f.price-stop),f.price*.001)
        stop=f.price-side*distance
        target=target if target is not None else f.price+side*distance*(3 if family=='spot_trend' else 2)
        if side*(target-f.price)<distance*1.5:return None
        return Proposal(f.time,family,side,f.price,stop,target,
                        7*86400000 if family=='spot_trend' else 300000 if family=='cascade' else 900000,
                        family=='spot_trend','closed-range retest' if family=='spot_trend' else 'failed rebound' if family=='cascade' else 'absorption reclaim')

    def reset(self):self.trend=None;self.cascade=None;self.last_liq=0

    def on_frame(self,f:Frame):
        if f.time<=self.last_time:raise ValueError('Frames must advance')
        self.last_time=f.time
        if self.trend and f.time-self.trend['at']>600000:self.trend=None
        if self.cascade and f.time-self.cascade['at']>600000:self.cascade=None
        old=self.cascade
        if f.liquidation_side and f.liquidation_side!=self.last_liq:
            self.cascade=dict(side=f.liquidation_side,at=f.time,extreme=f.price,anchor=f.price,
                              rebound=None,fired=False,absorb=None,absorbed=False)
        self.last_liq=f.liquidation_side
        c=self.cascade
        if c and c is old:
            s=c['side'];c['extreme']=min(c['extreme'],f.price) if s<0 else max(c['extreme'],f.price)
            refill=f.bid_refill if s<0 else f.ask_refill
            stalled=f.low10>=f.prior_low10 if s<0 else f.high10<=f.prior_high10
            if not c['absorbed'] and c['absorb'] is None and s*f.perp_flow>.3 and stalled and refill>1.2:
                c['absorb']=f.high10 if s<0 else f.low10
            elif c['absorb'] is not None and not c['absorbed'] and -s*(f.price-c['absorb'])>.1*f.atr and -s*f.spot_bias>0:
                c['absorbed']=True
                return self.proposal(f,'absorption',-s,c['extreme']+s*.25*f.atr,c['anchor'])
            if c['rebound'] is None and -s*(f.price-c['extreme'])>.35*f.atr:
                c['rebound']=f.price
            elif c['rebound'] is not None:
                c['rebound']=max(c['rebound'],f.price) if s<0 else min(c['rebound'],f.price)
                if not c['fired'] and not c['absorbed'] and s*(f.price-c['rebound'])>.25*f.atr and s*f.perp_flow>.3 and refill<1:
                    c['fired']=True
                    return self.proposal(f,'cascade',s,c['rebound']-s*.25*f.atr)
        t=self.trend
        if t:
            side,level=t['side'],t['level']
            if side*(f.price-level)<-f.atr:self.trend=None
            elif not t['retested'] and abs(f.price-level)<.25*f.atr:
                t['retested']=True
            elif t['retested'] and not t['fired'] and side*(f.price-level)>.25*f.atr and f.consensus==side:
                t['fired']=True
                return self.proposal(f,'spot_trend',side,level-side*.5*f.atr)
        elif f.consensus and (f.price>f.range_high if f.consensus==1 else f.price<f.range_low):
            self.trend=dict(side=f.consensus,level=f.range_high if f.consensus==1 else f.range_low,
                            at=f.time,retested=False,fired=False)
        return None
