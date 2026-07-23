from __future__ import annotations
from dataclasses import asdict
import numpy as np,pandas as pd
from config import Config,Process
from metrics import metrics

def schedule(w,every,band=.10):
 a=w.to_numpy(float);out=np.zeros_like(a);cur=np.zeros(a.shape[1])
 for i,row in enumerate(a):
  row=np.nan_to_num(row);cur[(np.abs(cur)>0)&(np.abs(row)<=0)]=0
  if i%every==0 and np.abs(row-cur).sum()>=band:cur=row.copy()
  g=np.abs(cur).sum();out[i]=cur*(1/g if g>1 else 1)
 return pd.DataFrame(out,index=w.index,columns=w.columns)
def select_unit_weights(m,score,long_k,short_k,beta_mode):
 S=score.to_numpy(float);A=m.available.to_numpy(bool);V=m.vol.to_numpy(float);B=m.beta(90).to_numpy(float);out=np.zeros_like(S)
 for i in range(len(S)):
  valid=np.flatnonzero(A[i]&np.isfinite(S[i])&np.isfinite(V[i])&(V[i]>1e-6))
  if len(valid)<long_k+short_k:continue
  order=valid[np.argsort(S[i,valid])];short=order[:short_k];long=order[-long_k:]
  if S[i,long].mean()<=0 or S[i,short].mean()>=0:continue
  li=1/V[i,long];li/=li.sum();si=1/V[i,short];si/=si.sum();lw=li;sw=si
  if beta_mode=='beta':
   bl=float(np.sum(lw*np.maximum(B[i,long],.05)));bs=float(np.sum(sw*np.maximum(B[i,short],.05)))
   if bs>0:sw*=np.clip(bl/bs,.5,1.5)
   total=lw.sum()+sw.sum()
   if total>2:lw*=2/total;sw*=2/total
  out[i,long]=lw;out[i,short]=-sw
 return pd.DataFrame(out,index=m.index,columns=m.symbols)

def select_weights(m,score,long_k,short_k,side_gross,beta_mode):
 return select_unit_weights(m,score,long_k,short_k,beta_mode)*side_gross
def vol_scale(m,w,target,max_gross=.85):
 unit=(w.shift(1).fillna(0)*m.returns.fillna(0)).sum(axis=1);rv=unit.rolling(60,min_periods=60).std()*np.sqrt(365);scale=(target/rv.replace(0,np.nan)).clip(upper=2).fillna(0);x=w.mul(scale,axis=0);g=x.abs().sum(axis=1);return x.mul((max_gross/g.replace(0,np.nan)).clip(upper=1).fillna(0),axis=0)
def family_library(m):
 fam={};counts={};template=pd.DataFrame(0.,index=m.index,columns=m.symbols)

 def assemble(score_map, ks, sides, neutral_modes, schedules, target=.20):
  unit_cache={}
  variants=[]
  for score_key,score in score_map.items():
   for k in ks:
    for neutral in neutral_modes:
     unit_cache[(score_key,k,neutral)]=select_unit_weights(m,score,k,k,neutral)
     for side in sides:
      scaled=vol_scale(m,unit_cache[(score_key,k,neutral)]*side,target)
      for every in schedules:variants.append(schedule(scaled,every))
  return sum(variants)/len(variants),len(variants)

 score_map={}
 for look in (30,60,90,180):score_map[f'mom_{look}']=m.close.pct_change(look,fill_method=None).div(m.vol.replace(0,np.nan))
 fam['xs_momentum'],counts['xs_momentum']=assemble(score_map,(1,2,3),(.25,.35,.425),('dollar','beta'),(7,14))

 score_map={}
 for bd in (60,90):
  beta=m.beta(bd);resid=m.logret-beta.mul(m.market,axis=0)
  for look in (30,60,90):score_map[f'resid_{bd}_{look}']=resid.rolling(look,min_periods=look).sum().div(resid.rolling(60,min_periods=60).std()*np.sqrt(365))
 fam['residual_momentum'],counts['residual_momentum']=assemble(score_map,(1,2,3),(.25,.35),('dollar','beta'),(7,14))

 score_map={}
 for look in (60,90,180):
  high=np.log(m.close/m.close.rolling(look,min_periods=look).max());low=np.log(m.close/m.close.rolling(look,min_periods=look).min());score_map[f'anchor_{look}']=high+low
 fam['anchor'],counts['anchor']=assemble(score_map,(1,2,3),(.25,.35),('dollar','beta'),(7,14))

 score_map={}
 for look in (30,60,90):
  mom=m.close.pct_change(look,fill_method=None).div(m.vol.replace(0,np.nan))
  for fl in (7,21):
   carry=m.funding.rolling(fl,min_periods=fl).mean()*365*3
   for penalty in (10,25,50):score_map[f'fund_{look}_{fl}_{penalty}']=mom-penalty*carry
 fam['funding_momentum'],counts['funding_momentum']=assemble(score_map,(1,2,3),(.25,.35),('beta',),(7,14))
 return fam,counts

def simulate(m,w,start,end,cost,cfg:Config):
 loc=np.flatnonzero((m.index>=pd.Timestamp(start,tz='UTC'))&(m.index<pd.Timestamp(end,tz='UTC')));O=m.open.to_numpy(float);C=m.close.to_numpy(float);F=m.funding.to_numpy(float);A=m.available.to_numpy(bool);S=w.reindex(m.index).fillna(0).to_numpy(float);n=len(m.symbols);pending=S[loc[0]-1].copy() if loc[0]>0 else np.zeros(n);notional=np.zeros(n);cash=cfg.starting_equity;prev=-1;rows=[]
 for i in loc:
  forced=0
  if prev>=0:
   for j in np.flatnonzero(np.abs(notional)>0):
    if np.isfinite(O[i,j]) and np.isfinite(C[prev,j]):cash+=notional[j]*(O[i,j]/C[prev,j]-1);notional[j]*=O[i,j]/C[prev,j]
    else:forced+=abs(notional[j]);cash-=abs(notional[j])*max(cost,cfg.forced_exit_penalty_bps/10000);notional[j]=0
  eq=cash;actual=notional/eq;target=np.nan_to_num(pending);target[~A[i]]=0;g=np.abs(target).sum();target*=min(1,cfg.max_gross/g) if g>0 else 1;turn=float(np.abs(target-actual).sum())+forced/eq;tc=eq*float(np.abs(target-actual).sum())*cost;cash-=tc;notional=target*(cash)
  ratio=np.divide(C[i],O[i],out=np.ones(n),where=np.isfinite(C[i])&np.isfinite(O[i]));cash+=float(np.sum(notional*(ratio-1)));fp=float(np.sum(-(notional*F[i])));cash+=fp;notional*=ratio;equity=cash;rows.append({'equity':equity,'gross':float(np.abs(notional).sum()/equity),'turnover':turn,'costs':tc+forced*max(cost,cfg.forced_exit_penalty_bps/10000),'funding_pnl':fp});pending=S[i].copy();prev=int(i)
 return pd.DataFrame(rows,index=m.index[loc])

def score_account(a,mode):
 x=metrics(a)
 if x['max_drawdown']<-.4 or x['annual_turnover']>45:return -1e9
 if mode=='robust':return x['sharpe']+.5*x['calmar']+.25*x['annualized_return']-.01*x['annual_turnover']
 yr=[z.equity.iloc[-1]/z.equity.iloc[0]-1 for _,z in a.groupby(a.index.year) if len(z)>1];return min(yr)+.5*x['annualized_return']+.25*x['sharpe']-.005*x['annual_turnover']

def process_frame(m,fam,p:Process,cfg,costs,accounts=None):
 template=next(iter(fam.values()))
 if p.kind=='static':return sum(fam[x] for x in p.subset)/len(p.subset),[]
 arrays={k:v.to_numpy(float) for k,v in fam.items()}
 if accounts is None:accounts={(k,c):simulate(m,v,cfg.start,cfg.end_exclusive,costs[c],cfg) for k,v in fam.items() for c in ('stress','severe')}
 out=np.zeros_like(template.to_numpy(float));selected=[];last=None;logs=[]
 for i,t in enumerate(m.index):
  if t<pd.Timestamp('2021-01-01',tz='UTC'):continue
  if last is None or (t-last).days>=p.selection_days:
   start=max(m.index[0],t-pd.Timedelta(days=p.train_days));scores={}
   for k in fam:
    vals=[]
    for c in ('stress','severe'):
     seg=accounts[(k,c)][(accounts[(k,c)].index>=start)&(accounts[(k,c)].index<=t)]
     vals.append(score_account(seg,p.score_mode) if len(seg)>180 else -1e9)
    scores[k]=min(vals)
   selected=[k for k,_ in sorted(scores.items(),key=lambda z:z[1],reverse=True)[:p.top_k]];last=t;logs.append({'time':t.isoformat(),'selected':'+'.join(selected),**scores})
  if selected:out[i]=np.mean([arrays[k][i] for k in selected],axis=0)
 return pd.DataFrame(out,index=m.index,columns=m.symbols),logs
