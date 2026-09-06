"""Closed-bar features shared by archived replay and the public-data producer.

Pandas is an optional research/producer dependency, not an execution dependency.
Forward fill applies only to already closed indicator states, never to prices.
"""
from dataclasses import dataclass, asdict
import math
from . import FAMILIES
from finruntime.canonical import sha256_id

HOUR = 3600000

@dataclass(frozen=True)
class Frame:
    time_ms: int
    close: float
    atr_hour: float
    atr_day: float
    atr_four: float
    ema20: float
    mean20: float
    std20: float
    rsi2: float
    efficiency: float
    daily_up: bool
    breakout: bool
    healthy: bool = True

    def validate(self):
        if type(self.time_ms) is not int or self.time_ms % HOUR:
            raise ValueError('Frame must end on a UTC hour')
        for k, v in asdict(self).items():
            if k not in ('time_ms', 'daily_up', 'breakout', 'healthy') and not math.isfinite(v):
                raise ValueError('Nonfinite feature: '+k)
        if self.close <= 0 or min(self.atr_hour,self.atr_day,self.atr_four,self.std20) < 0:
            raise ValueError('Invalid feature prices')
        if not 0 <= self.rsi2 <= 100 or not 0 <= self.efficiency <= 1.000001:
            raise ValueError('Invalid bounded indicator')
        if any(type(getattr(self,k)) is not bool for k in ('daily_up','breakout','healthy')):
            raise ValueError('Boolean quality/condition required')

    @property
    def identity(self):
        self.validate()
        return sha256_id(asdict(self))

@dataclass(frozen=True)
class Opportunity:
    family: str
    signal_ms: int
    stop_fraction: float
    target_fraction: float
    hold_hours: int
    reason: str


def scan(frame: Frame):
    frame.validate()
    if not frame.healthy: return []
    c=frame.close; out=[]
    def add(family, stop, target, hold, reason):
        if 0 < stop <= .20 and math.isfinite(target) and target > 0:
            out.append(Opportunity(family,frame.time_ms,stop,target,hold,reason))
    if frame.daily_up:
        stop=max(.05,2*frame.atr_day/c)
        add('daily_trend',stop,3*stop,720,'daily EMA10/50 trend')
        if c<frame.ema20 and frame.rsi2<15:
            stop=max(.01,2*frame.atr_hour/c)
            add('trend_pullback',stop,2*stop,24,'hourly oversold in positive daily trend')
        if frame.breakout:
            stop=max(.02,2*frame.atr_four/c)
            add('breakout',stop,3*stop,96,'fresh closed4h channel55 breakout')
    if frame.efficiency<.25 and c<frame.mean20-2*frame.std20 and frame.rsi2<10:
        stop=max(.01,2*frame.atr_hour/c)
        add('range_rebound',stop,frame.mean20/c-1,12,'range overshoot toward preceding mean')
    return out


def build_frames(data):
    import numpy as np
    import pandas as pd
    if data.index.tz is None or str(data.index.tz)!='UTC':
        raise ValueError('UTC-indexed hours required')
    if data.index.has_duplicates or not data.index.is_monotonic_increasing:
        raise ValueError('Nonunique/reversed hourly data')
    if len(data)>1 and not np.all(np.diff(data.index.asi8)==HOUR*1000000):
        raise ValueError('Gaps must be explicit NaN rows, not dropped')
    def aggregate(h):
        b=data.resample(f'{h}h',closed='left',label='right').agg(
            {'open':'first','high':'max','low':'min','close':'last','volume':'sum'})
        count=data.close.resample(f'{h}h',closed='left',label='right').count()
        b.loc[count!=h,:]=np.nan
        return b
    def indicators(b):
        result=pd.DataFrame(index=b.index)
        groups=b.close.isna().cumsum()
        for _,v in b.loc[b.close.notna()].groupby(groups[b.close.notna()]):
            c=v.close; delta=c.diff()
            tr=pd.concat([v.high-v.low,(v.high-c.shift()).abs(),(v.low-c.shift()).abs()],axis=1).max(axis=1)
            result.loc[v.index,'atr']=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
            for n in (10,20,50):result.loc[v.index,f'ema{n}']=c.ewm(span=n,adjust=False,min_periods=n).mean()
            result.loc[v.index,'mean20']=c.rolling(20).mean().shift()
            result.loc[v.index,'std20']=c.rolling(20).std().shift()
            gain=delta.clip(lower=0).ewm(alpha=.5,adjust=False,min_periods=2).mean()
            loss=(-delta.clip(upper=0)).ewm(alpha=.5,adjust=False,min_periods=2).mean()
            result.loc[v.index,'rsi2']=(100*gain/(gain+loss).replace(0,np.nan)).fillna(50)
            result.loc[v.index,'efficiency']=(c-c.shift(20)).abs()/delta.abs().rolling(20).sum().replace(0,np.nan)
            result.loc[v.index,'channel55']=v.high.rolling(55).max().shift()
        result['close']=b.close
        return result
    h=indicators(aggregate(1)); four=indicators(aggregate(4)); day=indicators(aggregate(24))
    f=four.reindex(h.index,method='ffill');d=day.reindex(h.index,method='ffill')
    good=(h[['close','atr','ema20','mean20','std20','rsi2','efficiency']].notna().all(axis=1)
          & d[['close','atr','ema10','ema50']].notna().all(axis=1)
          & f[['atr','channel55']].notna().all(axis=1))
    rows=[]
    for i,t in enumerate(h.index):
        healthy=bool(good.iloc[i]);v=h.iloc[i];dv=d.iloc[i];fv=f.iloc[i]
        def finite(x,default=0.):return float(x) if np.isfinite(x) else default
        frame=Frame(int(t.timestamp()*1000),finite(v.close,1.),finite(v.atr),finite(dv.atr),
            finite(fv.atr),finite(v.ema20),finite(v.mean20),finite(v.std20),finite(v.rsi2,50),
            min(1.,max(0.,finite(v.efficiency))),bool(dv.ema10>dv.ema50 and dv.close>dv.ema50),
            bool(t.hour%4==0 and fv.close>fv.channel55),healthy)
        rows.append(frame)
    return rows
