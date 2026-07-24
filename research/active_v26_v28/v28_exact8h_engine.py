from __future__ import annotations
from dataclasses import dataclass, asdict, replace
from pathlib import Path
import sys, json, time
import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar
from pandas.tseries.offsets import CustomBusinessDay

ROOT=Path('/mnt/data/v26_work/active_v26'); V8=ROOT/'v8_frozen'
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(V8));sys.path.insert(0,'/mnt/data')
import inputs as v8inputs
import signals as v8signals
import engine as v8engine
from execution_policy import mean_transforms,SPOT_SELECTED,PERP_SELECTED
from v35_funding_carry import CarryPolicy,build_signal
from v36_cash_carry import CashPolicy,POLICIES as CASH_POLICIES,PERIODS,SEGMENTS,SCENARIOS,MONTHLY_RATES

OUT=Path('/mnt/data/v43');OUT.mkdir(exist_ok=True)
CARRY_POLICIES=[
 CarryPolicy(30,.08,0.,0.,.30,7,'equal',0),
 CarryPolicy(30,.12,0.,0.,.30,7,'equal',0),
 CarryPolicy(45,.08,0.,0.,.30,7,'equal',0),
 CarryPolicy(45,.12,0.,0.,.30,7,'equal',0),
]
CASH=next(x for x in CASH_POLICIES if x.name=='upper_b1_14d')
USBD=CustomBusinessDay(calendar=USFederalHolidayCalendar())

@dataclass(frozen=True)
class AuditPolicy:
 name:str
 positive_funding_haircut:float=1.0
 suppress_first_intraday_funding_on_carry_entry:bool=False
 margin_multiple:float=1.0
AUDITS=(
 AuditPolicy('exact'),
 AuditPolicy('fund80',.8),
 AuditPolicy('fund60',.6),
 AuditPolicy('entry_delay8h',1.,True),
 AuditPolicy('fund80_margin125',.8,False,1.25),
)

@dataclass
class Ctx:
 index:pd.DatetimeIndex
 so:np.ndarray;sc:np.ndarray;av:np.ndarray
 spot_signal:np.ndarray;perp_signal:np.ndarray;carry_signal:np.ndarray
 o8:np.ndarray;c8:np.ndarray;f8:np.ndarray
 symbols:tuple;pair_symbols:tuple;pair_to_spot:list
 is_bus:np.ndarray;settle1:np.ndarray;rate_by_policy:dict

def utc(x):
 t=pd.Timestamp(x);return t.tz_localize('UTC') if t.tzinfo is None else t.tz_convert('UTC')

def prepare():
 data,raw,po,pc,fund=v8inputs.load(Path('/mnt/data/v6_new'),Path('/mnt/data/v5_new'))
 v7=v8signals.build_v7_hedge(pc,fund,raw);rel=v8signals.build_v8_relative(pc);rp=v8signals.build_combined_hedge(v7,rel)
 bs=mean_transforms(raw,SPOT_SELECTED);bp=mean_transforms(rp,PERP_SELECTED)
 spotclose=pd.DataFrame({s:data.close[s] for s in pc.columns},index=data.index)
 carry=sum(build_signal(fund,pc,spotclose,p) for p in CARRY_POLICIES)/len(CARRY_POLICIES)
 opens={};closes={};funds={};base=Path('/mnt/data/v5_new/processed')
 for s in ('BTCUSDT','ETHUSDT'):
  p=pd.read_csv(base/f'perp_{s}_8h.csv',index_col=0,parse_dates=True);p.index=pd.to_datetime(p.index,utc=True);opens[s]=p.open;closes[s]=p.close
  f=pd.read_csv(base/f'funding_{s}.csv',index_col=0,parse_dates=True);f.index=pd.to_datetime(f.index,utc=True,format='mixed').floor('8h');funds[s]=f.funding_rate.groupby(level=0).sum()
 o=pd.DataFrame(opens).sort_index();c=pd.DataFrame(closes).reindex(o.index);f=pd.DataFrame(funds).reindex(o.index).fillna(0.)
 start=data.index.min();end=data.index.max()+pd.Timedelta(days=1);o=o[(o.index>=start)&(o.index<end)];c=c.reindex(o.index);f=f.reindex(o.index).fillna(0.)
 if len(o)!=len(data.index)*3:raise ValueError((len(o),len(data.index)))
 naive=data.index.tz_convert(None).normalize();bdset=set(pd.date_range(naive.min(),naive.max()+pd.Timedelta(days=10),freq=USBD))
 isbus=np.array([x in bdset for x in naive],bool);settle=np.empty(len(naive),int)
 for i,x in enumerate(naive):
  submit=x if x in bdset else USBD.rollforward(x);sdate=submit+USBD
  settle[i]=min(int(naive.searchsorted(sdate,side='left')),len(naive)-1)
 rate={}
 for policy in CASH_POLICIES:
  arr=np.zeros(len(data.index))
  for i,ts in enumerate(data.index):
   prev=(ts.tz_convert(None).to_period('M')-1).strftime('%Y-%m');rawr=MONTHLY_RATES.get(prev,0.)/100.;arr[i]=max(0.,rawr*policy.yield_haircut-policy.annual_expense_bps/10000.)
  rate[policy.name]=arr
 symbols=tuple(data.symbols);pairs=tuple(o.columns);pair_to=[symbols.index(s) for s in pairs]
 ctx=Ctx(data.index,data.open.to_numpy(float),data.close.to_numpy(float),data.available.to_numpy(bool),bs.reindex(data.index).fillna(0).to_numpy(float),bp.reindex(data.index).fillna(0).to_numpy(float),carry.reindex(data.index).fillna(0).to_numpy(float),o.to_numpy(float),c.to_numpy(float),f.to_numpy(float),symbols,pairs,pair_to,isbus,settle,rate)
 return data,ctx

def base_targets(s,p,locked,cap,available):
 s=np.nan_to_num(s.copy());p=np.nan_to_num(p.copy());s[(~available)|(s<0)]=0.;s*=locked;p*=locked;g=float(s.sum()+np.abs(p).sum())
 if g>cap and g>0:m=cap/g;s*=m;p*=m
 return s,p

def add_carry(bs,bp,c,pair_to,cap):
 c=np.maximum(np.nan_to_num(c.copy()),0.)
 def combo(a):
  cs=np.zeros_like(bs);cp=np.zeros_like(bp)
  for k,j in enumerate(pair_to):cs[j]+=a*c[k]
  cp-=a*c;ts=bs+cs;tp=bp+cp;return ts,tp,cs,cp,float(ts.sum()+np.abs(tp).sum())
 ts,tp,cs,cp,g=combo(1.)
 if g<=cap:return ts,tp,cs,cp,1.,g
 lo,hi=0.,1.
 for _ in range(45):
  mid=(lo+hi)/2;*_,gm=combo(mid)
  if gm<=cap:lo=mid
  else:hi=mid
 return (*combo(lo)[:4],lo,combo(lo)[4])

def feasible(ds,dp,as_,ap,eq,locked_cash,cost,op_res,margin):
 def f(a):
  ts=ds*a;tp=dp*a;turn=float(np.abs(ts-as_).sum()+np.abs(tp-ap).sum());tc=eq*turn*cost;after=max(0.,eq-tc);req=locked_cash+after*(float(ts.sum())+margin*float(np.abs(tp).sum())+op_res);return req<=after+1e-9,tc,after
 ok,tc,af=f(1.)
 if ok:return 1.,tc,af
 lo,hi=0.,1.;btc,baf=f(0.)[1:]
 for _ in range(45):
  mid=(lo+hi)/2;ok,tc,af=f(mid)
  if ok:lo=mid;btc,baf=tc,af
  else:hi=mid
 return lo,btc,baf

def simulate(ctx:Ctx,start,end,cost_bps,cash:CashPolicy|None,audit:AuditPolicy,carry_enabled=True):
 begin,finish=utc(start),utc(end);locs=np.flatnonzero((ctx.index>=begin)&(ctx.index<finish));ns=len(ctx.symbols);npair=len(ctx.pair_symbols)
 pend_s=ctx.spot_signal[locs[0]-1].copy() if locs[0]>0 else np.zeros(ns);pend_bp=ctx.perp_signal[locs[0]-1].copy() if locs[0]>0 else np.zeros(npair);pend_c=ctx.carry_signal[locs[0]-1].copy() if locs[0]>0 and carry_enabled else np.zeros(npair)
 bsv=np.zeros(ns);csv=np.zeros(ns);bpn=np.zeros(npair);cpn=np.zeros(npair);liquid=10000.;treasury=0.;pending=[];initial=10000.;high=initial;locked=1.5;prev=None;prev_c=np.zeros(npair)
 n=len(locs);equity=np.empty(n);gross=np.empty(n);turnover=np.empty(n);costs=np.empty(n);fundp=np.empty(n);tint=np.empty(n);missed=np.empty(n);carrygross=np.empty(n);tcosts=np.empty(n);xfercosts=np.empty(n);risk_scales=np.empty(n);high_waters=np.empty(n)
 rate=ctx.rate_by_policy[cash.name] if cash else None;cost_rate=cost_bps/10000.
 for ii,loc in enumerate(locs):
  transfer=interest=forced_not=forced_cost=0.;keep=[]
  for sloc,amt in pending:
   if sloc<=loc:liquid+=amt
   else:keep.append((sloc,amt))
  pending=keep
  i0=loc*3;funding_total=0.
  if prev is not None:
   if cash and treasury>0:interest=treasury*((1+rate[loc])**(1/365)-1);treasury+=interest
   comb=bsv+csv
   ids=np.flatnonzero(comb>0)
   for j in ids:
    a=ctx.sc[prev,j];b=ctx.so[loc,j]
    if np.isfinite(a) and np.isfinite(b):r=b/a;bsv[j]*=r;csv[j]*=r
    else:
     nn=float(comb[j]);pen=nn*max(cost_rate,.01);liquid+=max(0.,nn-pen);bsv[j]=csv[j]=0.;forced_not+=nn;forced_cost+=pen
   r=np.divide(ctx.o8[i0],ctx.c8[i0-1],out=np.ones(npair),where=np.isfinite(ctx.o8[i0])&np.isfinite(ctx.c8[i0-1]));liquid+=float(np.sum((bpn+cpn)*(r-1)));bpn*=r;cpn*=r
   fr=ctx.f8[i0];rawb=float(np.sum(-(bpn*fr)));rawc=-(cpn*fr);adj=np.where(rawc>0,rawc*audit.positive_funding_haircut,rawc);fp=rawb+float(adj.sum());liquid+=fp;funding_total+=fp
  pbal=float(sum(x[1] for x in pending));tot_s=bsv+csv;tot_p=bpn+cpn;eq=float(liquid+treasury+pbal+tot_s.sum())
  if high>=2*initial:locked=min(locked,.75)
  elif high>=1.5*initial:locked=min(locked,1.)
  as_=tot_s/eq;ap=tot_p/eq;bs,bp=base_targets(pend_s,pend_bp,locked,.85,ctx.av[loc])
  if carry_enabled:ds,dp,cs,cp,calpha,dgross=add_carry(bs,bp,pend_c,ctx.pair_to_spot,.85)
  else:ds,dp,cs,cp,calpha,dgross=bs,bp,np.zeros(ns),np.zeros(npair),0.,float(bs.sum()+np.abs(bp).sum())
  if cash:
   alpha,tc,after=feasible(ds,dp,as_,ap,eq,treasury+pbal,cost_rate,cash.operational_reserve,audit.margin_multiple)
   if alpha<.999999 and treasury>0:
    desired_after=max(0.,eq-eq*float(np.abs(ds-as_).sum()+np.abs(dp-ap).sum())*cost_rate);max_t=max(0.,desired_after*(1-cash.operational_reserve-float(ds.sum())-audit.margin_multiple*float(np.abs(dp).sum())));req=min(max(0.,treasury-max_t),treasury)
    if req>0:
     rc=req*cash.transfer_cost_bps/10000.;pending.append((ctx.settle1[loc],req-rc));treasury-=req;transfer+=rc;pbal=float(sum(x[1] for x in pending));eq-=rc;alpha,tc,after=feasible(ds,dp,as_,ap,eq,treasury+pbal,cost_rate,cash.operational_reserve,audit.margin_multiple)
  else:
   alpha=1.;tc=eq*float(np.abs(ds-as_).sum()+np.abs(dp-ap).sum())*cost_rate;after=max(0.,eq-tc)
  ts=ds*alpha;tp=dp*alpha;turn=float(np.abs(ts-as_).sum()+np.abs(tp-ap).sum());bsv=bs*alpha*after;csv=cs*alpha*after;bpn=bp*alpha*after;cpn=cp*alpha*after;pbal=float(sum(x[1] for x in pending));liquid=max(0.,after-treasury-pbal-float((bsv+csv).sum()))
  sr=np.divide(ctx.sc[loc],ctx.so[loc],out=np.ones(ns),where=np.isfinite(ctx.so[loc])&np.isfinite(ctx.sc[loc]));bsv*=sr;csv*=sr
  entry=np.any(pend_c>prev_c+1e-12)
  for k in range(3):
   i8=i0+k;r=np.divide(ctx.c8[i8],ctx.o8[i8],out=np.ones(npair),where=np.isfinite(ctx.o8[i8])&np.isfinite(ctx.c8[i8]));liquid+=float(np.sum((bpn+cpn)*(r-1)));bpn*=r;cpn*=r
   if k<2:
    fr=ctx.f8[i8+1];rawb=float(np.sum(-(bpn*fr)));rawc=-(cpn*fr)
    if audit.suppress_first_intraday_funding_on_carry_entry and entry and k==0:adj=np.minimum(rawc,0.)
    else:adj=np.where(rawc>0,rawc*audit.positive_funding_haircut,rawc)
    fp=rawb+float(adj.sum());liquid+=fp;funding_total+=fp
  pbal=float(sum(x[1] for x in pending));eqc=float(liquid+treasury+pbal+(bsv+csv).sum());high=max(high,eqc)
  nbs,nbp=base_targets(ctx.spot_signal[loc],ctx.perp_signal[loc],locked,.85,ctx.av[loc]);nds,ndp=(add_carry(nbs,nbp,ctx.carry_signal[loc],ctx.pair_to_spot,.85)[:2] if carry_enabled else (nbs,nbp))
  if cash:
   maxnext=max(0.,eqc*(1-cash.operational_reserve-float(nds.sum())-audit.margin_multiple*float(np.abs(ndp).sum())))
   if treasury>maxnext:
    req=min(treasury-maxnext,treasury);rc=req*cash.transfer_cost_bps/10000.;pending.append((ctx.settle1[loc],req-rc));treasury-=req;transfer+=rc;eqc-=rc
   if ii%cash.sweep_interval_days==0 and liquid>0 and ctx.is_bus[loc]:
    current=float((bsv+csv).sum());inc=max(0.,eqc*float(nds.sum())-current);required=eqc*cash.operational_reserve+eqc*audit.margin_multiple*float(np.abs(ndp).sum())+inc+eqc*.002;excess=max(0.,liquid-required);room=max(0.,cash.max_treasury_fraction*eqc-treasury);sweep=min(excess,room);minimum=cash.minimum_transfer_fraction*eqc
    if sweep>=minimum:rc=sweep*cash.transfer_cost_bps/10000.;liquid-=sweep;treasury+=sweep-rc;transfer+=rc;eqc-=rc
  tot_s=bsv+csv;tot_p=bpn+cpn;equity[ii]=eqc;gross[ii]=(tot_s.sum()+np.abs(tot_p).sum())/eqc;turnover[ii]=turn+forced_not/max(eq,1e-9);costs[ii]=tc+forced_cost+transfer;tcosts[ii]=tc+forced_cost;xfercosts[ii]=transfer;fundp[ii]=funding_total;tint[ii]=interest;missed[ii]=max(0.,dgross-float(ts.sum()+np.abs(tp).sum()));carrygross[ii]=(csv.sum()+np.abs(cpn).sum())/eqc;risk_scales[ii]=locked;high_waters[ii]=high
  pend_s=ctx.spot_signal[loc].copy();pend_bp=ctx.perp_signal[loc].copy();pend_c=ctx.carry_signal[loc].copy() if carry_enabled else np.zeros(npair);prev_c=pend_c.copy();prev=loc
 return pd.DataFrame({'equity':equity,'gross':gross,'turnover':turnover,'costs':costs,'trading_costs':tcosts,'transfer_costs':xfercosts,'funding_pnl':fundp,'treasury_interest':tint,'missed_target_gross':missed,'carry_gross':carrygross,'risk_scale':risk_scales,'high_water':high_waters},index=ctx.index[locs])

def main():
 data,ctx=prepare();zero=ctx.carry_signal.copy()*0;rows=[];accounts={};jobs=[];exact=AUDITS[0]
 for per in (*SEGMENTS,'prefinal','full','final_2026h1'):jobs.append(('carry_cash',exact,'stress',40.,per,True,CASH))
 for audit in AUDITS[1:]:
  for per in ('prefinal','full','final_2026h1'):jobs.append(('carry_cash',audit,'stress',40.,per,True,CASH))
 for scen,bps in SCENARIOS.items():
  if scen=='stress':continue
  for per in (*SEGMENTS,'prefinal','full','final_2026h1'):jobs.append(('carry_cash',exact,scen,bps,per,True,CASH))
 for label,cash in [('v26_exact',None),('v27_exact_cash',CASH)]:
  for per in (*SEGMENTS,'prefinal','full','final_2026h1'):jobs.append((label,exact,'stress',40.,per,False,cash))
 print('jobs',len(jobs),flush=True);t0=time.time()
 original=ctx.carry_signal.copy()
 for n,(label,audit,scen,bps,per,enabled,cash) in enumerate(jobs,1):
  ctx.carry_signal=original if enabled else zero
  a=simulate(ctx,*PERIODS[per],bps,cash,audit,enabled);m=v8engine.metrics(a);rows.append({'candidate':label,'audit':audit.name,'scenario':scen,'period':per,**m,'treasury_interest':float(a.treasury_interest.sum()),'funding_pnl':float(a.funding_pnl.sum()),'avg_carry_gross':float(a.carry_gross.mean()),'avg_missed_gross':float(a.missed_target_gross.mean()),'p95_missed_gross':float(a.missed_target_gross.quantile(.95))})
  if per in ('full','final_2026h1'):accounts[(label,audit.name,scen,per)]=a
  if n%10==0:print(n,'elapsed',time.time()-t0,flush=True)
 ctx.carry_signal=original;df=pd.DataFrame(rows);df.to_csv(OUT/'metrics.csv',index=False)
 for k,a in accounts.items():a.to_csv(OUT/f"equity_{'_'.join(k)}.csv")
 def r(c,a,s,p):return df[(df.candidate==c)&(df.audit==a)&(df.scenario==s)&(df.period==p)].iloc[0]
 audits={}
 for a in [x.name for x in AUDITS]:
  pre=r('carry_cash',a,'stress','prefinal');full=r('carry_cash',a,'stress','full');fin=r('carry_cash',a,'stress','final_2026h1');audits[a]={'prefinal_cagr':float(pre.annualized_return),'full_cagr':float(full.annualized_return),'full_return':float(full.total_return),'full_dd':float(full.max_drawdown),'full_sharpe':float(full.sharpe),'final_return':float(fin.total_return),'final_dd':float(fin.max_drawdown),'funding_pnl':float(full.funding_pnl),'avg_carry_gross':float(full.avg_carry_gross)}
 b26=r('v26_exact','exact','stress','full');b27=r('v27_exact_cash','exact','stress','full');out={'candidate':'V43_EXACT_8H_FUNDING_CARRY_CASH_AUDIT','status':'audit_complete','policies':[asdict(x) for x in CARRY_POLICIES],'cash':asdict(CASH),'audits':audits,'exact_v26':{'full_cagr':float(b26.annualized_return),'full_return':float(b26.total_return),'full_dd':float(b26.max_drawdown),'final_return':float(r('v26_exact','exact','stress','final_2026h1').total_return)},'exact_v27':{'full_cagr':float(b27.annualized_return),'full_return':float(b27.total_return),'full_dd':float(b27.max_drawdown),'final_return':float(r('v27_exact_cash','exact','stress','final_2026h1').total_return)}};(OUT/'summary.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
if __name__=='__main__':main()
