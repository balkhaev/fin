from __future__ import annotations
import importlib.util,sys,json
from pathlib import Path
from dataclasses import asdict
import pandas as pd,numpy as np
ROOT=Path('/mnt/data');OUT=ROOT/'v68_v67_intrabar';OUT.mkdir(exist_ok=True)
def lm(n,p):s=importlib.util.spec_from_file_location(n,p);m=importlib.util.module_from_spec(s);sys.modules[n]=m;s.loader.exec_module(m);return m
v62=lm('v62a',ROOT/'v62_onchain_spot_perp.py');v64=lm('v64a',ROOT/'v64_v52_spot_perp.py');v66=lm('v66a',ROOT/'v66_v65_intrabar_margin_audit.py')
so,sc,s62,_=v62.frozen_v59_inputs();po,hi,lo,pc,fr=v66.load_perp_ohlc();feat62=v62.market_features(sc,fr);b115=next(x for x in v62.BUDGETS if x.name=='small_115');f62=v62.overlay_families(s62,feat62,b115);o62=pd.concat([f62[x] for x in ('fast_long','vol_long','funding_long')],keys=range(3)).groupby(level=1).mean().reindex(s62.index).fillna(0)
_,_,s64,_=v64.frozen_v52_inputs();feat64=v64.market_features(sc,fr);f64=v64.overlay_families(s64,feat64,next(x for x in v64.BUDGETS if x.name=='small_115'));o64=f64['slow_long'].reindex(s64.index).fillna(0)
w=.8;spot=(1-w)*s62+w*s64;g=spot.sum(axis=1);mk=g>1;spot.loc[mk]=spot.loc[mk].div(g[mk],axis=0);overlay=(1-w)*o62+w*o64
base=v62.Budget('v67_w80_g115',.24,.12,1.15,initial_margin_ratio=.2,maintenance_margin_ratio=.08,operational_cash_reserve=.03,per_asset_perp_cap=.16)
specs=[('observed',.25,.10,.04,1.,0,0.,40.),('harsh',.50,.20,.07,3.,2,0.,40.),('widen10',.50,.20,.07,3.,2,.10,40.),('widen20_cost120',.50,.20,.07,3.,2,.20,120.)]
rows=[]
for name,im,mm,res,fm,delay,widen,cost in specs:
 b=v62.Budget(**{**asdict(base),'initial_margin_ratio':im,'maintenance_margin_ratio':mm,'operational_cash_reserve':res})
 for period in ('prefinal','post_2020','full','final_2026_ytd'):
  a=v66.simulate(so,sc,po,hi,lo,pc,fr,spot,overlay,b,*v62.PERIODS[period],cost,fm,delay,widen);m=v62.metrics(a);m['min_buffer']=float(a.min_intrabar_margin_buffer.min());m['liq_notional']=float(a.liquidated_notional.sum());rows.append({'audit':name,'period':period,**m})
t=pd.DataFrame(rows);t.to_csv(OUT/'metrics.csv',index=False);checks={'no_liquidations':bool((t.liquidations==0).all()),'min_buffer_positive':float(t.min_buffer.min())>0,'widen20_full_cagr_gt10':float(t[(t.audit=='widen20_cost120')&(t.period=='full')].iloc[0].cagr)>.10,'all_prefinal_positive':bool((t[t.period=='prefinal'].cagr>0).all()),'all_post2020_positive':bool((t[t.period=='post_2020'].cagr>0).all()),'final_nonzero':float(t[(t.audit=='observed')&(t.period=='final_2026_ytd')].iloc[0].average_gross)>.001};summary={'candidate':'V68_V67_INTRABAR','checks':checks,'status':'intrabar_passed_forward_unproven' if all(v for k,v in checks.items() if k!='final_nonzero') else 'failed','rows':t.to_dict(orient='records')};(OUT/'summary.json').write_text(json.dumps(summary,indent=2,default=float));print(json.dumps(summary,indent=2,default=float))
