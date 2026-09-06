"""Offline LINEAR FUTURES variation-margin reference, no exchange adapter.

Contract notional is not spent as if it were spot. Price PnL, funding, fees and
adverse fill displacement are reconciled from signed fills. Mark-price extrema
provide a conservative scenario, not a synchronous observed liquidation path.
"""
from dataclasses import dataclass,asdict
import math
import numpy as np
import pandas as pd
from .data import SYMBOLS

@dataclass(frozen=True)
class Costs:
    initial:float=10000.
    gross:float=1.
    fee:float=.0005
    slip:float=.0001
    delay:int=0
    participation:float=.001
    minimum:float=20.
    step:float=.001
    def __post_init__(self):
        if not all(math.isfinite(x) for x in asdict(self).values()):raise ValueError('Nonfinite cost')
        if self.initial<=0 or not 0<self.gross<=2 or not 0<=self.fee<=.01 or not 0<=self.slip<=.01:
            raise ValueError('Outside fixed research capital limits')
        if type(self.delay) is not int or self.delay<0 or not 0<self.participation<=1 or self.minimum<0 or self.step<=0:
            raise ValueError('Invalid execution assumptions')


def simulate(frames,target,start,end,cost=Costs()):
    idx=frames[SYMBOLS[0]].index
    if any(not f.index.equals(idx) for f in frames.values()):raise ValueError('Misaligned prices')
    if target.shape!=(len(idx),2) or not np.isfinite(target).all() or (np.abs(target).sum(axis=1)>1+1e-10).any():
        raise ValueError('Invalid signed target')
    a=idx.searchsorted(pd.Timestamp(start,tz='UTC'));b=idx.searchsorted(pd.Timestamp(end,tz='UTC'))
    if a>=b or idx[a]!=pd.Timestamp(start,tz='UTC') or idx[b-1]+pd.Timedelta(hours=1)!=pd.Timestamp(end,tz='UTC'):
        raise ValueError('Unavailable requested calendar range')
    def array(col):return np.column_stack([frames[s][col].to_numpy() for s in SYMBOLS])
    op=array('open');hi=array('high');lo=array('low');cl=array('close');vol=array('volume')
    mo=array('mark_open');mh=array('mark_high');ml=array('mark_low');mc=array('mark_close')
    rates=array('funding_rate');known=array('funding_known').astype(bool);event=array('funding_event').astype(bool)
    # balance is collateral plus variation PnL valued at the last observed OPEN.
    balance=cost.initial;anchor=np.zeros(2);q=np.zeros(2);active_key=None;blocked_key=None
    closing=False;last_exit=a-2;episode_start=None;episode_start_equity=0.
    fills=[];funding=[];episodes=[];curve=[];missing_funding=missing_trade=missing_risk=0
    capacity_rejections=partial_exit_hours=0;maintenance_breach=False;peak=cost.initial;stress_dd=0.;max_gross=0.
    independent_cash=cost.initial;independent_q=np.zeros(2)
    for i in range(a,b):
        time=str(idx[i]);just_closed=False;held=np.abs(q)>0
        # Variation from last observed price recovers total gap PnL, never invents missing path.
        usable=np.isfinite(op[i])&(op[i]>0)
        missing_trade+=int(np.any(held&~usable))
        move=held&usable
        balance+=float(np.sum(q[move]*(op[i,move]-anchor[move])))
        anchor[usable]=op[i,usable]
        for k in np.flatnonzero(held&event[i]):
            if not known[i,k] or not math.isfinite(mo[i,k]):
                missing_funding+=1;closing=True;continue
            payment=-q[k]*mo[i,k]*rates[i,k]
            bound=mh[i,k] if q[k]*rates[i,k]>=0 else ml[i,k]
            uncertainty=max(0.,q[k]*rates[i,k]*(bound-mo[i,k])) if math.isfinite(bound) else None
            balance+=payment;independent_cash+=payment
            funding.append(dict(time=time,symbol=SYMBOLS[k],quantity=float(q[k]),rate=float(rates[i,k]),
                approximate_mark=float(mo[i,k]),cashflow=float(payment),adverse_extra=uncertainty))
        j=i-2-cost.delay
        w=target[j] if j>=0 else np.zeros(2)
        key=tuple(np.sign(w).astype(int)) if np.any(w) else None
        if held.any() and (key!=active_key or i>=b-12 or balance<=0):closing=True
        if held.any() and usable[held].all():
            gross_open=float(np.sum(np.abs(q)*op[i]))
            if balance<=0 or gross_open>2.25*balance:closing=True
        if closing and held.any():
            remaining=False;partial=False
            for k in np.flatnonzero(held):
                if not usable[k]:remaining=True;continue
                previous_volume=vol[i-1,k] if i>0 else np.nan
                cap=int(math.floor(previous_volume*cost.participation/cost.step)) if np.isfinite(previous_volume) and previous_volume>0 else 0
                units=min(int(round(abs(q[k])/cost.step)),cap)
                dq=-math.copysign(units*cost.step,q[k])
                price=op[i,k]*(1+math.copysign(cost.slip,dq)) if units else op[i,k]
                if units==0 or abs(dq)*price<cost.minimum:
                    capacity_rejections+=1;remaining=True;continue
                fee=abs(dq)*price*cost.fee
                balance-=dq*(price-op[i,k])+fee
                q[k]=round((q[k]+dq)/cost.step)*cost.step
                independent_cash-=dq*price+fee;independent_q[k]+=dq
                fills.append(dict(time=time,symbol=SYMBOLS[k],quantity_delta=float(dq),price=float(price),fee=float(fee),
                    side='buy' if dq>0 else 'sell',reason='exit',balance_at_open=float(balance)))
                if abs(q[k])>cost.step/2:remaining=True;partial=True
            if partial:partial_exit_hours+=1
            if not remaining and not np.any(q):
                episodes.append(dict(entry_time=episode_start,exit_time=time,net=float(balance-episode_start_equity),
                    end_balance=float(balance),target_direction=list(active_key),gross_budget=cost.gross))
                blocked_key=active_key;active_key=None;closing=False;last_exit=i;just_closed=True
        if not np.any(q) and not just_closed and not closing and i>last_exit and i<b-12 and key is not None and key!=blocked_key and balance>0:
            if usable.all():
                fill_prices=op[i]*(1+np.sign(w)*cost.slip)
                proposed=np.sign(w)*np.floor(np.abs(w)*balance*cost.gross/fill_prices/cost.step)*cost.step
                notional=np.abs(proposed)*fill_prices
                cap=vol[i-1]*cost.participation if i>0 else np.zeros(2)
                legal=((np.abs(proposed)<=cap+1e-12)&np.isfinite(cap))| (w==0)
                legal &= (notional>=cost.minimum)|(w==0)
                im=float(notional.sum()/3);fee=float(np.sum(notional)*cost.fee)
                if legal.all() and np.all((proposed!=0)|(w==0)) and im+fee<=balance*.75:
                    episode_start=time;episode_start_equity=balance;active_key=key
                    for k in np.flatnonzero(proposed):
                        dq=proposed[k];price=fill_prices[k];charge=abs(dq)*price*cost.fee
                        balance-=dq*(price-op[i,k])+charge;q[k]=dq;anchor[k]=op[i,k]
                        independent_cash-=dq*price+charge;independent_q[k]+=dq
                        fills.append(dict(time=time,symbol=SYMBOLS[k],quantity_delta=float(dq),price=float(price),fee=float(charge),
                            side='buy' if dq>0 else 'sell',reason='entry',balance_at_open=float(balance)))
                else:capacity_rejections+=1
            else:capacity_rejections+=1
        if key is None and not np.any(q):blocked_key=None
        held=np.abs(q)>0
        risk_valid=bool((np.isfinite(mc[i,held])&np.isfinite(mh[i,held])&np.isfinite(ml[i,held])).all())
        missing_risk+=int(not risk_valid)
        if held.any() and risk_valid:
            eq=balance+float(np.sum(q*(mc[i]-anchor)))
            worst_prices=np.where(q>=0,ml[i],mh[i])
            worst=balance+float(np.sum(q*(worst_prices-anchor)))-float(np.sum(np.abs(q)*worst_prices))*(cost.fee+cost.slip)
            maint=.10*float(np.sum(np.abs(q)*np.where(q>=0,mh[i],mh[i])))
            stress_dd=min(stress_dd,worst/peak-1)
            if worst<=maint:maintenance_breach=True;closing=True
            if eq>0:max_gross=max(max_gross,float(np.sum(np.abs(q)*mc[i])/eq))
        elif held.any():
            eq=balance+float(np.sum(q[held]*(cl[i,held]-anchor[held]))) if np.isfinite(cl[i,held]).all() else np.nan
            closing=True
        else:eq=balance
        if np.isfinite(eq):peak=max(peak,eq)
        # Independent signed-notional identity at the current anchor, not same recurrence.
        if not np.allclose(q,independent_q,atol=1e-9,rtol=0):raise AssertionError('Signed quantity mismatch')
        reconstruction=independent_cash+float(np.sum(q*anchor))
        if not math.isclose(balance,reconstruction,abs_tol=1e-5,rel_tol=1e-10):raise AssertionError('Independent futures cash identity failed')
        curve.append(dict(time=str(idx[i]+pd.Timedelta(hours=1)),equity=float(eq) if np.isfinite(eq) else None,
            balance_at_open=float(balance),btc_quantity=float(q[0]),eth_quantity=float(q[1])))
    complete=not np.any(q) and missing_funding==0 and missing_trade==0
    risk_verified=missing_risk==0 and not maintenance_breach
    if not np.any(q) and not math.isclose(balance,cost.initial+sum(x['net'] for x in episodes),abs_tol=1e-5):raise AssertionError('Episode sum discrepancy')
    c=pd.DataFrame(curve);f=pd.DataFrame(fills);fund=pd.DataFrame(funding);e=pd.DataFrame(episodes)
    values=np.r_[cost.initial,c.equity.to_numpy(float)];peaks=np.maximum.accumulate(np.where(np.isfinite(values),values,-np.inf))
    dd=np.nanmin(values/peaks-1)*100
    daily=pd.Series(c.equity.to_numpy(float),index=pd.to_datetime(c.time)-pd.Timedelta(nanoseconds=1)).resample('D').last()
    if complete and np.isfinite(daily).all():
        p=np.r_[cost.initial,daily.to_numpy()];dr=pd.Series(p[1:]/p[:-1]-1,index=daily.index)
        annual=[dict(year=int(y),return_pct=float(((1+d).prod()-1)*100),full_year=bool(d.index[0].month==1 and d.index[0].day==1 and d.index[-1].month==12 and d.index[-1].day==31)) for y,d in dr.groupby(dr.index.year)]
        months=[dict(year=int(y),month=int(m),return_pct=float(((1+d).prod()-1)*100)) for (y,m),d in dr.groupby([dr.index.year,dr.index.month])]
    else:annual=[];months=[]
    days=(pd.Timestamp(end)-pd.Timestamp(start)).days
    pnl=np.array([x['net'] for x in episodes]);fees=sum(x['fee'] for x in fills);paid=sum(x['cashflow'] for x in funding)
    report=dict(start=start,end_exclusive=end,days=days,initial=cost.initial,final_balance=float(balance),
        return_pct=float((balance/cost.initial-1)*100) if complete else None,
        cagr_pct=float(((balance/cost.initial)**(365.25/days)-1)*100) if complete and balance>0 and days>=365 else None,
        max_mark_close_drawdown_pct=float(dd),simultaneous_mark_extrema_stress_pct=float(stress_dd*100),
        completed_episodes=len(episodes),order_fills=len(fills),episodes_per_day=len(episodes)/days,
        active_entry_days=len({x['time'][:10] for x in fills if x['reason']=='entry'}),
        fees=float(fees),funding_cashflow=float(paid),gross_price_pnl=float(balance-cost.initial+fees-paid),
        funding_events_held=len(funding),unpriced_funding_events=missing_funding,held_trade_gap_hours=missing_trade,
        held_mark_gap_hours=missing_risk,maintenance_scenario_breach=maintenance_breach,max_observed_gross=float(max_gross),
        capacity_rejections=capacity_rejections,partial_exit_hours=partial_exit_hours,
        accounting_complete=complete,margin_scenario_verified=risk_verified,
        terminal_quantities=q.tolist(),net_closed=float(pnl.sum()),annual=annual,months=months,
        positive_months=sum(x['return_pct']>1e-10 for x in months),negative_months=sum(x['return_pct']<-1e-10 for x in months),
        zero_months=sum(abs(x['return_pct'])<=1e-10 for x in months),
        funding_mark_approximation=True,same_fills_adverse_funding_extra=float(sum(x['adverse_extra'] or 0 for x in funding)),
        actual_exchange_margin_tiers_verified=False,costs=asdict(cost),live_orders=0)
    return report,f,fund,e,c
