"""Historical simulation only; no exchange client or real order path.
Funding uses realized rates and a declared minute-open MARK price approximation.
Settlement-minute adverse bounds are reported on fixed fills, never used in sizing.
"""
from dataclasses import dataclass,asdict
import math
import numpy as np
import pandas as pd
from numba import njit

TRADE_COLS=['entry_ms','exit_ms','side','entry','exit','quantity','gross','fees','funding','net','reason','risk_budget','adverse_funding_extra']

@dataclass(frozen=True)
class Costs:
    fee:float=.0005
    slip:float=.0001
    latency:int=0
    def __post_init__(self):
        if not all(math.isfinite(x) and 0<=x<.1 for x in (self.fee,self.slip)):raise ValueError('Invalid costs')
        if type(self.latency) is not int or self.latency<0:raise ValueError('Invalid latency')


@njit(cache=True)
def simulate(m,signal,stop_fraction,rr,hold,trailing,fee,slip,latency):
    # Columns: time,o,h,l,c,vol,rate,event,mark_open,mark_high,mark_low.
    n=len(m);equity=np.empty(n);trades=np.empty((np.count_nonzero(signal)+1,13))
    cash=peak=day_start=1000.;day=-1;position=0;quantity=0.;entry=stop=target=0.
    entry_fee=funding=adverse_extra=risk_budget=0.;entered=0;last_exit=-100
    halted=day_halted=False;halt_at=0.;unpriced=0;count=0;funding_paid_count=0
    adverse_dd=0.;max_exposure=0.;eligible=0;entry_attempts=0
    for i in range(n):
        t,o,h,l,c,vol,rate,event,mark,mhigh,mlow=m[i]
        today=int(t//86400000)
        open_eq=cash+position*quantity*(o-entry)
        if today!=day:day=today;day_start=open_eq;day_halted=False
        if position and event:
            if not math.isfinite(mark) or mark<=0:
                unpriced+=1;halted=True
            else:
                pay=position*quantity*mark*rate
                cash-=pay;funding+=pay;funding_paid_count+=1
                bound=mhigh if position*rate>=0 else mlow
                if math.isfinite(bound):adverse_extra+=max(0.,position*quantity*rate*(bound-mark))
                else:unpriced+=1;halted=True
        j=i-1-latency
        if j>=0 and signal[j] and .001<=stop_fraction[j]<=.05:
            sf=stop_fraction[j]
            # Identical primary-cost gate in every stress. No higher-fee gate
            # hides unfavorable trades by silently excluding their signals.
            gate=(sf*rr>=2*.0012) if rr>0 else (sf>=.0012)
            if gate:eligible+=1
            if gate and not position and not halted and not day_halted and not event and i>last_exit+1 and i<n-1:
                entry_attempts+=1;s=int(signal[j]);p=o*(1+s*slip)
                risk_budget=cash*.0025
                q=min(risk_budget/(sf+2*fee+2*slip)/p,cash*2/p,m[max(0,i-1),5]*.001)
                q=math.floor(q/.001+1e-10)*.001
                if q*p>=100 and q>0:
                    position=s;quantity=q;entry=p;entered=i
                    stop=p*(1-s*sf);target=p*(1+s*sf*rr) if rr>0 else (np.inf if s==1 else -np.inf)
                    entry_fee=q*p*fee;cash-=entry_fee;funding=adverse_extra=0.
        reason=0;exit_price=0.
        if position:
            threshold=max(day_start*.99,peak*.93)
            # Solve a liquidation-equity barrier including anticipated exit costs.
            if position==1:
                barrier=(threshold-cash+quantity*entry)/(quantity*(1-slip)*(1-fee))
                effective=max(stop,barrier)
            else:
                barrier=(cash+quantity*entry-threshold)/(quantity*(1+slip)*(1+fee))
                effective=min(stop,barrier)
            stop_hit=l<=effective if position==1 else h>=effective
            target_hit=h>=target if position==1 else l<=target
            if stop_hit:
                raw=min(o,effective) if position==1 else max(o,effective)
                exit_price=raw*(1-position*slip);reason=4 if effective!=stop else 1
            elif target_hit:exit_price=target*(1-position*slip);reason=2
            elif i-entered+1>=hold or i==n-1 or unpriced:
                exit_price=c*(1-position*slip);reason=3 if i<n-1 else 5
            else:
                liquidation_eq=cash+position*quantity*(c-entry)-quantity*c*fee
                if liquidation_eq<=0 or quantity*c>2*liquidation_eq:
                    exit_price=c*(1-position*slip);reason=6
            worst_price=(l if position==1 else h)
            if reason in (1,4):worst_price=exit_price
            worst=cash+position*quantity*(worst_price-entry)-quantity*worst_price*fee
            adverse_dd=min(adverse_dd,worst/peak-1)
            marked=cash+position*quantity*(c-entry)
            if marked>0:max_exposure=max(max_exposure,quantity*c/marked)
            if reason:
                gross=position*quantity*(exit_price-entry);outfee=quantity*exit_price*fee
                cash+=gross-outfee;fees=entry_fee+outfee
                trades[count]=np.array([m[entered,0],t+60000,position,entry,exit_price,quantity,
                    gross,fees,funding,gross-fees-funding,reason,risk_budget,adverse_extra])
                count+=1;position=0;quantity=0.;last_exit=i
            elif trailing:
                # Only closed prices/ATR can alter the NEXT minute's protective stop.
                candidate=c*(1-position*stop_fraction[i])
                stop=max(stop,candidate) if position==1 else min(stop,candidate)
        eq=cash+position*quantity*(c-entry)
        equity[i]=eq;peak=max(peak,eq)
        if eq<=day_start*.99+1e-8:day_halted=True
        if eq<=peak*.93+1e-8 and not halted:halted=True;halt_at=t+60000
    return equity,trades[:count],halt_at,unpriced,adverse_dd,max_exposure,funding_paid_count,eligible,entry_attempts


def market(d):
    return np.ascontiguousarray(d[['time','open','high','low','close','volume','funding_rate','funding_event','funding_mark','funding_high','funding_low']].to_numpy(dtype=float))


def run(m,inputs,start,end,costs=Costs()):
    signal,sf,rr,hold,trailing=inputs
    a=int(np.searchsorted(m[:,0],pd.Timestamp(start,tz='UTC').timestamp()*1000))
    b=int(np.searchsorted(m[:,0],pd.Timestamp(end,tz='UTC').timestamp()*1000))
    if b<=a:raise ValueError('Empty evaluation')
    eq,raw,halt,missing,dd,maxexp,fcount,eligible,attempts=simulate(m[a:b],signal[a:b],sf[a:b],rr,hold,trailing,costs.fee,costs.slip,costs.latency)
    t=pd.DataFrame(raw,columns=TRADE_COLS)
    if not math.isclose(1000+t.net.sum(),eq[-1],rel_tol=1e-10,abs_tol=1e-7):raise AssertionError('Cash ledger mismatch')
    # Independent reconstruction from entry/exit notional cashflows.
    if len(t):
        reconstructed=t.side*t.quantity*(t.exit-t.entry)-t.fees-t.funding
        if not np.allclose(reconstructed,t.net,rtol=1e-10,atol=1e-8):raise AssertionError('Trade cashflow mismatch')
    days=(m[b-1,0]+60000-m[a,0])/86400000;net=t.net.to_numpy()
    profits=net[net>0].sum();loss=-net[net<0].sum();series=np.r_[1000.,eq]
    draw=series/np.maximum.accumulate(series)-1
    ret=(eq[-1]/1000-1)*100
    report=dict(start=start,end_exclusive=end,days=days,initial_equity=1000.,final_equity=float(eq[-1]),
       return_pct=float(ret) if not missing else None,known_cash_return_pct=float(ret),
       cagr_pct=float(((eq[-1]/1000)**(365.25/days)-1)*100) if not missing and days>=365 and eq[-1]>0 else None,
       max_drawdown_pct=float(draw.min()*100),adverse_bar_drawdown_pct=float(dd*100),
       trades=len(t),trades_per_day=len(t)/days,profit_factor=float(profits/loss) if loss>0 else None,
       win_rate_pct=float((net>0).mean()*100) if len(t) else None,
       net_closed=float(t.net.sum()),gross_closed=float(t.gross.sum()),fees=float(t.fees.sum()),funding=float(t.funding.sum()),
       funding_events_held=int(fcount),missing_funding_marks=int(missing),raw_signal_minutes=int(np.count_nonzero(signal[a:b])),
       eligible_minutes=int(eligible),entry_attempts=int(attempts),max_observed_exposure=float(maxexp),
       halted_at=str(pd.to_datetime(halt,unit='ms',utc=True)) if halt else None,
       same_fills_adverse_funding_net=float(t.net.sum()-t.adverse_funding_extra.sum()),
       adverse_funding_sensitivity_is_causal_strategy=False,costs=asdict(costs),
       ledger_reconciled=True,funding_uses_realized_rate=True,funding_mark_is_approximate=True,
       historical_account_fee_tier_verified=False,live_ready=False)
    daily=pd.Series(eq,index=pd.to_datetime(m[a:b,0]+60000,unit='ms',utc=True)).resample('1D',closed='right',label='right').last().dropna()
    return report,t,daily
