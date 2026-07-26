from __future__ import annotations
import argparse, hashlib, importlib.util, json, sys
from dataclasses import asdict
from pathlib import Path
import pandas as pd

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('screen',HERE/'run_research.py');screen=importlib.util.module_from_spec(spec);sys.modules['screen']=screen;spec.loader.exec_module(screen)

COMPONENT_SPECS=(
 ('breadth','defensive','relative','adaptive',0.15,0.05),
 ('breadth','symmetric','relative','adaptive',0.10,0.10),
 ('breadth','growth','balanced','adaptive',0.05,0.15),
 ('breadth','symmetric','balanced','adaptive',0.10,0.10),
)

def policy_from(spec):
 family,style,alloc,risk,short_budget,long_budget=spec
 return screen.Policy(f'{family}_{style}_{alloc}_{risk}_frozen',family,style,alloc,risk,long_budget,short_budget,0.10)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--atlas-root',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args();out=a.output;out.mkdir(parents=True,exist_ok=True)
 v75=screen.load_module('v75_frozen_breadth',a.atlas_root/'source'/'v75_operational_feedback_engine.py')
 index,so,sh,sl,sc,quote,taker=screen.load_daily(a.atlas_root/'inputs'/'asset'/'v6');po,ph,pl,pc,fr=screen.load_perp(a.atlas_root/'inputs'/'asset'/'v5'/'processed');base_inputs=screen.load_base_inputs(a.atlas_root,v75,index)
 base=v75.simulate(index,so,sh,sl,sc,po,ph,pl,pc,fr,base_inputs,v75.Audit('stress'));base.to_csv(out/'v75_original_stress_equity.csv');base_y=screen.yearly(base).rename(columns={'return':'V75_original'});base_y.to_csv(out/'v75_original_annual_returns.csv',index=False);base_pre=screen.period_metrics(base,screen.START,screen.PREFINAL_END);base_full=screen.account_metrics(base)
 scores,units,pair_vol=screen.build_regime_signals(sc,quote,taker)
 policies=[policy_from(x) for x in COMPONENT_SPECS];overlays=[screen.process_overlay(p,scores[p.signal_family],units,pair_vol) for p in policies]
 rows=[]
 for p,ov in zip(policies,overlays):
  st=screen.simulate(v75,index,so,sh,sl,sc,po,ph,pl,pc,fr,base_inputs,ov,p,screen.Audit('stress'));sv=screen.simulate(v75,index,so,sh,sl,sc,po,ph,pl,pc,fr,base_inputs,ov,p,screen.Audit('severe'));m=screen.period_metrics(st,screen.START,screen.PREFINAL_END);sm=screen.period_metrics(sv,screen.START,screen.PREFINAL_END);seg={k:screen.period_metrics(st,*w)['total_return'] for k,w in screen.SEGMENTS.items()};rows.append({'policy':p.name,**asdict(p),**{f'prefinal_{k}':v for k,v in m.items()},'severe_cagr':sm['cagr'],'severe_dd':sm['max_drawdown'],**{f'segment_{k}':v for k,v in seg.items()}})
 pd.DataFrame(rows).to_csv(out/'frozen_component_prefinal.csv',index=False)
 overlay=sum(overlays)/len(overlays);central=screen.Policy('V89_BREADTH_PLATEAU_ENSEMBLE','breadth','plateau','mixed','adaptive',float(sum(p.long_budget for p in policies)/len(policies)),float(sum(p.short_budget for p in policies)/len(policies)),0.10)
 proof={'candidate':'ACTIVE_V89_BREADTH_PLATEAU_ENSEMBLE','discovery':'V88 exploratory prefinal screen','selection_uses_2021_2025_only':True,'program_level_final_pristine':False,'components':[asdict(p) for p in policies],'ensemble_policy':asdict(central),'baseline_prefinal':base_pre,'gates':{'cagr_uplift_min':.005,'max_drawdown_min':-.25,'sharpe_min':1.25,'worst_segment_min':-.03,'severe_cagr_min':.18,'severe_dd_min':-.35,'turnover_max':15}}
 proof['sha256']=hashlib.sha256(json.dumps(proof,sort_keys=True,default=float).encode()).hexdigest();(out/'selection_proof_before_final.json').write_text(json.dumps(proof,indent=2,default=float))
 audits=[];accounts={}
 for au in screen.AUDITS:
  acc=screen.simulate(v75,index,so,sh,sl,sc,po,ph,pl,pc,fr,base_inputs,overlay,central,au);accounts[au.name]=acc;acc.to_csv(out/f'{au.name}_equity.csv');audits.append({'audit':au.name,**asdict(au),**screen.account_metrics(acc),'prefinal_cagr':screen.period_metrics(acc,screen.START,screen.PREFINAL_END)['cagr'],'final_2026h1_return':screen.period_metrics(acc,screen.PREFINAL_END,screen.END)['total_return']})
 adf=pd.DataFrame(audits);adf.to_csv(out/'audit_metrics.csv',index=False);cand=accounts['stress'];pre=screen.period_metrics(cand,screen.START,screen.PREFINAL_END);final=screen.period_metrics(cand,screen.PREFINAL_END,screen.END);segments={k:screen.period_metrics(cand,*w)['total_return'] for k,w in screen.SEGMENTS.items()}
 checks={'prefinal_uplift_ge_0_5pp':pre['cagr']>=base_pre['cagr']+.005,'prefinal_dd_gt_minus25':pre['max_drawdown']>=-.25,'prefinal_sharpe_ge1_25':pre['sharpe']>=1.25,'all_prefinal_segments_gt_minus3pct':min(segments.values())>-.03,'severe_cagr_gt18':float(adf[adf.audit=='severe'].iloc[0].cagr)>.18,'severe_dd_gt_minus35':float(adf[adf.audit=='severe'].iloc[0].max_drawdown)>-.35,'extreme_cagr_gt10':float(adf[adf.audit=='extreme'].iloc[0].cagr)>.10,'all_liquidations_zero':bool((adf.liquidations==0).all()),'all_margin_buffers_positive':float(adf.min_margin_buffer.min())>0,'delay_cagr_floor_gt15':float(adf[adf.audit.str.startswith('delay_')].cagr.min())>.15,'final_2026h1_positive':final['total_return']>0}
 status='frozen_historical_candidate_needs_nonzero_forward' if all(checks.values()) else 'rejected_or_needs_iteration'
 summary={'candidate':'ACTIVE_V89_BREADTH_PLATEAU_ENSEMBLE','audit':'ACTIVE_V90_V94_IMMUTABLE_AUDITS','status':status,'live_ready':False,'real_leverage_authorized':False,'checks':checks,'baseline_v75_full':base_full,'baseline_v75_prefinal':base_pre,'candidate_full':screen.account_metrics(cand),'candidate_prefinal':pre,'candidate_final_2026h1':final,'prefinal_segments':segments,'selection_proof_sha256':proof['sha256'],'audit_metrics':adf.to_dict(orient='records'),'limitations':['V88 discovery used the known 2021-2025 prefinal sample; V89 freezes a neighboring plateau before opening 2026 H1.','2026 H1 is diagnostic, not a pristine program-level holdout.','OHLC/funding margin simulation cannot reproduce exchange outage, mark-price gaps, collateral freeze or counterparty failure.']};(out/'summary.json').write_text(json.dumps(summary,indent=2,default=float))
 cy=screen.yearly(cand).rename(columns={'return':'V89_candidate'});base_y.merge(cy,on='year',how='outer').to_csv(out/'annual_returns.csv',index=False);pd.DataFrame([{'candidate':'V75 original',**base_full},{'candidate':'V89 breadth plateau',**screen.account_metrics(cand)}]).to_csv(out/'candidate_comparison.csv',index=False)
 print(json.dumps(summary,indent=2,default=float))
if __name__=='__main__':main()
