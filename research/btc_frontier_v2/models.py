"""Predeclared causal strategy families. Input index is minute CLOSE time."""
from dataclasses import dataclass
import itertools
import numpy as np
import pandas as pd

FAMILIES=('breakout','trend_pullback','impulse_follow','impulse_fade',
          'range_revert','squeeze','trend_channel','funding_revert')

@dataclass(frozen=True)
class Config:
    family: str
    timeframe: int
    lookback: int
    profile: str
    @property
    def id(self): return f'{self.family}_t{self.timeframe}_w{self.lookback}_{self.profile}'


def grid():
    return [Config(f,t,w,p) for f in FAMILIES
            for t in ([15,60,240,1440] if f=='trend_channel' else [1,5,15,60])
            for w,p in itertools.product([12,48],['fast','wide'])]


def aggregate(d, timeframe):
    spec={'open':'first','high':'max','low':'min','close':'last',
          'volume':'sum','quote_volume':'sum','taker_buy_volume':'sum'}
    b=d.resample(f'{timeframe}min',closed='right',label='right').agg(spec)
    count=d.close.resample(f'{timeframe}min',closed='right',label='right').count()
    return b.loc[count==timeframe].copy()


def prepare_bars(b, funding):
    c=b.close; prev=c.shift()
    tr=pd.concat([b.high-b.low,(b.high-prev).abs(),(b.low-prev).abs()],axis=1).max(axis=1)
    b=b.copy();b['atr']=tr.ewm(alpha=1/14,adjust=False,min_periods=60).mean()
    b['ema20']=c.ewm(span=20,adjust=False,min_periods=60).mean()
    b['ema60']=c.ewm(span=60,adjust=False,min_periods=60).mean()
    b['flow']=b.taker_buy_volume/b.volume.replace(0,np.nan)
    # A funding observation is available only AFTER its actual publication timestamp.
    known=pd.Series(funding.last_funding_rate.to_numpy(),
                    index=pd.to_datetime(funding.calc_time,unit='ms',utc=True))
    b['last_funding']=known.reindex(b.index,method='ffill')
    return b


def signals(d, prepared, cfg):
    b=prepared[cfg.timeframe]; w=cfg.lookback
    c,o,a=b.close,b.open,b.atr
    hi=b.high.rolling(w,min_periods=w).max().shift()
    lo=b.low.rolling(w,min_periods=w).min().shift()
    half=max(2,w//2)
    exit_hi=b.high.rolling(half,min_periods=half).max().shift()
    exit_lo=b.low.rolling(half,min_periods=half).min().shift()
    trend=np.sign(b.ema20-b.ema60)
    vwap=b.quote_volume.rolling(w).sum()/b.volume.rolling(w).sum()
    sd=c.rolling(w,min_periods=w).std()
    relvol=b.volume/b.volume.rolling(w,min_periods=w).mean().shift()
    f=cfg.family
    if f=='breakout':
        long=(c>hi)&(b.flow>.53);short=(c<lo)&(b.flow<.47)
    elif f=='trend_channel':
        long=c>hi;short=c<lo
    elif f=='trend_pullback':
        long=(trend>0)&(b.low<=b.ema20)&(c>b.ema20)&(c>o)&(b.flow>.5)
        short=(trend<0)&(b.high>=b.ema20)&(c<b.ema20)&(c<o)&(b.flow<.5)
    elif f in ('impulse_follow','impulse_fade'):
        impulse=(abs(c-o)>a.shift()*1.5)&(relvol>2)
        up=impulse&(c>o)&(b.flow>.6);down=impulse&(c<o)&(b.flow<.4)
        long,short=(up,down) if f=='impulse_follow' else (down,up)
    elif f=='range_revert':
        quiet=abs(b.ema20-b.ema60)<a
        long=quiet&(c<vwap-1.5*sd)&(c>o)
        short=quiet&(c>vwap+1.5*sd)&(c<o)
    elif f=='squeeze':
        width=4*sd/c
        squeeze=width.shift()<width.rolling(2*w,min_periods=w).quantile(.25).shift()
        long=squeeze&(c>hi)&(relvol>1.2)&(b.flow>.55)
        short=squeeze&(c<lo)&(relvol>1.2)&(b.flow<.45)
    elif f=='funding_revert':
        long=(b.last_funding<-.0003)&(c<vwap-a)&(c>o)
        short=(b.last_funding>.0003)&(c>vwap+a)&(c<o)
    else: raise ValueError('Unknown family')
    ready=np.isfinite(a)&np.isfinite(hi)&np.isfinite(lo)
    side=long.where(ready,False).astype('int8')-short.where(ready,False).astype('int8')
    stop_atr=1.5 if cfg.profile=='fast' else 3.
    sf=(a*stop_atr/c).clip(lower=.001,upper=.05)
    # No incomplete aggregate is forward-filled into a signal. Only closed timestamps emit.
    sig=side.reindex(d.index,fill_value=0).to_numpy(np.int8)
    stop=sf.reindex(d.index,method='ffill').fillna(.001).to_numpy(float)
    exits=((c<exit_lo).astype('int8')-(c>exit_hi).astype('int8'))
    xs=exits.reindex(d.index,fill_value=0).to_numpy(np.int8)
    if f!='trend_channel': xs[:]=0
    hold=cfg.timeframe*(288 if f=='trend_channel' else 12 if cfg.profile=='fast' else 48)
    rr=0. if f=='trend_channel' else 2. if cfg.profile=='fast' else 3.
    return sig,stop,xs,hold,rr,f=='trend_channel'
