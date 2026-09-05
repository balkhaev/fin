"""Historical spot cash/coin ledger, not a live exchange adapter."""
from dataclasses import dataclass,asdict
import math
import numpy as np
import pandas as pd

@dataclass(frozen=True)
class Settings:
    capital:float=1000.
    allocation:float=1.
    fee:float=.001
    slip:float=.0005
    delay:int=1
    drawdown_stop:float=0.
    qty_step:float=.00001
    min_notional:float=10.
    participation:float=.001
    def __post_init__(self):
        if not all(math.isfinite(v) for v in asdict(self).values()):raise ValueError('Nonfinite setting')
        if self.capital<=0 or not 0<self.allocation<=1:raise ValueError('Spot must be fully funded')
        if not 0<=self.fee<.05 or not 0<=self.slip<.05:raise ValueError('Invalid costs')
        if type(self.delay) is not int or self.delay<1:raise ValueError('At least one full hour delay')
        if not 0<=self.drawdown_stop<1 or self.qty_step<=0 or self.min_notional<0 or not 0<self.participation<=1:raise ValueError('Invalid contract/risk scenario')


def run(data,signals,start,end,settings=Settings()):
    s=settings;idx=data.index
    start=pd.Timestamp(start,tz='UTC');end=pd.Timestamp(end,tz='UTC')
    a=int(idx.searchsorted(start));b=int(idx.searchsorted(end))
    if a>=b or idx[a]!=start or idx[b-1]+pd.Timedelta(hours=1)!=end:raise ValueError('Incomplete requested range')
    sig=np.asarray(signals)
    if len(sig)!=len(data) or not np.isin(sig,[0,1]).all():raise ValueError('Binary spot signal required')
    m=data[['open','high','low','close','volume']].to_numpy(float)
    times=idx.asi8//1000000;cash=s.capital;qty=0.;entry=0.;entry_fee=0.;entry_ms=0
    peak=s.capital;maxdd=0.;adverse_dd=0.;halted=False;halt_ms=None;incomplete=False
    trades=[];fills=[];curve=[];attempts=0;liquidity_rejected=0;held_hours=0;gap_hours=0
    def close(price,now,reason):
        nonlocal cash,qty
        px=price*(1-s.slip);fee=qty*px*s.fee;gross=qty*(px-entry)
        cash+=qty*px-fee
        fills.append(dict(time_ms=int(now),side='sell',quantity=qty,price=px,fee=fee,reason=reason))
        trades.append(dict(entry_ms=int(entry_ms),exit_ms=int(now),quantity=qty,entry=entry,exit=px,
            gross=gross,fees=entry_fee+fee,net=gross-entry_fee-fee,reason=reason,hold_hours=(now-entry_ms)/3600000))
        qty=0.
    for i in range(a,b):
        o,h,l,c,volume=m[i];now=times[i]
        if not np.isfinite(m[i]).all():
            gap_hours+=1
            if qty:incomplete=True
            curve.append(dict(time_ms=int(now+3600000),equity=None if qty else cash,cash=cash,quantity=qty))
            continue
        if qty and incomplete:close(o,now,'data_gap_recovery');halted=True;halt_ms=halt_ms or int(now)
        # A gap through a previous-close drawdown barrier is filled at worse OPEN.
        if qty and s.drawdown_stop:
            barrier=(peak*(1-s.drawdown_stop)-cash)/(qty*(1-s.slip)*(1-s.fee))
            if o<=barrier:
                close(o,now,'drawdown_gap');halted=True;halt_ms=int(now)
        desired=int(sig[i-s.delay]) if i>=s.delay else 0
        if qty and desired==0:close(o,now,'signal_exit')
        elif not qty and desired==1 and not halted and not incomplete and i<b-1:
            attempts+=1
            px=o*(1+s.slip);q=math.floor((cash*s.allocation/(px*(1+s.fee)))/s.qty_step)*s.qty_step
            previous_volume=m[i-1,4] if i else np.nan
            if not math.isfinite(previous_volume) or q>previous_volume*s.participation:
                liquidity_rejected+=1
            elif q>0 and q*px>=s.min_notional:
                qty=q;entry=px;entry_ms=now;entry_fee=q*px*s.fee;cash-=q*px+entry_fee
                if cash < -1e-8:raise AssertionError('Borrowing is not allowed')
                fills.append(dict(time_ms=int(now),side='buy',quantity=q,price=px,fee=entry_fee,reason='signal_entry'))
        if qty:
            held_hours+=1
            if s.drawdown_stop:
                barrier=(peak*(1-s.drawdown_stop)-cash)/(qty*(1-s.slip)*(1-s.fee))
                if l<=barrier:
                    close(min(o,barrier),now+3600000,'drawdown_stop');halted=True;halt_ms=int(now+3600000)
            if qty:
                adverse_dd=min(adverse_dd,(cash+qty*l*(1-s.slip)*(1-s.fee))/peak-1)
        if i==b-1 and qty:close(c,now+3600000,'period_end')
        eq=cash+qty*c*(1-s.slip)*(1-s.fee)
        peak=max(peak,eq);maxdd=min(maxdd,eq/peak-1);adverse_dd=min(adverse_dd,eq/peak-1)
        curve.append(dict(time_ms=int(now+3600000),equity=eq,cash=cash,quantity=qty))
    complete=not incomplete and qty==0
    cashflow=s.capital+sum((1 if x['side']=='sell' else -1)*x['quantity']*x['price']-x['fee'] for x in fills)
    if not math.isclose(cash,cashflow,abs_tol=1e-6):raise AssertionError('Signed notional ledger mismatch')
    if qty==0 and not math.isclose(cash,s.capital+sum(x['net'] for x in trades),abs_tol=1e-6):raise AssertionError('Trade ledger mismatch')
    for t in trades:
        if not math.isclose(t['net'],t['quantity']*(t['exit']-t['entry'])-t['fees'],abs_tol=1e-8):raise AssertionError('Trade arithmetic mismatch')
    days=(end-start).total_seconds()/86400;ret=(cash/s.capital-1)*100
    win=sum(x['net'] for x in trades if x['net']>0);loss=-sum(x['net'] for x in trades if x['net']<0)
    report=dict(start=str(start.date()),end_exclusive=str(end.date()),days=days,initial_equity=s.capital,final_cash=cash,
        return_pct=ret if complete else None,diagnostic_cash_return_pct=ret,
        cagr_pct=((cash/s.capital)**(365.25/days)-1)*100 if complete and days>=365 and cash>0 else None,
        max_close_drawdown_pct=maxdd*100,adverse_hour_drawdown_pct=adverse_dd*100,
        round_trips=len(trades),order_fills=len(fills),round_trips_per_day=len(trades)/days,
        gross=sum(x['gross'] for x in trades),fees=sum(x['fee'] for x in fills),net=sum(x['net'] for x in trades),
        profit_factor=win/loss if loss>0 else None,win_rate_pct=100*sum(x['net']>0 for x in trades)/len(trades) if trades else None,
        exposure_time_pct=100*held_hours/(b-a),average_hold_hours=float(np.mean([x['hold_hours'] for x in trades])) if trades else None,
        attempts=attempts,liquidity_rejected=liquidity_rejected,missing_hours=gap_hours,held_data_gap=incomplete,
        open_quantity=qty,accounting_complete=complete,halted_at=str(pd.to_datetime(halt_ms,unit='ms',utc=True)) if halt_ms else None,
        same_fills_double_commission_net=sum(x['net']-x['fees'] for x in trades),
        settings=asdict(s),ledger_reconciled=True,funding_applicable=False,live_ready=False,
        old_risk_constraints_met=False,history_status='REUSED_research_not_pristine_OOS')
    return report,pd.DataFrame(trades),pd.DataFrame(fills),pd.DataFrame(curve)
