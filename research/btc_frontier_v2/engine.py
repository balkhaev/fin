"""Linear BTC research ledger. No networking, credentials or live execution.

Signals at a closed minute are executed on the NEXT minute's open. Stops are
stop-first under unknown intrabar ordering. Slippage/fees and funding are paid.
Circuit breakers are modeled triggers, not guaranteed bounds through gaps.
"""
from dataclasses import dataclass,asdict
import math
import numpy as np
import pandas as pd
from numba import njit

COLUMNS=['entry_ms','exit_ms','side','entry','exit','quantity','gross',
         'fees','funding','net','reason','entry_equity']

@dataclass(frozen=True)
class Costs:
    fee: float=.0005
    slip: float=.0001
    latency: int=0
    def __post_init__(self):
        if not (math.isfinite(self.fee) and 0<=self.fee<.1 and
                math.isfinite(self.slip) and 0<=self.slip<.1 and
                isinstance(self.latency,int) and self.latency>=0): raise ValueError('Invalid costs')

@dataclass(frozen=True)
class Risk:
    fraction: float=.0025
    exposure: float=2.
    daily: float=.02
    drawdown: float=.095
    def __post_init__(self):
        if not (0<self.fraction<=.005 and 0<self.exposure<=2 and
                0<self.daily<=.02 and 0<self.drawdown<=.10): raise ValueError('Risk constraints violated')

@njit(cache=True)
def simulate(m,sig,sf,xs,hold,rr,trail,fee,slip,latency,risk,exposure,daylimit,ddlimit):
    # m columns: open timestamp, open, high, low, close, volume, funding rate, funding event
    n=len(m);equity=np.empty(n);ledger=np.empty((np.count_nonzero(sig)+1,12))
    cash=peak=daystart=10000.;day=-1;pos=0;q=0.;entry=stop=target=0.
    entered=0;fees=funding=entry_eq=0.;count=0;last_exit=-100
    halted=False;dayhalt=False;halt_ms=0.;adverse_dd=0.;active=0
    for i in range(n):
        ts,o,h,l,c,volume,rate,event=m[i]
        openeq=cash+pos*q*(o-entry)
        today=int(ts//86400000)
        if today!=day:day=today;daystart=openeq;dayhalt=False
        if pos and event:
            pay=pos*q*o*rate;cash-=pay;funding+=pay
        j=i-1-latency
        reason=0;px=0.;exit_ts=ts+60000
        # Causal channel exit; execute before observing the new minute's range.
        if pos and j>=0 and (xs[j]==pos or xs[j]==2):
            px=o*(1-pos*slip);reason=4;exit_ts=ts
        if not pos and not halted and not dayhalt and i<n-1 and i>last_exit+1 and j>=0 and sig[j] and not event:
            p=int(sig[j]);e=o*(1+p*slip);s=sf[j]
            if np.isfinite(s) and .001<=s<=.05 and cash>0:
                qty=min(cash*risk/(s+2*fee+2*slip)/e,cash*exposure/e,m[max(0,i-1),5]*.001)
                qty=math.floor(qty/.001+1e-10)*.001
                if qty*e>=100:
                    pos=p;q=qty;entry=e;stop=e*(1-p*s)
                    target=e*(1+p*s*rr[j]) if rr[j]>0 else (np.inf if p==1 else -np.inf)
                    entered=i;entry_eq=cash;fees=q*e*fee;funding=0.;cash-=fees
        if pos:
            active+=1
            threshold=max(daystart*(1-daylimit),peak*(1-ddlimit))
            # Include the anticipated exit fee and slippage when solving risk barrier.
            if pos==1:
                riskprice=(threshold-cash+q*entry)/(q*(1-fee)*(1-slip))
                effective=max(stop,riskprice)
            else:
                riskprice=(cash+q*entry-threshold)/(q*(1+fee)*(1+slip))
                effective=min(stop,riskprice)
            stop_hit=l<=effective if pos==1 else h>=effective
            target_hit=h>=target if pos==1 else l<=target
            if not reason:
                if stop_hit:
                    px=(min(o,effective) if pos==1 else max(o,effective))*(1-pos*slip)
                    reason=5 if effective!=stop else 1
                elif target_hit:px=target*(1-pos*slip);reason=2
                elif i-entered+1>=hold[max(0,entered-1-latency)] or i==n-1:
                    px=c*(1-pos*slip);reason=3 if i<n-1 else 6
            adverse_price=(l if pos==1 else h)
            if reason in (1,5):adverse_price=px
            elif reason==4:adverse_price=px
            worst=cash+pos*q*(adverse_price-entry)-q*adverse_price*fee
            adverse_dd=min(adverse_dd,worst/peak-1)
            if reason:
                gross=pos*q*(px-entry);exitfee=q*px*fee
                cash+=gross-exitfee;fees+=exitfee
                ledger[count]=np.array([m[entered,0],exit_ts,pos,entry,px,q,gross,fees,funding,
                                        gross-fees-funding,reason,entry_eq])
                count+=1;pos=0;q=0.;last_exit=i
            elif trail[max(0,entered-1-latency)]:
                # Only closed data may advance a trailing stop, for the following minute.
                nxt=c*(1-pos*sf[i])
                stop=max(stop,nxt) if pos==1 else min(stop,nxt)
        eq=cash+pos*q*(c-entry);equity[i]=eq
        peak=max(peak,eq);adverse_dd=min(adverse_dd,eq/peak-1)
        if eq<=daystart*(1-daylimit)+1e-7:dayhalt=True
        if eq<=peak*(1-ddlimit)+1e-7 and not halted:halted=True;halt_ms=ts+60000
    return equity,ledger[:count],halt_ms,adverse_dd,active


def market(d,f):
    if not len(d) or not np.all(np.diff(d.timestamp.to_numpy())==60000):raise ValueError('Non-contiguous data')
    start,end=int(d.timestamp.iloc[0]),int(d.timestamp.iloc[-1])+60000
    expected=np.arange(((start+28799999)//28800000)*28800000,end,28800000)
    actual=f.loc[(f.minute_time>=start)&(f.minute_time<end),'minute_time'].to_numpy()
    if not np.array_equal(actual,expected):raise ValueError('Funding coverage incomplete')
    if not np.isfinite(f.last_funding_rate.to_numpy()).all():raise ValueError('Nonfinite funding')
    fs=pd.Series(f.last_funding_rate.to_numpy(),index=f.minute_time)
    events=np.isin(d.timestamp.to_numpy(),f.minute_time.to_numpy())
    rates=fs.reindex(d.timestamp).fillna(0).to_numpy(float)
    return np.ascontiguousarray(np.column_stack([d.timestamp,d.open,d.high,d.low,d.close,d.volume,rates,events]),dtype=float)


def run(m,inputs,start=None,end=None,costs=Costs(),risk=Risk()):
    sig,sf,xs,hold,rr,trail=inputs
    n=len(m)
    a=0 if start is None else int(np.searchsorted(m[:,0],pd.Timestamp(start,tz='UTC').timestamp()*1000))
    b=n if end is None else int(np.searchsorted(m[:,0],pd.Timestamp(end,tz='UTC').timestamp()*1000))
    if b<=a:raise ValueError('Empty interval')
    def arr(x,dtype=float):return np.full(b-a,x,dtype=dtype) if np.isscalar(x) else np.asarray(x[a:b],dtype=dtype)
    eq,tr,halt,adverse,active=simulate(m[a:b],sig[a:b],sf[a:b],xs[a:b],arr(hold),arr(rr),
               arr(trail,np.bool_),costs.fee,costs.slip,costs.latency,risk.fraction,risk.exposure,risk.daily,risk.drawdown)
    trades=pd.DataFrame(tr,columns=COLUMNS)
    if not math.isclose(10000+trades.net.sum(),eq[-1],abs_tol=1e-6,rel_tol=1e-10):raise AssertionError('Ledger mismatch')
    days=(m[b-1,0]+60000-m[a,0])/86400000
    values=np.r_[10000.,eq];dd=values/np.maximum.accumulate(values)-1
    wins=trades.loc[trades.net>0,'net'].sum();loss=-trades.loc[trades.net<0,'net'].sum()
    stats=dict(start=str(pd.to_datetime(m[a,0],unit='ms',utc=True)),end_exclusive=str(pd.to_datetime(m[b-1,0]+60000,unit='ms',utc=True)),
        days=days,return_pct=(eq[-1]/10000-1)*100,final_equity=float(eq[-1]),max_drawdown_pct=float(dd.min()*100),
        adverse_bar_drawdown_pct=adverse*100,trades=len(tr),trades_per_day=len(tr)/days,
        profit_factor=float(wins/loss) if loss>0 else None,win_rate_pct=float((trades.net>0).mean()*100) if len(tr) else None,
        fees=float(trades.fees.sum()),funding_cost=float(trades.funding.sum()),
        gross=float(trades.gross.sum()),time_in_market_pct=active/len(eq)*100,
        halted_at=str(pd.to_datetime(halt,unit='ms',utc=True)) if halt else None,
        cagr_pct=float(((eq[-1]/10000)**(365.25/days)-1)*100) if days>=365 and eq[-1]>0 else None,
        costs=asdict(costs),risk=asdict(risk),live_ready=False)
    daily=pd.Series(eq,index=pd.to_datetime(m[a:b,0]+60000,unit='ms',utc=True)).resample('1D',closed='right',label='right').last().dropna()
    return stats,trades,daily
