from __future__ import annotations
import argparse, hashlib, importlib.util, json, sys
from dataclasses import asdict, dataclass
from pathlib import Path
import numpy as np, pandas as pd
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('screen',HERE/'run_research.py');screen=importlib.util.module_from_spec(spec);sys.modules['screen']=screen;spec.loader.exec_module(screen)

@dataclass(frozen=True)
class ExecPolicy:
 name:str; band:float; max_age:int; step_fraction:float; risk_reduction_buffer:float

POLICIES=(
 ExecPolicy('band03_age7_full',.03,7,1.0,.01),
 ExecPolicy('band05_age14_full',.05,14,1.0,.01),
 ExecPolicy('band08_age14_full',.08,14,1.0,.02),
 ExecPolicy('band08_age28_full',.08,28,1.0,.02),
 ExecPolicy('band10_age28_full',.10,28,1.0,.02),
 ExecPolicy('band12_age28_full',.12,28,1.0,.03),
 ExecPolicy('band10_age42_half',.10,42,.50,.02),
 ExecPolicy('band12_age56_half',.12,56,.50,.03),
)

def apply_policy(desired_s,desired_p,held_s,held_p,age,p:ExecPolicy):
 d=np.r_[desired_s,desired_p];h=np.r_[held_s,held_p];change=float(np.abs(d-h).sum());dg=float(desired_s.sum()+np.abs(desired_p).sum());hg=float(held_s.sum()+np.abs(held_p).sum())
 sign_flip=bool(np.any(np.sign(desired_p)!=np.sign(held_p)) and np.any(np.abs(desired_p-held_p)>1e-6))
 force_reduce=dg<hg-p.risk_reduction_buffer or (dg==0 and hg>0) or sign_flip
 if force_reduce or age>=p.max_age or change>=p.band:
  if force_reduce or p.step_fraction>=.999:ns,np_=desired_s.copy(),desired_p.copy()
  else:ns=held_s+p.step_fraction*(desired_s-held_s);np_=held_p+p.step_fraction*(desired_p-held_p)
  return ns,np_,0,True
 return held_s.copy(),held_p.copy(),age+1,False

def simulate(v75,index,so,sh,sl,sc,po,ph,pl,pc,fr,inputs,p:ExecPolicy,audit):
 v27,s27,p27,s4,s67,p67,vg,vs,vp,credit,vol=inputs;arr=[x.to_numpy(float) if hasattr(x,'to_numpy') else np.asarray(x,float) for x in (s27,p27,s4,s67,p67,vg,vs,vp,credit,vol)];s27a,p27a,s4a,s67a,p67a,vga,vsa,vpa,ca,va=arr
 if audit.execution_delay:
  def delay(x):o=np.zeros_like(x);o[audit.execution_delay:]=x[:-audit.execution_delay];return o
  s27a,p27a,s4a,s67a,p67a=map(delay,(s27a,p27a,s4a,s67a,p67a));vga,vsa,vpa,ca,va=map(delay,(vga,vsa,vpa,ca,va))
 so,sh,sl,sc=[x.to_numpy(float) for x in (so,sh,sl,sc)];po,ph,pl,pc=[x.to_numpy(float) for x in (po,ph,pl,pc)];fr=fr.to_numpy(float)*audit.funding_multiplier;lookup={t:i for i,t in enumerate(pd.date_range(screen.START,screen.END-pd.Timedelta(hours=8),freq='8h',tz='UTC'))}
 cash=initial=hw=10000.;spot=np.zeros(len(screen.SPOT_SYMBOLS));perp=np.zeros(2);held_s=np.zeros(len(screen.SPOT_SYMBOLS));held_p=np.zeros(2);age=10**9;prev=prev8=None;rec=[];rate=audit.cost_bps/10000.
 for i,day in enumerate(index):
  forced=funding=liq=0.;i0=lookup[day]
  if prev is not None:
   for j in np.flatnonzero(spot>0):
    if np.isfinite(so[i,j]) and np.isfinite(sc[prev,j]) and sc[prev,j]>0:spot[j]*=so[i,j]/sc[prev,j]
    else:n=spot[j];pen=n*max(rate,screen.FORCED_PENALTY);cash+=max(0,n-pen);forced+=pen;spot[j]=0;held_s[j]=0
   ratio=np.divide(po[i0],pc[prev8],out=np.ones(2),where=np.isfinite(po[i0])&np.isfinite(pc[prev8])&(pc[prev8]>0));cash+=float(np.sum(perp*(ratio-1)));perp*=ratio;z=float(np.sum(-perp*fr[i0]));cash+=z;funding+=z
  eo=float(cash+spot.sum());actual_s=spot/max(eo,1e-12);actual_p=perp/max(eo,1e-12);des_s,des_p,w4,q,cap,stages=v75.targets_for_day(i,hw,initial,s27a,p27a,s4a,s67a,p67a,vga,vsa,vpa,va,audit);ds,dp,age,traded=apply_policy(des_s,des_p,held_s,held_p,age,p);held_s,held_p=ds.copy(),dp.copy()
  # ensure current availability and feasibility; risk reductions remain immediate
  avail=np.isfinite(so[i])&np.isfinite(sc[i]);ds[~avail]=0;held_s[~avail]=0
  gross=float(ds.sum()+np.abs(dp).sum());
  if gross>cap and gross>0:ds*=cap/gross;dp*=cap/gross;held_s,held_p=ds.copy(),dp.copy()
  req=float(ds.sum()+audit.initial_margin_ratio*np.abs(dp).sum()+audit.operational_reserve)
  if req>1:
   market=float(ds.sum()+audit.initial_margin_ratio*np.abs(dp).sum());z=max(0,(1-audit.operational_reserve)/market) if market>0 else 0;z=min(1,z);ds*=z;dp*=z;held_s,held_p=ds.copy(),dp.copy()
  turnover=float(np.abs(ds-actual_s).sum()+np.abs(dp-actual_p).sum());tc=eo*turnover*rate;after=max(0,eo-tc);spot=ds*after;perp=dp*after;cash=after-spot.sum();
  if audit.apply_cash_credit:cash+=after*max(0,1-w4)*ca[i]
  sr=np.divide(sc[i],so[i],out=np.ones(len(screen.SPOT_SYMBOLS)),where=np.isfinite(sc[i])&np.isfinite(so[i])&(so[i]>0));spot*=sr;minbuf=1e9
  for k in range(3):
   j=i0+k;r=np.divide(pc[j],po[j],out=np.ones(2),where=np.isfinite(pc[j])&np.isfinite(po[j])&(po[j]>0));cash+=float(np.sum(perp*(r-1)));perp*=r;high=ph[j]*(1+audit.intrabar_widen);low=pl[j]*(1-audit.intrabar_widen);mark=np.where(perp>=0,low,high);mr=np.divide(mark,pc[j],out=np.ones(2),where=np.isfinite(mark)&np.isfinite(pc[j])&(pc[j]>0));adverse=float(np.sum(perp*(mr-1)));maint=audit.maintenance_margin_ratio*float(np.abs(perp*mr).sum());buf=(cash+adverse-maint)/max(cash+spot.sum(),1e-12);minbuf=min(minbuf,buf)
   if buf<0 and np.any(perp):n=float(np.abs(perp).sum());pen=n*.01;cash-=pen;forced+=pen;liq+=n;perp[:]=0;held_p[:]=0
   if k<2:z=float(np.sum(-perp*fr[j+1]));cash+=z;funding+=z
  eq=float(cash+spot.sum());hw=max(hw,eq);rec.append({'equity':eq,'gross':float((spot.sum()+np.abs(perp).sum())/max(eq,1e-12)),'turnover':turnover,'costs':tc+forced,'funding_pnl':funding,'spot_gross':float(spot.sum()/max(eq,1e-12)),'perp_gross':float(np.abs(perp).sum()/max(eq,1e-12)),'liquidated_notional':liq,'min_margin_buffer':minbuf,'regime_overlay_gross':0.,'target_age':age,'target_changed':int(traded),'high_water':hw});prev=i;prev8=i0+2
 return pd.DataFrame(rec,index=index)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--atlas-root',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args();out=a.output;out.mkdir(parents=True,exist_ok=True);v75=screen.load_module('v75_exec',a.atlas_root/'source'/'v75_operational_feedback_engine.py');idx,so,sh,sl,sc,q,t=screen.load_daily(a.atlas_root/'inputs'/'asset'/'v6');po,ph,pl,pc,fr=screen.load_perp(a.atlas_root/'inputs'/'asset'/'v5'/'processed');inputs=screen.load_base_inputs(a.atlas_root,v75,idx)
 base=v75.simulate(idx,so,sh,sl,sc,po,ph,pl,pc,fr,inputs,v75.Audit('stress'));base.to_csv(out/'v75_original_stress_equity.csv');basey=screen.yearly(base).rename(columns={'return':'V75_original'});basey.to_csv(out/'v75_original_annual_returns.csv',index=False);basepre=screen.period_metrics(base,screen.START,screen.PREFINAL_END);basefull=screen.account_metrics(base);rows=[];accs={}
 for p in POLICIES:
  st=simulate(v75,idx,so,sh,sl,sc,po,ph,pl,pc,fr,inputs,p,screen.Audit('stress'));sv=simulate(v75,idx,so,sh,sl,sc,po,ph,pl,pc,fr,inputs,p,screen.Audit('severe'));accs[p.name]=st;m=screen.period_metrics(st,screen.START,screen.PREFINAL_END);sm=screen.period_metrics(sv,screen.START,screen.PREFINAL_END);seg={k:screen.period_metrics(st,*w)['total_return'] for k,w in screen.SEGMENTS.items()};eligible=bool(m['cagr']>=basepre['cagr']+.005 and m['max_drawdown']>=-.23 and m['sharpe']>=basepre['sharpe']-.03 and min(seg.values())>0 and sm['cagr']>.20 and sm['max_drawdown']>-.32 and m['annual_turnover']<=basepre['annual_turnover']*.90);score=m['cagr']-basepre['cagr']+.10*(m['sharpe']-basepre['sharpe'])+.05*(abs(basepre['max_drawdown'])-abs(m['max_drawdown']))+.003*(basepre['annual_turnover']-m['annual_turnover'])+.08*min(seg.values());rows.append({'policy':p.name,'eligible_before_final':eligible,'score':score,**asdict(p),**{f'prefinal_{k}':v for k,v in m.items()},'severe_cagr':sm['cagr'],'severe_dd':sm['max_drawdown'],**{f'segment_{k}':v for k,v in seg.items()}});print(p.name,m,eligible,flush=True)
 rank=pd.DataFrame(rows).sort_values(['eligible_before_final','score'],ascending=[False,False]);rank.to_csv(out/'selection_ranking_before_final.csv',index=False);el=rank[rank.eligible_before_final];names=list((el if not el.empty else rank).head(3).policy);selected=[next(p for p in POLICIES if p.name==n) for n in names]
 # A predeclared neighboring execution ensemble is implemented as median policy parameters.
 central=ExecPolicy('V100_EXECUTION_PLATEAU',float(np.median([p.band for p in selected])),int(np.median([p.max_age for p in selected])),float(np.median([p.step_fraction for p in selected])),float(np.median([p.risk_reduction_buffer for p in selected])))
 proof={'candidate':'ACTIVE_V100_EXECUTION_PLATEAU','selection_uses_2021_2025_only':True,'program_level_final_pristine':False,'selected_components':[asdict(p) for p in selected],'central_policy':asdict(central),'eligible_before_final':bool(not el.empty),'baseline_prefinal':basepre,'gates':{'cagr_uplift_min':.005,'max_drawdown_min':-.23,'sharpe_floor_vs_base':-.03,'all_segments_positive':True,'severe_cagr_min':.20,'severe_dd_min':-.32,'turnover_ratio_max':.90}};proof['sha256']=hashlib.sha256(json.dumps(proof,sort_keys=True,default=float).encode()).hexdigest();(out/'selection_proof_before_final.json').write_text(json.dumps(proof,indent=2,default=float))
 audits=[];accounts={}
 for au in screen.AUDITS:
  acc=simulate(v75,idx,so,sh,sl,sc,po,ph,pl,pc,fr,inputs,central,au);accounts[au.name]=acc;acc.to_csv(out/f'{au.name}_equity.csv');audits.append({'audit':au.name,**asdict(au),**screen.account_metrics(acc),'prefinal_cagr':screen.period_metrics(acc,screen.START,screen.PREFINAL_END)['cagr'],'final_2026h1_return':screen.period_metrics(acc,screen.PREFINAL_END,screen.END)['total_return'],'target_changes':int(acc.target_changed.sum())})
 adf=pd.DataFrame(audits);adf.to_csv(out/'audit_metrics.csv',index=False);cand=accounts['stress'];pre=screen.period_metrics(cand,screen.START,screen.PREFINAL_END);final=screen.period_metrics(cand,screen.PREFINAL_END,screen.END);seg={k:screen.period_metrics(cand,*w)['total_return'] for k,w in screen.SEGMENTS.items()};checks={'eligible_before_final':bool(not el.empty),'prefinal_uplift_ge_0_5pp':pre['cagr']>=basepre['cagr']+.005,'prefinal_dd_gt_minus23':pre['max_drawdown']>=-.23,'prefinal_sharpe_within_0_03_of_base':pre['sharpe']>=basepre['sharpe']-.03,'all_prefinal_segments_positive':min(seg.values())>0,'turnover_reduction_ge10pct':pre['annual_turnover']<=basepre['annual_turnover']*.90,'severe_cagr_gt20':float(adf[adf.audit=='severe'].iloc[0].cagr)>.20,'extreme_cagr_gt12':float(adf[adf.audit=='extreme'].iloc[0].cagr)>.12,'all_liquidations_zero':bool((adf.liquidations==0).all()),'all_margin_buffers_positive':float(adf.min_margin_buffer.min())>0,'delay_cagr_floor_gt15':float(adf[adf.audit.str.startswith('delay_')].cagr.min())>.15,'final_2026h1_positive':final['total_return']>0};status='frozen_historical_candidate_needs_nonzero_forward' if all(checks.values()) else 'rejected_or_needs_iteration';summary={'candidate':'ACTIVE_V100_EXECUTION_PLATEAU','audit':'ACTIVE_V101_V104_IMMUTABLE_AUDITS','status':status,'live_ready':False,'real_leverage_authorized':False,'selected_components':names,'central_policy':asdict(central),'checks':checks,'baseline_v75_full':basefull,'baseline_v75_prefinal':basepre,'candidate_full':screen.account_metrics(cand),'candidate_prefinal':pre,'candidate_final_2026h1':final,'prefinal_segments':seg,'selection_proof_sha256':proof['sha256'],'audit_metrics':adf.to_dict(orient='records'),'limitations':['Selection used 2021-2025; 2026 H1 is diagnostic, not pristine.','The no-trade state machine can miss fast reversals despite immediate risk-reduction rules.','OHLC/funding margin simulation cannot reproduce exchange outage, mark-price gaps, collateral freeze or counterparty failure.']};(out/'summary.json').write_text(json.dumps(summary,indent=2,default=float));basey.merge(screen.yearly(cand).rename(columns={'return':'V100_candidate'}),on='year',how='outer').to_csv(out/'annual_returns.csv',index=False);pd.DataFrame([{'candidate':'V75 original',**basefull},{'candidate':'V100 execution plateau',**screen.account_metrics(cand)}]).to_csv(out/'candidate_comparison.csv',index=False);print(json.dumps(summary,indent=2,default=float))
if __name__=='__main__':main()
