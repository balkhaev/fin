"""Pure historical target functions. No broker, credentials, or order submission.

Budgets constrain targets at weekly rebalance, not drifted holdings or gap losses.
"""
from dataclasses import dataclass
import math
import numpy as np
import pandas as pd
from research.annual_rotation.data import SYMBOLS
from research.annual_rotation.model import Config, feature_bank, weights

PRIMARY='guarded_ensemble20'
SELECTABLE=(PRIMARY,'guarded_ensemble10','guarded_raw12620','guarded_risk6320','guarded_btc20')
WEEKLY=Config('raw',126,3,7)

@dataclass(frozen=True)
class Policy:
    name:str
    source:str
    gate:bool=False
    volatility:float=0.
    gross:float=1.
    asset_cap:float=1.

POLICIES=(
    Policy('legacy_raw126','raw126'),Policy('legacy_risk63','risk63'),
    Policy('ensemble_unscaled','ensemble'),Policy('ensemble_market_gate','ensemble',True),
    Policy('ensemble_vol20','ensemble',False,.20),
    Policy(PRIMARY,'ensemble',True,.20,.60,.20),
    Policy('guarded_ensemble10','ensemble',True,.10,.60,.20),
    Policy('guarded_ensemble30','ensemble',True,.30,.60,.20),
    Policy('guarded_raw12620','raw126',True,.20,.60,.20),
    Policy('guarded_risk6320','risk63',True,.20,.60,.20),
    Policy('guarded_btc20','btc',True,.20,.60,.20),
)


def risk_scale(w,covariance,policy):
    """Estimate covariance and stressed-correlation risk using ONLY past returns."""
    w=np.asarray(w,float)
    if np.any(~np.isfinite(w)) or np.any(w<0) or w.sum()>1+1e-10:
        raise ValueError('Invalid fully funded target')
    if not w.any():return w.copy(),0.,0.
    if covariance.shape!=(len(w),len(w)) or not np.isfinite(covariance).all():
        return np.zeros_like(w),0.,0.
    diagonal=np.sqrt(np.maximum(np.diag(covariance),0.))*math.sqrt(365.25)
    estimate=max(math.sqrt(max(float(w@covariance@w),0.)*365.25),float(.7*np.dot(w,diagonal)))
    factor=min(1.,policy.gross/w.sum(),policy.asset_cap/w.max())
    if policy.volatility:
        if estimate<=0:return np.zeros_like(w),0.,0.
        factor=min(factor,policy.volatility/estimate)
    return w*factor,estimate,estimate*factor


def build(frames,exclude=None):
    if exclude is not None and exclude not in SYMBOLS:raise ValueError('Unknown excluded asset')
    idx=frames[SYMBOLS[0]].index
    if any(not frames[s].index.equals(idx) for s in SYMBOLS):raise ValueError('Misaligned source')
    bank=feature_bank(frames)
    cfgs=(Config('raw',63,3,7),Config('raw',126,3,7),
          Config('risk_adjusted',63,3,7),Config('risk_adjusted',126,3,7))
    vectors=[weights(bank,c,exclude) for c in cfgs]
    base={'raw126':vectors[1],'risk63':vectors[2],
          'ensemble':sum(vectors)/len(vectors)}
    btc_bank={k:v.copy() for k,v in bank.items()}
    for value in btc_bank.values():value[:,1:]=np.nan
    base['btc']=weights(btc_bank,Config('risk_adjusted',63,1,7),exclude)
    available=[s for s in SYMBOLS if s!=exclude]
    columns=[SYMBOLS.index(s) for s in available]
    close=pd.DataFrame({s:frames[s].close for s in SYMBOLS})
    returns=close.pct_change(fill_method=None).to_numpy(float)
    trend=(close>close.rolling(100,min_periods=100).mean())&(close>close.shift(63))
    breadth=trend[available].sum(axis=1).to_numpy()/len(available)
    # Excluding BTC removes both its allocation and its market-gate vote.
    btc_gate=(close.BTCUSDT>close.BTCUSDT.rolling(200,min_periods=200).mean())&(close.BTCUSDT>close.BTCUSDT.shift(63))
    if exclude=='BTCUSDT':btc_gate=pd.Series(True,index=idx)
    complete=close[available].notna().all(axis=1).rolling(201,min_periods=201).sum().eq(201)
    market=(btc_gate&complete).to_numpy()&(breadth>=.5)
    covariances=[]
    for i in range(len(idx)):
        cov=np.zeros((len(SYMBOLS),len(SYMBOLS)))
        observations=returns[max(0,i-59):i+1,columns]
        if i<60 or not np.isfinite(observations).all():cov[:]=np.nan
        else:cov[np.ix_(columns,columns)]=np.cov(observations,rowvar=False,ddof=1)
        covariances.append(cov)
    targets={};diagnostics={}
    for policy in POLICIES:
        target=base[policy.source].copy();trace=[]
        for i in range(len(idx)):
            allowed=not policy.gate or bool(market[i])
            before=0.;after=0.
            if not allowed:target[i]=0.
            elif policy.volatility:
                target[i],before,after=risk_scale(target[i],covariances[i],policy)
            trace.append(dict(signal_date=str(idx[i].date()),market_allowed=allowed,
                breadth=float(breadth[i]),target_gross=float(target[i].sum()),
                maximum_asset_target=float(target[i].max()),forecast_vol_before=before,forecast_vol_after=after))
        if (target<0).any() or (target.sum(axis=1)>1+1e-10).any():raise AssertionError('Leverage in target')
        targets[policy.name]=target;diagnostics[policy.name]=pd.DataFrame(trace)
    return targets,diagnostics


def freeze_after_first_drawdown(target,curve,start_index,threshold=.04,initial=10000.):
    """Permanent historical control, observable close first, normal latency next.

    It never fabricates an intraday fill. The original weekly executor still owns
    trading. The pre-halt path is identical to the baseline that supplies the first
    crossing; later observations cannot move an earlier crossing.
    """
    if not 0<threshold<1:raise ValueError('Invalid drawdown threshold')
    values=curve.equity.to_numpy(float)
    if not np.isfinite(values).all():raise ValueError('No drawdown conclusion from unpriced equity')
    high=np.maximum.accumulate(np.r_[initial,values])[1:]
    crossings=np.flatnonzero(values/high-1<=-threshold)
    result=target.copy()
    if not len(crossings):return result,None
    offset=int(crossings[0]);result[start_index+offset:]=0.
    return result,dict(first_observed_close=curve.time.iloc[offset],
                      signal_row=start_index+offset,latency_and_weekly_execution_still_apply=True)
