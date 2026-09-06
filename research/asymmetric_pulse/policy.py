"""Exploratory fully funded spot targets, not a live order or margin engine.

All state changes occur after completed daily observations. Event states describe
signals, NOT proof that the executor filled the candidate. Native delayed open
execution remains the only source of actual simulated positions and cashflows.
"""
from dataclasses import dataclass
import math
import numpy as np
import pandas as pd
from research.annual_rotation.data import SYMBOLS
from research.annual_rotation.model import Config,feature_bank,weights
from research.rotation_stability.policy import build as build_guarded,PRIMARY as OLD_PRIMARY

PRIMARY='asymmetric_blend'
NAMES=('early_total','early_downside','early_full','leader_downside','residual_downside',
       'ignition_downside','rebound_downside',PRIMARY,'asymmetric_blend_weekly',
       'guarded_plus_pulse','btc_early_downside')
CONTROLS=('guarded_control','raw126_control','btc_hold','cash')
DAILY=Config('raw',21,3,1)
WEEKLY=Config('raw',126,3,7)


def schedule(name):
    if name not in NAMES+CONTROLS:raise ValueError('Unknown precommitted policy')
    return WEEKLY if name in ('asymmetric_blend_weekly','guarded_control','raw126_control','btc_hold') else DAILY


def segmented_ema(close,span):
    out=pd.DataFrame(np.nan,index=close.index,columns=close.columns)
    for col in close:
        s=close[col];valid=s.notna();groups=(~valid).cumsum()
        for _,part in s[valid].groupby(groups[valid]):
            out.loc[part.index,col]=part.ewm(span=span,adjust=False,min_periods=span).mean()
    return out


def event_states(entry,valid,close,exit_low,max_hold,trail_fraction=None):
    """No same-candle exit/reentry. A gap erases the signal state, not a held coin."""
    arrays=[np.asarray(x) for x in (entry,valid,close,exit_low)]
    if len({x.shape for x in arrays})!=1 or arrays[0].ndim!=2:raise ValueError('Misaligned event inputs')
    entry,valid,close,exit_low=arrays
    if max_hold<1:raise ValueError('Invalid holding period')
    active=np.zeros_like(entry,dtype=bool);begun=np.full(entry.shape[1],-1,int)
    peak=np.zeros(entry.shape[1]);start_count=0
    for t in range(len(entry)):
        for k in range(entry.shape[1]):
            if not valid[t,k] or not math.isfinite(close[t,k]):
                begun[k]=-1;continue
            if begun[k]>=0:
                peak[k]=max(peak[k],close[t,k])
                bad=math.isfinite(exit_low[t,k]) and close[t,k]<exit_low[t,k]
                if trail_fraction is not None:bad|=close[t,k]<=peak[k]*(1-trail_fraction)
                if bad or t-begun[k]>=max_hold:
                    begun[k]=-1;continue
            elif entry[t,k]:
                begun[k]=t;peak[k]=close[t,k];start_count+=1
            active[t,k]=begun[k]>=0
    return active,start_count


def risk_estimate(w,sample,asymmetric=False):
    sample=np.asarray(sample,float);w=np.asarray(w,float)
    if sample.ndim!=2 or sample.shape[1]!=len(w) or len(sample)<60:
        return math.inf
    if not np.isfinite(sample).all() or not np.isfinite(w).all():return math.inf
    total=np.cov(sample,rowvar=False,ddof=1)
    total=np.atleast_2d(total)
    sigma=np.sqrt(np.maximum(np.diag(total),0.)*365.25)
    symmetric=max(math.sqrt(max(float(w@total@w),0.)*365.25),.7*float(w@sigma))
    if not asymmetric:return symmetric
    # Uncentered downside second moment, doubled for comparison with total risk.
    # A total-risk floor prevents a run of up days implying unlimited safe size.
    negative=np.minimum(sample,0.)
    downside=2*negative.T@negative/len(sample)
    dsigma=np.sqrt(np.maximum(np.diag(downside),0.)*365.25)
    return max(math.sqrt(max(float(w@downside@w),0.)*365.25),.7*float(w@dsigma),.25*symmetric)


def rank_target(score,eligible,returns,top=3,mode='downside',exclude=None):
    if mode not in ('total','downside','none') or top not in (1,3):raise ValueError('Uncommitted rank/risk setting')
    if score.shape!=eligible.shape or returns.shape!=score.shape:raise ValueError('Target alignment failure')
    result=np.zeros_like(score,dtype=float)
    columns=[k for k,s in enumerate(SYMBOLS) if s!=exclude]
    risk_trace=np.zeros(len(score))
    for t in range(len(score)):
        available=[k for k in columns if eligible[t,k] and np.isfinite(score[t,k])]
        chosen=sorted(available,key=lambda k:(-score[t,k],SYMBOLS[k]))[:top]
        if not chosen:continue
        w=np.zeros(score.shape[1]);w[chosen]=1/top
        if mode!='none':
            r=risk_estimate(w[columns],returns[max(0,t-59):t+1,columns],mode=='downside')
            if not math.isfinite(r) or r<=1e-12:continue
            w*=min(1.,.5/r);risk_trace[t]=min(r,.5)
        result[t]=w
    return result,risk_trace


def build(frames,exclude=None):
    if set(frames)!=set(SYMBOLS):raise ValueError('Full original cohort required before exclusions')
    if exclude is not None and exclude not in SYMBOLS:raise ValueError('Unknown exclusion')
    idx=frames[SYMBOLS[0]].index
    if idx.tz is None or str(idx.tz)!='UTC' or idx.has_duplicates or not idx.is_monotonic_increasing:
        raise ValueError('Unique chronological UTC daily index required')
    if len(idx)>1 and not np.all(np.diff(idx.asi8)==86400000000000):raise ValueError('Dropped daily rows are not supported')
    if any(not frames[s].index.equals(idx) for s in SYMBOLS):raise ValueError('Misaligned venue data')
    def table(col):return pd.DataFrame({s:frames[s][col] for s in SYMBOLS})
    c,h,l,turnover=table('close'),table('high'),table('low'),table('quote_volume')
    ema20,ema50=segmented_ema(c,20),segmented_ema(c,50)
    lr=np.log(c/c.shift());simple=c.pct_change(fill_method=None)
    total=simple.rolling(60,min_periods=60).std().replace(0,np.nan)*math.sqrt(365.25)
    down=np.sqrt(2*simple.clip(upper=0).pow(2).rolling(60,min_periods=60).mean()*365.25)
    down=pd.DataFrame(np.maximum(down.to_numpy(),.25*total.to_numpy()),index=idx,columns=SYMBOLS).replace(0,np.nan)
    logs={n:np.log(c/c.shift(n)) for n in (3,7,21,63)}
    composite=.5*logs[21]+.3*logs[63]+.2*logs[7]
    support=c.notna().rolling(201,min_periods=201).sum().eq(201)
    liquid=turnover.rolling(30,min_periods=30).mean().ge(5e6)
    valid=support&liquid&total.notna()&down.notna()
    if exclude:valid[exclude]=False
    trend=valid&(c>ema20)&(ema20>ema50)&(logs[7]>0)&(logs[21]>0)
    r=simple.to_numpy(float)
    target={};trace={}
    def rank(name,score,good,top=3,mode='downside'):
        target[name],trace[name]=rank_target(score.to_numpy(float),good.to_numpy(bool),r,top,mode,exclude)
    rank('early_total',composite/total,trend,mode='total')
    rank('early_downside',composite/down,trend)
    rank('early_full',composite,trend,mode='none')
    rank('leader_downside',composite/down,trend,top=1)
    btc=lr.BTCUSDT
    variance=btc.rolling(60,min_periods=60).var().replace(0,np.nan)
    residual=pd.DataFrame(index=idx,columns=SYMBOLS,dtype=float)
    for s in SYMBOLS:
        beta=lr[s].rolling(60,min_periods=60).cov(btc)/variance
        residual[s]=(lr[s]-beta*btc).rolling(21,min_periods=21).sum()
    rank('residual_downside',residual/down,trend&(residual>0))
    previous_high=h.rolling(20,min_periods=20).max().shift()
    previous_low=l.rolling(10,min_periods=10).min().shift()
    prior_turnover=turnover.rolling(20,min_periods=20).median().shift()
    ignition=valid&(c>previous_high)&(turnover>1.5*prior_turnover)
    ignited,ignition_count=event_states(ignition.to_numpy(),valid.to_numpy(),c.to_numpy(),previous_low.to_numpy(),42)
    rank('ignition_downside',composite/down,pd.DataFrame(ignited,index=idx,columns=SYMBOLS))
    # The reference high includes only observations at or before this close.
    draw=c/h.rolling(63,min_periods=63).max()-1
    bounce=valid&(draw<-.20)&(logs[3]>math.log(1.05))&(turnover>1.5*prior_turnover)
    rebounding,rebound_count=event_states(bounce.to_numpy(),valid.to_numpy(),c.to_numpy(),np.full(c.shape,np.nan),7,.10)
    rank('rebound_downside',(logs[3]-logs[21])/down,pd.DataFrame(rebounding,index=idx,columns=SYMBOLS))
    columns=[s for s in SYMBOLS if s!=exclude]
    broad=(c[columns]>ema20[columns]).mean(axis=1)
    weak=(c[columns]/c[columns].shift(7)-1).mean(axis=1)<-.10
    crash=(weak&(broad<1/3)).rolling(3,min_periods=1).max().eq(1).to_numpy()
    blend=.5*target['early_downside']+.3*target['ignition_downside']+.2*target['rebound_downside']
    blend[crash]=0.
    target[PRIMARY]=blend;target['asymmetric_blend_weekly']=blend.copy()
    old,old_trace=build_guarded(frames,exclude=exclude)
    target['guarded_control']=old[OLD_PRIMARY]
    target['guarded_plus_pulse']=.75*old[OLD_PRIMARY]+.25*blend
    btc_good=trend.copy();btc_good.loc[:,[s for s in SYMBOLS if s!='BTCUSDT']]=False
    rank('btc_early_downside',composite/down,btc_good,top=1)
    target['raw126_control']=weights(feature_bank(frames),WEEKLY,exclude)
    target['btc_hold']=np.zeros(c.shape)
    if exclude!='BTCUSDT':target['btc_hold'][:,0]=1.
    target['cash']=np.zeros(c.shape)
    if set(target)!=set(NAMES+CONTROLS):raise AssertionError('Policy registry differs from protocol')
    for name,w in target.items():
        if not np.isfinite(w).all() or (w<0).any() or (w.sum(axis=1)>1+1e-10).any():
            raise AssertionError('Nonfinite, short or leveraged target: '+name)
        if exclude and w[:,SYMBOLS.index(exclude)].any():raise AssertionError('Excluded allocation survived')
    audit=pd.DataFrame(dict(signal_date=idx.astype(str),crash_brake=crash,
        trend_assets=trend.sum(axis=1).to_numpy(),ignition_signal_assets=ignited.sum(axis=1),
        rebound_signal_assets=rebounding.sum(axis=1),primary_target_gross=blend.sum(axis=1)))
    counts=dict(ignition_signal_starts=ignition_count,rebound_signal_starts=rebound_count,
        event_starts_are_not_filled_orders=True,crash_brake_days=int(crash.sum()))
    return target,audit,counts
