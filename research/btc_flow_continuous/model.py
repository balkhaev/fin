"""A new minute model, not the original second-resolution BTC Pressure."""
from dataclasses import dataclass
import itertools
import numpy as np
import pandas as pd

FAMILIES=('confirmed_breakout','spot_absorption','perp_exhaustion')
PROFILES={'quick':(1.5,2.,15,False),'intraday':(2.,3.,120,False),'runner':(3.,0.,1440,True)}

@dataclass(frozen=True)
class Config:
    family:str
    window:int
    threshold:float
    profile:str
    @property
    def id(self):return f'{self.family}_w{self.window}_q{self.threshold:g}_{self.profile}'


def grid():
    return [Config(*x) for x in itertools.product(FAMILIES,(1,5,15),(.10,.25),PROFILES)]


def features(d):
    c=d.close;prev=c.shift()
    tr=pd.concat([d.high-d.low,(d.high-prev).abs(),(d.low-prev).abs()],axis=1).max(axis=1)
    atr=tr.ewm(alpha=1/14,adjust=False,min_periods=60).mean()
    ready=d.spot_close.notna().rolling(61,min_periods=61).sum().eq(61)&atr.notna()
    basis=np.log(c/d.spot_close)
    bm=basis.rolling(60,min_periods=60).mean().shift()
    bs=basis.rolling(60,min_periods=60).std().shift().replace(0,np.nan)
    f=dict(atr=atr.to_numpy(),ready=ready.to_numpy(),
           hi=d.high.rolling(20,min_periods=20).max().shift().to_numpy(),
           lo=d.low.rolling(20,min_periods=20).min().shift().to_numpy(),
           vwap=(d.quote_volume.rolling(60).sum()/d.volume.rolling(60).sum()).to_numpy(),
           spottrend=(d.spot_close-d.spot_close.shift(20)).to_numpy(),bz=((basis-bm)/bs).to_numpy(),flow={})
    for w in (1,5,15):
        sf=((2*d.spot_buy_quote-d.spot_quote_volume).rolling(w).sum()/d.spot_quote_volume.rolling(w).sum().replace(0,np.nan))
        pf=((2*d.buy_quote-d.quote_volume).rolling(w).sum()/d.quote_volume.rolling(w).sum().replace(0,np.nan))
        impulse=(c.shift()/c.shift(w+1)-1)
        f['flow'][w]=(sf.to_numpy(),pf.to_numpy(),impulse.to_numpy())
    return f


def signals(d,f,cfg):
    if cfg not in grid():raise ValueError('Configuration outside frozen grid')
    sf,pf,imp=f['flow'][cfg.window];q=cfg.threshold
    c=d.close.to_numpy();o=d.open.to_numpy()
    if cfg.family=='confirmed_breakout':
        long=(sf>q)&(pf>q)&(c>f['hi'])&(f['spottrend']>0)
        short=(sf<-q)&(pf<-q)&(c<f['lo'])&(f['spottrend']<0)
    elif cfg.family=='spot_absorption':
        long=(sf>q)&(pf<-q)&(c>o)&(c<f['vwap'])
        short=(sf<-q)&(pf>q)&(c<o)&(c>f['vwap'])
    else:
        long=(pf<-q)&(sf>-q)&(imp<-f['atr']/c)&(f['bz']<-1.5)&(c>o)
        short=(pf>q)&(sf<q)&(imp>f['atr']/c)&(f['bz']>1.5)&(c<o)
    result=np.where(long&f['ready'],1,np.where(short&f['ready'],-1,0)).astype(np.int8)
    atr_mult,rr,hold,trailing=PROFILES[cfg.profile]
    stop=np.maximum(.001,f['atr']*atr_mult/c)
    result[~np.isfinite(stop)|(stop>.05)]=0
    # Conditions are re-evaluated each closed minute; a held position cannot add.
    # The broker enforces one idle minute following an exit before a new entry.
    return result,stop,rr,hold,trailing
