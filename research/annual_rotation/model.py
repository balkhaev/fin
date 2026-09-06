"""Daily spot rotation simulation. No broker client, borrowing or live orders."""
from dataclasses import dataclass,asdict
import itertools
import math
import numpy as np
import pandas as pd
from .data import SYMBOLS

@dataclass(frozen=True)
class Config:
    rank:str
    lookback:int
    top:int
    every:int
    @property
    def id(self):return f'{self.rank}_{self.lookback}_top{self.top}_every{self.every}'

PRIMARY=Config('risk_adjusted',63,3,7)

def grid():
    return [Config(*x) for x in itertools.product(('raw','risk_adjusted'),(21,63,126),(1,3,5),(1,7))]

@dataclass(frozen=True)
class Costs:
    fee:float=.001
    slip:float=.0005
    allocation:float=1.
    extra_delay:int=0
    initial:float=10000.
    step:float=.00000001
    minimum:float=10.
    participation:float=.001
    def __post_init__(self):
        if not all(math.isfinite(v) for v in asdict(self).values()):raise ValueError('Nonfinite setting')
        if not 0<=self.fee<.05 or not 0<=self.slip<.05 or not 0<self.allocation<=1:raise ValueError('Invalid cost/allocation')
        if type(self.extra_delay) is not int or self.extra_delay<0 or self.initial<=0 or self.step<=0 or self.minimum<0 or not 0<self.participation<=1:
            raise ValueError('Invalid cash/latency/filter setting')


def feature_bank(frames):
    close=pd.DataFrame({s:frames[s].close for s in SYMBOLS})
    quote=pd.DataFrame({s:frames[s].quote_volume for s in SYMBOLS})
    returns=close.pct_change(fill_method=None)
    vol=returns.rolling(60,min_periods=60).std(ddof=1).replace(0,np.nan)
    regime=close>close.rolling(100,min_periods=100).mean()
    liquidity=quote.rolling(30,min_periods=30).mean()>=5000000
    result={}
    for n in (21,63,126):
        momentum=close/close.shift(n)-1
        support=close.notna().rolling(max(101,n+1),min_periods=max(101,n+1)).sum().eq(max(101,n+1))
        valid=regime&liquidity&support&(momentum>0)&vol.notna()
        result['raw',n]=momentum.where(valid).to_numpy(float)
        result['risk_adjusted',n]=(momentum/vol).where(valid).to_numpy(float)
    return result


def weights(bank,cfg,exclude=None):
    if cfg not in grid():raise ValueError('Outside fixed grid')
    if exclude is not None and exclude not in SYMBOLS:raise ValueError('Unknown excluded asset')
    score=bank[cfg.rank,cfg.lookback].copy()
    if exclude is not None:score[:,SYMBOLS.index(exclude)]=np.nan
    out=np.zeros_like(score)
    names=np.array(SYMBOLS)
    for t,row in enumerate(score):
        indices=np.flatnonzero(np.isfinite(row))
        order=sorted(indices,key=lambda k:(-row[k],names[k]))[:cfg.top]
        out[t,order]=1/cfg.top
    return out


def simulate(frames,target,cfg,start,end,costs=Costs()):
    idx=frames[SYMBOLS[0]].index
    if any(not frames[s].index.equals(idx) for s in SYMBOLS):raise ValueError('Source indices differ')
    if len(idx)>1 and not np.all(np.diff(idx.asi8)==86400000000000):raise ValueError('Missing rows must be explicit NaN')
    a=int(idx.searchsorted(pd.Timestamp(start,tz='UTC')));b=int(idx.searchsorted(pd.Timestamp(end,tz='UTC')))
    if a>=b or idx[a]!=pd.Timestamp(start,tz='UTC') or idx[b-1]+pd.Timedelta(days=1)!=pd.Timestamp(end,tz='UTC'):
        raise ValueError('Uncovered interval')
    if target.shape!=(len(idx),len(SYMBOLS)) or not np.isfinite(target).all() or (target<0).any() or (target.sum(axis=1)>1+1e-9).any():
        raise ValueError('Invalid target allocation')
    matrices={c:np.column_stack([frames[s][c].to_numpy(float) for s in SYMBOLS]) for c in ('open','high','low','close','volume')}
    q=np.zeros(len(SYMBOLS));cash=costs.initial;fills=[];curve=[];day_missing=[]
    peak=costs.initial;adverse=0.;rebalance_count=0;liquidity_rejections=0;roundtrips=0;entries=0;total_turnover=0.
    def trade(k,side,amount,reference,stamp,reason,capacity):
        nonlocal cash,roundtrips,entries,liquidity_rejections,total_turnover
        if not math.isfinite(reference) or reference<=0:return False
        if amount*reference<costs.minimum:return False
        if not math.isfinite(capacity) or amount>capacity+1e-10:
            liquidity_rejections+=1;return False
        price=reference*(1+costs.slip if side=='buy' else 1-costs.slip)
        if side=='buy':
            amount=min(amount,math.floor(cash/(price*(1+costs.fee))/costs.step)*costs.step)
        amount=math.floor((amount+costs.step*1e-7)/costs.step)*costs.step
        if amount*price<costs.minimum or amount<=0:return False
        if side=='sell':amount=min(amount,q[k])
        notional=amount*price;fee=notional*costs.fee
        before=q[k]
        if side=='buy':q[k]+=amount;cash-=notional+fee
        else:q[k]-=amount;cash+=notional-fee
        if abs(q[k])<costs.step/2:q[k]=0.
        if cash<-1e-6 or (q<-1e-10).any():raise AssertionError('Borrowing/shorting detected')
        if before==0 and q[k]>0:entries+=1
        if before>0 and q[k]==0:roundtrips+=1
        fills.append(dict(time=str(stamp),symbol=SYMBOLS[k],side=side,quantity=float(amount),price=float(price),
            notional=float(notional),fee=float(fee),cash_after=float(cash),reason=reason))
        total_turnover+=notional
        return True
    for i in range(a,b):
        o=matrices['open'][i];c=matrices['close'][i];l=matrices['low'][i]
        held=q>0;missing=bool(np.any(held&(~np.isfinite(o)|~np.isfinite(c)|~np.isfinite(l))))
        day_missing.append(missing)
        current=float(cash+np.sum(q[held]*o[held])) if not np.any(held&~np.isfinite(o)) else np.nan
        # One full day AFTER a completed daily bar: open D uses close D-2.
        j=i-2-costs.extra_delay
        calendar_day=int(idx[i].timestamp()//86400)
        scheduled=(i==a or calendar_day%cfg.every==0)
        if scheduled and j>=0 and math.isfinite(current) and not missing and i<b-1:
            valid=np.isfinite(o)&(o>0)
            desired=np.zeros_like(q)
            usable=target[j]*valid
            buy_price=np.where(valid,o*(1+costs.slip)*(1+costs.fee),np.inf)
            desired=np.floor(current*costs.allocation*.996*usable/buy_price/costs.step)*costs.step
            capacities=matrices['volume'][i-1]*costs.participation if i else np.zeros_like(q)
            before=len(fills)
            for k in range(len(q)):
                if desired[k]<q[k]:trade(k,'sell',q[k]-desired[k],o[k],idx[i],'rebalance',capacities[k])
            for k in range(len(q)):
                if desired[k]>q[k]:trade(k,'buy',desired[k]-q[k],o[k],idx[i],'rebalance',capacities[k])
            rebalance_count+=int(len(fills)>before)
        if i==b-1:
            capacities=matrices['volume'][i-1]*costs.participation
            for k in range(len(q)):
                if q[k]>0:trade(k,'sell',q[k],c[k],idx[i]+pd.Timedelta(days=1),'period_end',capacities[k])
        held=q>0
        eq=float(cash+np.sum(q[held]*c[held]*(1-costs.slip)*(1-costs.fee))) if np.isfinite(c[held]).all() else np.nan
        loweq=float(cash+np.sum(q[held]*l[held]*(1-costs.slip)*(1-costs.fee))) if np.isfinite(l[held]).all() else np.nan
        if math.isfinite(loweq):adverse=min(adverse,loweq/peak-1)
        if math.isfinite(eq):peak=max(peak,eq)
        curve.append(dict(time=str(idx[i]+pd.Timedelta(days=1)),equity=eq,cash=float(cash),
            invested_assets=int(held.sum()),unpriced_held_quote=missing))
    final=curve[-1]['equity'];complete=not any(day_missing) and not (q>0).any() and math.isfinite(final)
    reconstructed=costs.initial+sum((1 if f['side']=='sell' else -1)*f['notional']-f['fee'] for f in fills)
    if not math.isclose(cash,reconstructed,abs_tol=1e-5,rel_tol=1e-10):raise AssertionError('Cashflow reconciliation failed')
    values=np.r_[costs.initial,[x['equity'] for x in curve]]
    maximum=np.maximum.accumulate(np.where(np.isfinite(values),values,-np.inf))
    draw=values/maximum-1
    daily=pd.Series(values[1:]/values[:-1]-1,index=idx[a:b])
    years=len(daily)/365.25
    rolling=(1+daily).rolling(365,min_periods=365).apply(np.prod,raw=True)-1
    monthly=daily.groupby([daily.index.year,daily.index.month]).apply(lambda x:(1+x).prod()-1)
    fees=sum(x['fee'] for x in fills)
    annual=[]
    for y,g in daily.groupby(daily.index.year):
        mask=np.array(day_missing)[daily.index.year==y]
        full=(g.index[0]==pd.Timestamp(f'{y}-01-01',tz='UTC') and g.index[-1]==pd.Timestamp(f'{y}-12-31',tz='UTC'))
        annual.append(dict(year=int(y),full_year=bool(full),return_pct=float(((1+g).prod()-1)*100) if not mask.any() and g.notna().all() else None,days=len(g)))
    ret=(final/costs.initial-1)*100 if math.isfinite(final) else None
    result=dict(start=start,end_exclusive=end,days=len(daily),initial=costs.initial,final_equity=final if math.isfinite(final) else None,
        return_pct=ret if complete else None,diagnostic_return_pct=ret,
        cagr_pct=float(((final/costs.initial)**(1/years)-1)*100) if complete and years>=1 and final>0 else None,
        max_close_drawdown_pct=float(np.nanmin(draw)*100),simultaneous_daily_low_stress_pct=adverse*100,
        worst_rolling_365_pct=float(rolling.min()*100) if rolling.notna().any() else None,
        order_fills=len(fills),rebalance_days=rebalance_count,position_entries=entries,closed_asset_positions=roundtrips,
        fills_per_day=len(fills)/len(daily),fees=float(fees),gross_before_fees=float(final-costs.initial+fees) if math.isfinite(final) else None,
        annual_nominal_turnover=total_turnover/costs.initial/years,positive_month_fraction=float((monthly>0).mean()),
        liquidity_rejections=liquidity_rejections,missing_days_while_held=int(sum(day_missing)),open_assets=int((q>0).sum()),
        accounting_complete=bool(complete),ledger_reconciled=True,annual=annual,settings=asdict(costs),
        stable_500_proven=False,live_ready=False)
    return result,pd.DataFrame(fills),pd.DataFrame(curve)
