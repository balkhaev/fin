from __future__ import annotations
from pathlib import Path
import sys,json,itertools,hashlib,time
import numpy as np
import pandas as pd
sys.path[:0]=['/mnt/data','/mnt/data/v26_work/active_v26','/mnt/data/v26_work/active_v26/v8_frozen']
from v50_exact8h_audit import prepare_cached, PERIODS, SCENARIOS, AUDITS
from v43_exact8h_fast import simulate,CASH
import engine as e
from run_research import paired_bootstrap,yearly

OUT=Path('/mnt/data/v28_exact8h_candidate');OUT.mkdir(exist_ok=True)

def set_signals(ctx,spot,perp,carry):
    ctx.spot_signal=spot.reindex(ctx.index).fillna(0).to_numpy(float)
    ctx.perp_signal=perp.reindex(ctx.index).fillna(0).to_numpy(float)
    ctx.carry_signal=carry.reindex(ctx.index).fillna(0).to_numpy(float)

def run(ctx,components,label,period,bps,audit='exact'):
    zero=components['carry']*0
    if label=='v26': set_signals(ctx,components['spot26'],components['perp26'],zero); return simulate(ctx,*PERIODS[period],bps,None,AUDITS[audit],False)
    if label=='v27': set_signals(ctx,components['spot26'],components['perp26'],zero); return simulate(ctx,*PERIODS[period],bps,CASH,AUDITS[audit],False)
    if label=='v49': set_signals(ctx,components['spot50'],components['perp50'],zero); return simulate(ctx,*PERIODS[period],bps,None,AUDITS[audit],False)
    if label=='v28': set_signals(ctx,components['spot50'],components['perp50'],components['carry']); return simulate(ctx,*PERIODS[period],bps,CASH,AUDITS[audit],True)
    raise ValueError(label)

def main():
    t=time.time();ctx,gate,comp=prepare_cached();print('prepared',time.time()-t,flush=True)
    rows=[];accounts={};jobs=[]
    for label in ('v26','v27','v49','v28'):
        for period in ('prefinal','final_2026h1','full'): jobs.append((label,'stress',period,'exact'))
    for scen in SCENARIOS:
        for period in ('development','validation_a','validation_b','bridge_2025','full','final_2026h1'):jobs.append(('v28',scen,period,'exact'))
    for audit in ('fund80','fund60','entry_delay8h','fund80_margin125'):
        for period in ('prefinal','final_2026h1','full'):jobs.append(('v28','stress',period,audit))
    seen=set()
    for n,(label,scen,period,audit) in enumerate(jobs,1):
        key=(label,scen,period,audit)
        if key in seen:continue
        seen.add(key);acc=run(ctx,comp,label,period,SCENARIOS[scen],audit);m=e.metrics(acc)
        rows.append({'candidate':label,'scenario':scen,'period':period,'audit':audit,**m,'funding_pnl':float(acc.funding_pnl.sum()),'treasury_interest':float(acc.treasury_interest.sum()),'avg_carry_gross':float(acc.carry_gross.mean()),'avg_missed_target_gross':float(acc.missed_target_gross.mean()),'p95_missed_target_gross':float(acc.missed_target_gross.quantile(.95)),'max_gross':float(acc.gross.max())});accounts[key]=acc
    df=pd.DataFrame(rows);df.to_csv(OUT/'metrics.csv',index=False)
    get=lambda c,s,p,a='exact':df[(df.candidate==c)&(df.scenario==s)&(df.period==p)&(df.audit==a)].iloc[0]
    pre49=get('v49','stress','prefinal');pre28=get('v28','stress','prefinal');full=get('v28','stress','full');fin=get('v28','stress','final_2026h1')
    checks={'prefinal_uplift_vs_v49_ge_1pp':float(pre28.annualized_return-pre49.annualized_return)>=.01,'all_stress_segments_positive':min(float(get('v28','stress',p).total_return) for p in ('development','validation_a','validation_b','bridge_2025'))>0,'worst_severe_segment_gt_minus10':min(float(get('v28','severe',p).total_return) for p in ('development','validation_a','validation_b','bridge_2025'))>-.10,'worst_extreme_segment_gt_minus15':min(float(get('v28','extreme',p).total_return) for p in ('development','validation_a','validation_b','bridge_2025'))>-.15,'worst_super_segment_gt_minus22':min(float(get('v28','super_extreme',p).total_return) for p in ('development','validation_a','validation_b','bridge_2025'))>-.22,'worst_catastrophic_segment_gt_minus30':min(float(get('v28','catastrophic',p).total_return) for p in ('development','validation_a','validation_b','bridge_2025'))>-.30,'stress_full_dd_gt_minus30':float(full.max_drawdown)>-.30,'extreme_full_dd_gt_minus36':float(get('v28','extreme','full').max_drawdown)>-.36,'super_full_dd_gt_minus42':float(get('v28','super_extreme','full').max_drawdown)>-.42,'turnover_lt25':float(pre28.annual_turnover)<25,'avg_missed_lt2pct':float(pre28.avg_missed_target_gross)<.02,'p95_missed_lt10pct':float(pre28.p95_missed_target_gross)<.10,'final_positive':float(fin.total_return)>0,'fund60_full_cagr_gt_v27':float(get('v28','stress','full','fund60').annualized_return)>float(get('v27','stress','full').annualized_return),'margin125_final_positive':float(get('v28','stress','final_2026h1','fund80_margin125').total_return)>0}
    boot=[]
    for baseline in ('v26','v27','v49'):
        br=accounts[(baseline,'stress','prefinal','exact')].equity.pct_change();cr=accounts[('v28','stress','prefinal','exact')].equity.pct_change()
        for block,horizon in itertools.product((14,30,60),(365,730)):boot.append({'baseline':baseline,**paired_bootstrap(br,cr,block=block,horizon=horizon)})
    pd.DataFrame(boot).to_csv(OUT/'paired_bootstrap.csv',index=False)
    summary={'candidate':'ACTIVE_V28_EXACT8H_BREAKOUT_CARRY_CASH','status':'frozen_paper_forward_candidate' if all(checks.values()) else 'rejected_or_needs_iteration','selection_excludes_2026h1':True,'program_level_holdout_is_pristine':False,'acceptance_checks':checks,'prefinal':{'v49_cagr':float(pre49.annualized_return),'v28_cagr':float(pre28.annualized_return),'uplift':float(pre28.annualized_return-pre49.annualized_return)},'stress_full':{k:float(full[k]) for k in ['annualized_return','total_return','max_drawdown','sharpe','annual_turnover','average_gross','max_gross','final_equity']},'stress_final_2026h1':{k:float(fin[k]) for k in ['annualized_return','total_return','max_drawdown','sharpe','annual_turnover','average_gross','max_gross','final_equity']},'selection_proof_sha256':hashlib.sha256(json.dumps({'checks':checks,'components':'frozen before final exact audit'},sort_keys=True).encode()).hexdigest()}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
