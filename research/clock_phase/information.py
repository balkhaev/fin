"""Predictive information and conditional observed price changes ONLY.

This is not the blocked cash ledger. No positions, cash balances, trade fills,
portfolio PnL, compounding or CAGR are implemented here. Signal observations can
overlap across times/assets and must never be represented as independent trades.
"""
from pathlib import Path
import argparse
import hashlib
import json
import math
import numpy as np
import pandas as pd
from .data import load,SYMBOLS
from .learning import prepare,forecasts,MODELS,HORIZONS,PARAMS


def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def save(path,value):path.write_text(json.dumps(value,indent=2,allow_nan=False))


def block_interval(values):
    values=np.asarray(values,float)
    if not len(values):return None
    rng=np.random.default_rng(20260906);length=7;n=len(values);draws=[]
    for _ in range(2000):
        begin=rng.integers(0,n,size=(n+length-1)//length)
        sample=((begin[:,None]+np.arange(length))%n).ravel()[:n]
        draws.append(values[sample].mean())
    return [float(x) for x in np.quantile(draws,[.025,.975])]


def evaluate(p,predictions,start,end,horizon):
    begin=pd.Timestamp(start,tz='UTC');stop=pd.Timestamp(end,tz='UTC')
    maturity=p.index+pd.Timedelta(hours=1+horizon,minutes=2)
    within=(p.index>=begin)&(maturity<stop)
    valid=p.valid&np.isfinite(p.y[horizon])&within[:,None]
    for model in MODELS:valid &= np.isfinite(predictions[model,horizon])
    losses={model:(p.y[horizon]-predictions[model,horizon])**2 for model in MODELS}
    mse={model:float(loss[valid].mean()) for model,loss in losses.items()}
    paired=[]
    for k,asset in enumerate(SYMBOLS):
        for i in np.flatnonzero(valid[:,k]):
            paired.append(dict(signal_date=str(p.index[i].date()),asset=asset,
                base_minus_boundary=float(losses['base'][i,k]-losses['boundary'][i,k]),
                placebo_minus_boundary=float(losses['placebo'][i,k]-losses['boundary'][i,k])))
    frame=pd.DataFrame(paired)
    if frame.empty:raise ValueError('No matured matched observations')
    daily=frame.groupby('signal_date')[['base_minus_boundary','placebo_minus_boundary']].mean()
    tests={col:dict(mean_daily_improvement=float(daily[col].mean()),interval95=block_interval(daily[col]),
                    positive_lower_bound=bool(block_interval(daily[col])[0]>0)) for col in daily}
    # These are conditionally sampled future price changes, NOT executable trades.
    conditional=[];samples=[]
    for model in MODELS:
        expected=predictions[model,horizon]*p.volatility*math.sqrt(horizon)
        raw_log=p.y[horizon]*p.volatility*math.sqrt(horizon)
        selected=valid&(expected>.006)
        for asset_index,asset in list(enumerate(SYMBOLS))+[(None,'POOLED')]:
            mask=selected if asset_index is None else selected[:,asset_index]
            prediction=expected if asset_index is None else expected[:,asset_index]
            observed=raw_log if asset_index is None else raw_log[:,asset_index]
            moves=np.expm1(observed[mask]);forecasts_log=prediction[mask]
            conditional.append(dict(model=model,horizon=horizon,asset=asset,count=int(mask.sum()),
                mean_predicted_log_bps=float(forecasts_log.mean()*10000) if len(moves) else None,
                mean_observed_price_change_bps=float(moves.mean()*10000) if len(moves) else None,
                median_observed_price_change_bps=float(np.median(moves)*10000) if len(moves) else None,
                fraction_price_change_above_30bps=float((moves>.003).mean()) if len(moves) else None,
                roundtrip_cost_reference_bps=30.,portfolio_profit_computed=False,
                overlapping_signal_observations_not_trades=True))
        for i,k in np.argwhere(selected):
            samples.append(dict(model=model,horizon=horizon,asset=SYMBOLS[k],signal_hour=str(p.index[i]),
                feature_available=str(p.index[i]+pd.Timedelta(hours=1)),
                reference_entry_time=str(p.index[i]+pd.Timedelta(hours=1,minutes=2)),
                reference_exit_time=str(maturity[i]),forecast_log=float(expected[i,k]),observed_log=float(raw_log[i,k])))
    summary=dict(start=start,end_exclusive=end,horizon=horizon,matched_observations=int(valid.sum()),
        signal_days=len(daily),mse=mse,paired_tests=tests,
        error_reduction_vs_base_pct=100*(mse['base']-mse['boundary'])/mse['base'],
        error_reduction_vs_placebo_pct=100*(mse['placebo']-mse['boundary'])/mse['placebo'],
        bootstrap_block_days=7,bootstrap_samples=2000,selection_corrected=False,
        independent_market_history=False,conditional_observations=conditional)
    return summary,daily,pd.DataFrame(samples)


def study(root,out):
    out=Path(out)
    if out.exists():raise FileExistsError('Fresh output required')
    frames,audit=load(root);p=prepare(frames);out.mkdir(parents=True)
    predictions,fits=forecasts(p,out/'model_text')
    save(out/'fit_audit.json',fits);save(out/'data_audit.json',audit)
    comparisons=[];prediction_rows=[]
    for period,start,end in [('earlier','2023-01-01','2025-01-01'),('later','2025-01-01','2026-09-01')]:
        for horizon in HORIZONS:
            summary,daily,samples=evaluate(p,predictions,start,end,horizon)
            summary['period']=period;comparisons.append(summary)
            daily.to_csv(out/f'{period}_{horizon}_daily_loss_difference.csv')
            samples.to_csv(out/f'{period}_{horizon}_conditional_prices.csv.gz',index=False,compression='gzip')
            print('INFORMATION',json.dumps(summary),flush=True)
    for i in range(len(p.index)):
        for k,asset in enumerate(SYMBOLS):
            if not all(np.isfinite(predictions[m,h][i,k]) for m in MODELS for h in HORIZONS):continue
            row=dict(signal_hour=str(p.index[i]),asset=asset,hour_volatility=float(p.volatility[i,k]))
            for horizon in HORIZONS:
                row['label'+str(horizon)]=float(p.y[horizon][i,k]) if np.isfinite(p.y[horizon][i,k]) else None
                for m in MODELS:row[m+str(horizon)]=float(predictions[m,horizon][i,k])
            prediction_rows.append(row)
    table=pd.DataFrame(prediction_rows);table.to_csv(out/'predictions.csv.gz',index=False,compression='gzip')
    pred_hash=digest(prediction_rows)
    principal=[x for x in comparisons if x['horizon']==4]
    informative=all(t['positive_lower_bound'] for r in principal for t in r['paired_tests'].values())
    result=dict(id='clock-phase-information-20260906',primary='boundary4',data=audit,comparisons=comparisons,
        monthly_horizon_fits=len(fits),fitted_models=sum(len(x['models']) for x in fits),
        matched_prediction_rows=len(prediction_rows),predictions_sha256=pred_hash,
        informative_in_both_periods_against_both_controls=informative,
        model_parameters=PARAMS,source_sha256={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in Path(__file__).parent.glob('*.py')},
        scope='prediction_only_no_portfolio_replay',ledger_publication_blocked=True,ledger_bypass_attempted=False,
        portfolio_return_pct=None,annual_return_pct=None,portfolio_trades=None,stable_profit_proven=False,
        annual500proven=False,real_orders=0,
        limitations=['One-minute spot phase proxy is not an exact reproduction of the ten-second futures paper.',
          'Historical dates economically reused; a new aggregation is not pristine out-of-time evidence.',
          'Only BTC and ETH, no generalization to an independent full market.',
          'Paired forecast errors and overlapping conditional returns do not establish executable profit.',
          'No cash ledger, capacity-constrained fills or drawdown simulation in this reduced scope.',
          'Model training clips targets only; scoring uses raw matured targets.',
          'Source gaps and short candles remain explicit; no interpolated phases/prices.',
          'Text model export is an audit artifact, not a production deserializer.',
          'Prediction repeatability is not independent economic validation.'])
    save(out/'results.json',result)
    save(out/'verification.json',dict(result_sha256=digest(result),predictions_sha256=pred_hash,
        model_count=result['fitted_models'],comparison_count=len(comparisons),portfolio_run=False))
    pd.DataFrame([dict(period=r['period'],horizon=r['horizon'],observations=r['matched_observations'],
        **r['mse'],improvement_pct=r['error_reduction_vs_base_pct']) for r in comparisons]).to_csv(out/'forecast_comparison.csv',index=False)
    pd.DataFrame([dict(period=r['period'],**x) for r in comparisons for x in r['conditional_observations']]).to_csv(out/'conditional_price_changes.csv',index=False)
    print('VERIFY',(out/'verification.json').read_text(),flush=True)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();study(a.data,a.out)
