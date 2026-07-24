from __future__ import annotations
import hashlib, importlib.util, itertools, json, math, sys
from dataclasses import asdict
from pathlib import Path
import numpy as np, pandas as pd
ROOT=Path('/mnt/data');OUT=ROOT/'v67_blended_onchain_market';OUT.mkdir(exist_ok=True)
WEIGHTS=(.65,.70,.75,.80);CAPS=(1.10,1.15)
PREFINAL_CAGR_MIN=.30;POST2020_CAGR_MIN=.145;DD_MIN=-.27;SHARPE_MIN=1.05;TURNOVER_MAX=6.5;MAX_GROSS_MAX=1.08
ROBUST_CAGR_MIN=.27;ROBUST_DD_MIN=-.30;ROBUST_SHARPE_MIN=.95;CAT_CAGR_MIN=.15;BEST_YEAR_SHARE_MAX=.60;WORST_LOO_MIN=.14

def loadmod(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

def setup():
 v62=loadmod('v62_v67',ROOT/'v62_onchain_spot_perp.py');v64=loadmod('v64_v67',ROOT/'v64_v52_spot_perp.py')
 so,sc,s62,_=v62.frozen_v59_inputs();po,pc,fr=v62.load_perp();feat62=v62.market_features(sc,fr);b115=next(x for x in v62.BUDGETS if x.name=='small_115');f62=v62.overlay_families(s62,feat62,b115);o62=pd.concat([f62[x] for x in ('fast_long','vol_long','funding_long')],keys=range(3)).groupby(level=1).mean().reindex(s62.index).fillna(0)
 so2,sc2,s64,_=v64.frozen_v52_inputs();assert so.equals(so2) and sc.equals(sc2);feat64=v64.market_features(sc,fr);f64=v64.overlay_families(s64,feat64,next(x for x in v64.BUDGETS if x.name=='small_115'));o64=f64['slow_long'].reindex(s64.index).fillna(0)
 return v62,so,sc,po,pc,fr,s62,o62,s64,o64

def candidate(v62,s62,o62,s64,o64,w,cap):
 st=((1-w)*s62+w*s64).clip(lower=0);g=st.sum(axis=1);mask=g>1;st.loc[mask]=st.loc[mask].div(g[mask],axis=0);ov=(1-w)*o62+w*o64
 if cap<=1.10:b=v62.Budget(f'w{int(w*100)}_g110',.18,.10,1.10,per_asset_perp_cap=.13)
 else:b=v62.Budget(f'w{int(w*100)}_g115',.24,.12,1.15,per_asset_perp_cap=.16)
 return st,ov,b

def self_test():
 v62,so,sc,po,pc,fr,s62,o62,s64,o64=setup()
 for w,cap in itertools.product(WEIGHTS,CAPS):
  st,ov,b=candidate(v62,s62,o62,s64,o64,w,cap);assert np.isfinite(st.to_numpy()).all() and np.isfinite(ov.to_numpy()).all()
  for i in (0,len(st)//2,len(st)-1):
   s,p=v62.apply_caps(st.iloc[i].to_numpy(),ov.iloc[i].to_numpy(),b);assert s.sum()+np.abs(p).sum()<=b.gross_cap+1e-12
 changed=sc.copy();changed.iloc[-1]*=11
 print('V67 blended onchain market self-test passed')

def main():
 v62,so,sc,po,pc,fr,s62,o62,s64,o64=setup();rows=[];accounts={};signals={}
 for w,cap in itertools.product(WEIGHTS,CAPS):
  st,ov,b=candidate(v62,s62,o62,s64,o64,w,cap);signals[b.name]=(w,cap,st,ov,b)
  for period in (*v62.SEGMENTS,'prefinal','post_2020','full'):
   a=v62.simulate(so,sc,po,pc,fr,st,ov,b,*v62.PERIODS[period],40);rows.append({'candidate':b.name,'w_v52':w,'target_gross_cap':cap,'scenario':'stress','period':period,**v62.metrics(a)});accounts[(b.name,'stress',period)]=a
  for scen in ('severe','extreme','super_extreme','catastrophic'):
   a=v62.simulate(so,sc,po,pc,fr,st,ov,b,*v62.PERIODS['full'],v62.COSTS[scen]);rows.append({'candidate':b.name,'w_v52':w,'target_gross_cap':cap,'scenario':scen,'period':'full',**v62.metrics(a)});accounts[(b.name,scen,'full')]=a
  for audit,mult,delay,margin in (('funding50',.5,0,.2),('funding150',1.5,0,.2),('delay1',1.,1,.2),('delay2',1.,2,.2),('margin25',1.,0,.25)):
   ba=v62.Budget(**{**asdict(b),'initial_margin_ratio':margin})
   a=v62.simulate(so,sc,po,pc,fr,st,ov,ba,*v62.PERIODS['prefinal'],40,funding_multiplier=mult,execution_delay=delay);rows.append({'candidate':b.name,'w_v52':w,'target_gross_cap':cap,'scenario':audit,'period':'prefinal',**v62.metrics(a)});accounts[(b.name,audit,'prefinal')]=a
 table=pd.DataFrame(rows);table.to_csv(OUT/'metrics.csv',index=False);rank=[]
 for name,(w,cap,st,ov,b) in signals.items():
  def r(s,p):return table[(table.candidate==name)&(table.scenario==s)&(table.period==p)].iloc[0]
  pre=r('stress','prefinal');post=r('stress','post_2020');cat=r('catastrophic','full');y=v62.yearly(accounts[(name,'stress','full')]);share,loo=v62.concentration(y);aud=[r(x,'prefinal') for x in ('funding50','funding150','delay1','delay2','margin25')];rc=min(float(x.cagr) for x in aud);rd=min(float(x.max_drawdown) for x in aud);rs=min(float(x.sharpe) for x in aud);allseg=min(float(r('stress',p).total_return) for p in v62.SEGMENTS)>0
  eligible=bool(pre.cagr>=PREFINAL_CAGR_MIN and post.cagr>=POST2020_CAGR_MIN and pre.max_drawdown>=DD_MIN and pre.sharpe>=SHARPE_MIN and pre.annual_turnover<=TURNOVER_MAX and pre.max_gross<=MAX_GROSS_MAX and allseg and cat.cagr>=CAT_CAGR_MIN and rc>=ROBUST_CAGR_MIN and rd>=ROBUST_DD_MIN and rs>=ROBUST_SHARPE_MIN and share<=BEST_YEAR_SHARE_MAX and loo>=WORST_LOO_MIN and pre.liquidations==0)
  score=float(rc+.10*rs+.10*post.cagr-.15*abs(rd)-.03*share-.002*pre.annual_turnover)
  rank.append({'candidate':name,'eligible_before_final':eligible,'score':score,'w_v52':w,'target_gross_cap':cap,'prefinal_cagr':pre.cagr,'prefinal_dd':pre.max_drawdown,'prefinal_sharpe':pre.sharpe,'post2020_cagr':post.cagr,'annual_turnover':pre.annual_turnover,'max_gross':pre.max_gross,'average_perp_gross':pre.average_perp_gross,'catastrophic_full_cagr':cat.cagr,'robust_cagr':rc,'robust_dd':rd,'robust_sharpe':rs,'best_year_share':share,'worst_loo_cagr':loo,'all_stress_segments_positive':allseg,'liquidations':pre.liquidations})
 ranking=pd.DataFrame(rank).sort_values(['eligible_before_final','score'],ascending=False);ranking.to_csv(OUT/'ranking.csv',index=False);leader=str(ranking.iloc[0].candidate)
 proof={'candidate':'ACTIVE_V67_BLENDED_ONCHAIN_SPOT_PERP','selection_excludes_final':True,'program_level_final_is_pristine':False,'weights':WEIGHTS,'caps':CAPS,'gates':{'prefinal_cagr_min':PREFINAL_CAGR_MIN,'post2020_cagr_min':POST2020_CAGR_MIN,'dd_min':DD_MIN,'sharpe_min':SHARPE_MIN,'turnover_max':TURNOVER_MAX,'max_gross_max':MAX_GROSS_MAX,'robust_cagr_min':ROBUST_CAGR_MIN,'robust_dd_min':ROBUST_DD_MIN,'robust_sharpe_min':ROBUST_SHARPE_MIN,'catastrophic_cagr_min':CAT_CAGR_MIN,'best_year_share_max':BEST_YEAR_SHARE_MAX,'worst_loo_min':WORST_LOO_MIN},'ranking':ranking.to_dict(orient='records'),'leader':leader};proof['selection_proof_sha256']=hashlib.sha256(json.dumps(proof,sort_keys=True,default=list).encode()).hexdigest();(OUT/'selection_proof_before_final.json').write_text(json.dumps(proof,indent=2,default=list))
 w,cap,st,ov,b=signals[leader];final=v62.simulate(so,sc,po,pc,fr,st,ov,b,*v62.PERIODS['final_2026_ytd'],40);accounts[(leader,'stress','final_2026_ytd')]=final;fm=v62.metrics(final);full=accounts[(leader,'stress','full')];full.to_csv(OUT/'leader_equity.csv');v62.yearly(full).to_csv(OUT/'leader_yearly.csv',index=False)
 lead=ranking.iloc[0];status='frozen_paper_forward_candidate' if bool(lead.eligible_before_final) and fm['average_gross']>.001 and fm['total_return']>0 else ('historical_small_leverage_candidate_needs_nonzero_forward' if bool(lead.eligible_before_final) else 'rejected_or_needs_iteration')
 summary={'candidate':'ACTIVE_V67_BLENDED_ONCHAIN_SPOT_PERP','status':status,'leader':leader,'leader_policy':asdict(b),'w_v52':w,'checks':{'eligible_before_final':bool(lead.eligible_before_final),'final_nonzero':fm['average_gross']>.001,'final_positive':fm['total_return']>0},'prefinal':v62.metrics(accounts[(leader,'stress','prefinal')]),'post_2020':v62.metrics(accounts[(leader,'stress','post_2020')]),'full_cost_sensitivity':{s:v62.metrics(accounts[(leader,s,'full')]) for s in ('stress','severe','extreme','super_extreme','catastrophic')},'final':fm,'robust_floors':{'cagr':float(lead.robust_cagr),'max_drawdown':float(lead.robust_dd),'sharpe':float(lead.robust_sharpe)},'concentration':{'best_year_share':float(lead.best_year_share),'worst_loo_cagr':float(lead.worst_loo_cagr)},'selection_proof_sha256':proof['selection_proof_sha256']};(OUT/'summary.json').write_text(json.dumps(summary,indent=2,default=float));print(json.dumps(summary,indent=2,default=float))
if __name__=='__main__':
 if '--self-test' in sys.argv:self_test()
 else:main()
