"""Closed-hour target states. Neither funding rates nor future returns are features.

Pair weights are dollar-neutral at target creation, not a claim of permanent beta
neutrality or of cointegration. The account simulator owns actual quantities.
"""
import numpy as np
import pandas as pd
from .data import SYMBOLS

PRIMARY='pair_revert168'
CONTROL='directional_trend_ensemble'
NAMES=(PRIMARY,'pair_revert720','pair_revert168_stable','pair_momentum168','pair_momentum720',
       'pair_breakout72','directional_breakout72',CONTROL,'btc_trend_ensemble','cash')


def stateful(values,entry,exit,valid,max_hold):
    values=np.asarray(values,float);entry=np.asarray(entry,int);exit=np.asarray(exit,bool)
    valid=np.asarray(valid,bool)
    if len({len(values),len(entry),len(exit),len(valid)})!=1:raise ValueError('State arrays misaligned')
    out=np.zeros(len(values));side=0;start=-1
    for i in range(len(out)):
        if not valid[i]:side=0;continue
        if side:
            # exit may encode side-specific rows: callers also supply current direction.
            if exit[i] or i-start>=max_hold or entry[i]==-side:
                side=0;continue
        elif entry[i]:side=int(entry[i]);start=i
        out[i]=side
    return out


def breakout(price,high,low,valid):
    previous_high=high.rolling(72,min_periods=72).max().shift()
    previous_low=low.rolling(72,min_periods=72).min().shift()
    midpoint=(high.rolling(24).max().shift()+low.rolling(24).min().shift())/2
    out=np.zeros(len(price));side=0;begun=-1
    for i in range(len(out)):
        if not valid[i] or not np.isfinite(previous_high.iloc[i]) or not np.isfinite(midpoint.iloc[i]):side=0;continue
        if side:
            if side*(price.iloc[i]-midpoint.iloc[i])<0 or i-begun>=168:side=0;continue
        elif price.iloc[i]>previous_high.iloc[i]:side=1;begun=i
        elif price.iloc[i]<previous_low.iloc[i]:side=-1;begun=i
        out[i]=side
    return out


def build(frames):
    if set(frames)!=set(SYMBOLS):raise ValueError('Fixed BTC/ETH universe required')
    idx=frames[SYMBOLS[0]].index
    if str(idx.tz)!='UTC' or idx.has_duplicates or not idx.is_monotonic_increasing:
        raise ValueError('Unique ordered UTC grid required')
    if len(idx)>1 and not np.all(np.diff(idx.asi8)==3600000000000):raise ValueError('Gaps must retain rows')
    if any(not f.index.equals(idx) for f in frames.values()):raise ValueError('Different grids')
    close=pd.DataFrame({s:frames[s].close for s in SYMBOLS})
    lp=np.log(close);ratio=lp.ETHUSDT-lp.BTCUSDT
    support=close.notna().all(axis=1).rolling(721,min_periods=721).sum().eq(721).to_numpy()
    std=ratio.diff().rolling(168,min_periods=168).std().replace(0,np.nan)
    lag=ratio.shift();change=ratio-lag
    phi=1+change.rolling(720).cov(lag).shift()/lag.rolling(720).var().shift().replace(0,np.nan)
    half=-np.log(2)/np.log(phi.where((phi>0)&(phi<1)))
    stable=half.between(4,168).to_numpy()
    targets={};traces={}
    def pair(name,side):
        targets[name]=np.column_stack([-.5*side,.5*side])
    for window in (168,720):
        mu=ratio.rolling(window,min_periods=window).mean().shift()
        deviation=ratio-mu;sd=ratio.rolling(window,min_periods=window).std().shift().replace(0,np.nan)
        z=(deviation/sd).to_numpy()
        for filtered in ((False,True) if window==168 else (False,)):
            valid=support&np.isfinite(z)
            if filtered:valid &= stable
            entry=np.where(valid&(np.abs(z)>=2)&(np.abs(z)<4)&(np.abs(deviation.to_numpy())/2>.0024),-np.sign(z),0).astype(int)
            side=np.zeros(len(idx));direction=0;entered=-1
            for i in range(len(idx)):
                if not valid[i]:direction=0;continue
                if direction:
                    if abs(z[i])<=.25 or direction*z[i]>=0 or abs(z[i])>=4 or i-entered>=(72 if window==168 else 168):
                        direction=0;continue
                elif entry[i]:direction=int(entry[i]);entered=i
                side[i]=direction
            name='pair_revert'+str(window)+('_stable' if filtered else '')
            pair(name,side);traces[name]=z
        momentum=ratio-ratio.shift(window)
        standardized=momentum/(std*np.sqrt(window))
        v=standardized.to_numpy();valid=support&np.isfinite(v)
        entry=np.where(valid&(np.abs(v)>=1.5),np.sign(v),0).astype(int)
        side=np.zeros(len(idx));direction=0;entered=-1
        for i in range(len(idx)):
            if not valid[i]:direction=0;continue
            if direction:
                if abs(v[i])<.25 or direction*v[i]<0 or i-entered>=168:direction=0;continue
            elif entry[i]:direction=int(entry[i]);entered=i
            side[i]=direction
        pair('pair_momentum'+str(window),side)
    pair('pair_breakout72',breakout(ratio,ratio,ratio,support))
    directions=np.column_stack([breakout(frames[s].close,frames[s].high,frames[s].low,support) for s in SYMBOLS])
    gross=np.abs(directions).sum(axis=1)
    targets['directional_breakout72']=directions/np.maximum(gross,1)[:,None]
    votes=sum(np.sign(lp-lp.shift(n)) for n in (24,168,720))/3
    trend=np.zeros((len(idx),2))
    for k in range(2):
        direction=0
        for i in range(len(idx)):
            v=votes.iloc[i,k]
            if not support[i] or not np.isfinite(v):direction=0
            elif direction and (abs(v)<1/3 or v*direction<0):direction=0
            elif not direction and abs(v)>=2/3:direction=int(np.sign(v))
            trend[i,k]=direction
    targets[CONTROL]=trend/np.maximum(np.abs(trend).sum(axis=1),1)[:,None]
    targets['btc_trend_ensemble']=np.column_stack([trend[:,0],np.zeros(len(idx))])
    targets['cash']=np.zeros((len(idx),2))
    if set(targets)!=set(NAMES):raise AssertionError('Model registry differs from protocol')
    for name,w in targets.items():
        if not np.isfinite(w).all() or (np.abs(w).sum(axis=1)>1+1e-10).any():raise AssertionError('Invalid gross weights')
    diagnostic=pd.DataFrame(dict(time=idx.astype(str),support=support,log_ratio=ratio.to_numpy(),
        ratio_z168=traces[PRIMARY],lagged_half_life=half.to_numpy()))
    return targets,diagnostic
