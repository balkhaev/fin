from __future__ import annotations
import argparse, hashlib, importlib.util, json, sys
from dataclasses import asdict, dataclass
from pathlib import Path
import numpy as np, pandas as pd
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('screen',HERE/'run_research.py');screen=importlib.util.module_from_spec(spec);sys.modules['screen']=screen;spec.loader.exec_module(screen)

@dataclass(frozen=True)
class HPolicy:
 name:str; enter:float; exit:float; confirm:int; exit_confirm:int; min_hold:int; budget:float; allocation:str='mixed'

VARIANTS=(
 HPolicy('hyst_60_42_c3_x3_h14_b10',.60,.42,3,3,14,.10),
 HPolicy('hyst_62_44_c3_x3_h14_b12',.62,.44,3,3,14,.12),
 HPolicy('hyst_65_45_c5_x3_h14_b12',.65,.45,5,3,14,.12),
 HPolicy('hyst_65_45_c5_x5_h21_b15',.65,.45,5,5,21,.15),
 HPolicy('hyst_68_48_c5_x5_h21_b15',.68,.48,5,5,21,.15),
 HPolicy('hyst_70_50_c7_x5_h28_b15',.70,.50,7,5,28,.15),
)

def hysteresis_gate(score:pd.Series,p:HPolicy):
 x=score.to_numpy(float);out=np.zeros(len(x));state=0;hi=lo=0;held=10**9
 for i,v in enumerate(x):
  held+=1
  if state==0:
   hi=hi+1 if np.isfinite(v) and v>=p.enter else 0
   if hi>=p.confirm:state=1;held=0;hi=0
  else:
   lo=lo+1 if np.isfinite(v) and v<=p.exit else 0
   if lo>=p.exit_confirm and held>=p.min_hold:state=0;held=0;lo=0
  out[i]=state
 return pd.Series(out,index=score.index)

def make_overlay(p:HPolicy,score,units,pair_vol):
 gate=hysteresis_gate(score,p)
 # Average balanced and relative allocation to avoid a single winner rule.
 unit=.5*units['balanced_long']+.5*units['relative_long'];unit=unit.div(unit.sum(axis=1).replace(0,np.nan),axis=0).fillna(0)
 volscale=(.40/pair_vol.replace(0,np.nan)).clip(.35,1.0).fillna(0)
 return unit.mul(gate*volscale*p.budget,axis=0)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--atlas-root',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args();out=a.output;out.mkdir(parents=True,exist_ok=True)
 v75=screen.load_module('v75_hyst',a.atlas_root/'source'/'v75_operational_feedback_engine.py');idx,so,sh,sl,sc,q,t=screen.load_daily(a.atlas_root/'inputs'/'asset'/'v6');po,ph,pl,pc,fr=screen.load_perp(a.atlas_root/'inputs'/'asset'/'v5'/'processed');inputs=screen.load_base_inputs(a.atlas_root,v75,idx)
 base=v75.simulate(idx,so,sh,sl,sc,po,ph,pl,pc,fr,inputs,v75.Audit('stress'));base.to_csv(out/'v75_original_stress_equity.csv');basey=screen.yearly(base).rename(columns={'return':'V75_original'});basey.to_csv(out/'v75_original_annual_returns.csv',index=False);basepre=screen.period_metrics(base,screen.START,screen.PREFINAL_END);basefull=screen.account_metrics(base)
 scores,units,pv=screen.build_regime_signals(sc,q,t);overlays={p.name:make_overlay(p,scores['composite'],units,pv) for p in VARIANTS};rows=[]
 for p in VARIANTS:
  pol=screen.Policy(p.name,'composite','growth',p.allocation,'adaptive',p.budget,0,.10);st=screen.simulate(v75,idx,so,sh,sl,sc,po,ph,pl,pc,fr,inputs,overlays[p.name],pol,screen.Audit('stress'));sv=screen.simulate(v75,idx,so,sh,sl,sc,po,ph,pl,pc,fr,inputs,overlays[p.name],pol,screen.Audit('severe'));m=screen.period_metrics(st,screen.START,screen.PREFINAL_END);sm=screen.period_metrics(sv,screen.START,screen.PREFINAL_END);seg={k:screen.period_metrics(st,*w)['total_return'] for k,w in screen.SEGMENTS.items()};eligible=bool(m['cagr']>=basepre['cagr']+.005 and m['max_drawdown']>=-.25 and m['sharpe']>=1.25 and min(seg.values())>-.03 and sm['cagr']>.18 and sm['max_drawdown']>-.35 and m['annual_turnover']<14);scoreval=m['cagr']-basepre['cagr']+.08*(m['sharpe']-basepre['sharpe'])-.12*max(0,abs(m['max_drawdown'])-abs(basepre['max_drawdown']))+.08*min(seg.values())-.001*m['annual_turnover'];rows.append({'policy':p.name,'eligible_before_final':eligible,'score':scoreval,**asdict(p),**{f'prefinal_{k}':v for k,v in m.items()},'severe_cagr':sm['cagr'],'severe_dd':sm['max_drawdown'],**{f'segment_{k}':v for k,v in seg.items()}});print(p.name,m,eligible,flush=True)
 rank=pd.DataFrame(rows).sort_values(['eligible_before_final','score'],ascending=[False,False]);rank.to_csv(out/'selection_ranking_before_final.csv',index=False);eligible=rank[rank.eligible_before_final]
 names=list((eligible if not eligible.empty else rank).head(3).policy);selected=[next(p for p in VARIANTS if p.name==n) for n in names];overlay=sum(overlays[n] for n in names)/len(names);budget=float(np.mean([p.budget for p in selected]));central=screen.Policy('V95_HYSTERESIS_PLATEAU','composite','growth','mixed','adaptive',budget,0,.10)
 proof={'candidate':'ACTIVE_V95_HYSTERESIS_PLATEAU','selection_uses_2021_2025_only':True,'program_level_final_pristine':False,'components':[asdict(p) for p in selected],'ensemble_policy':asdict(central),'eligible_before_final':bool(not eligible.empty),'baseline_prefinal':basepre,'gates':{'cagr_uplift_min':.005,'max_drawdown_min':-.25,'sharpe_min':1.25,'worst_segment_min':-.03,'severe_cagr_min':.18,'severe_dd_min':-.35,'turnover_max':14}};proof['sha256']=hashlib.sha256(json.dumps(proof,sort_keys=True,default=float).encode()).hexdigest();(out/'selection_proof_before_final.json').write_text(json.dumps(proof,indent=2,default=float))
 audits=[];accounts={}
 for au in screen.AUDITS:
  acc=screen.simulate(v75,idx,so,sh,sl,sc,po,ph,pl,pc,fr,inputs,overlay,central,au);accounts[au.name]=acc;acc.to_csv(out/f'{au.name}_equity.csv');audits.append({'audit':au.name,**asdict(au),**screen.account_metrics(acc),'prefinal_cagr':screen.period_metrics(acc,screen.START,screen.PREFINAL_END)['cagr'],'final_2026h1_return':screen.period_metrics(acc,screen.PREFINAL_END,screen.END)['total_return']})
 adf=pd.DataFrame(audits);adf.to_csv(out/'audit_metrics.csv',index=False);cand=accounts['stress'];pre=screen.period_metrics(cand,screen.START,screen.PREFINAL_END);final=screen.period_metrics(cand,screen.PREFINAL_END,screen.END);seg={k:screen.period_metrics(cand,*w)['total_return'] for k,w in screen.SEGMENTS.items()}
 checks={'eligible_before_final':bool(not eligible.empty),'prefinal_uplift_ge_0_5pp':pre['cagr']>=basepre['cagr']+.005,'prefinal_dd_gt_minus25':pre['max_drawdown']>=-.25,'prefinal_sharpe_ge1_25':pre['sharpe']>=1.25,'all_prefinal_segments_gt_minus3pct':min(seg.values())>-.03,'severe_cagr_gt18':float(adf[adf.audit=='severe'].iloc[0].cagr)>.18,'extreme_cagr_gt10':float(adf[adf.audit=='extreme'].iloc[0].cagr)>.10,'all_liquidations_zero':bool((adf.liquidations==0).all()),'all_margin_buffers_positive':float(adf.min_margin_buffer.min())>0,'delay_cagr_floor_gt15':float(adf[adf.audit.str.startswith('delay_')].cagr.min())>.15,'final_2026h1_positive':final['total_return']>0};status='frozen_historical_candidate_needs_nonzero_forward' if all(checks.values()) else 'rejected_or_needs_iteration'
 summary={'candidate':'ACTIVE_V95_HYSTERESIS_PLATEAU','audit':'ACTIVE_V96_V99_IMMUTABLE_AUDITS','status':status,'live_ready':False,'real_leverage_authorized':False,'selected_components':names,'checks':checks,'baseline_v75_full':basefull,'baseline_v75_prefinal':basepre,'candidate_full':screen.account_metrics(cand),'candidate_prefinal':pre,'candidate_final_2026h1':final,'prefinal_segments':seg,'selection_proof_sha256':proof['sha256'],'audit_metrics':adf.to_dict(orient='records'),'limitations':['2021-2025 was used for discovery and selection; 2026 H1 is diagnostic, not pristine.','The overlay is long-only and can amplify a false risk-on regime.','OHLC/funding margin simulation cannot reproduce exchange outage, mark-price gaps, collateral freeze or counterparty failure.']};(out/'summary.json').write_text(json.dumps(summary,indent=2,default=float));cy=screen.yearly(cand).rename(columns={'return':'V95_candidate'});basey.merge(cy,on='year',how='outer').to_csv(out/'annual_returns.csv',index=False);pd.DataFrame([{'candidate':'V75 original',**basefull},{'candidate':'V95 hysteresis',**screen.account_metrics(cand)}]).to_csv(out/'candidate_comparison.csv',index=False);print(json.dumps(summary,indent=2,default=float))
if __name__=='__main__':main()
