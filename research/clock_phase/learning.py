"""Causal paired hourly forecasting; no random validation or hyperparameter search.

The minute-spread phase is a hypothesized feature, not identified trader intent.
Models are exported as inspectable text, never loaded from arbitrary pickle.
"""
from dataclasses import dataclass
import gzip
import hashlib
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
import sklearn
from .data import SYMBOLS

MODELS=('base','boundary','placebo')
HORIZONS=(4,12)
PARAMS=dict(max_iter=80,max_leaf_nodes=7,max_depth=3,min_samples_leaf=128,
    learning_rate=.05,l2_regularization=10.,max_bins=63,early_stopping=False,
    random_state=20260906,loss='squared_error')

@dataclass
class Prepared:
    index:pd.DatetimeIndex
    x:dict
    valid:np.ndarray
    y:dict
    volatility:np.ndarray
    names:dict


def prepare(frames):
    if set(frames)!=set(SYMBOLS):raise ValueError('Wrong asset set')
    idx=frames[SYMBOLS[0]].index
    if str(idx.tz)!='UTC' or idx.has_duplicates or not idx.is_monotonic_increasing:
        raise ValueError('Unique sorted UTC hours required')
    if len(idx)>1 and not np.all(np.diff(idx.asi8)==3600000000000):raise ValueError('Explicit gap rows required')
    if any(not d.index.equals(idx) for d in frames.values()):raise ValueError('Inconsistent assets')
    def t(col):return pd.DataFrame({s:frames[s][col] for s in SYMBOLS})
    c,h,l,q,b=t('close'),t('high'),t('low'),t('quote_volume'),t('buy_quote')
    lr=np.log(c/c.shift());vol=lr.rolling(168,min_periods=168).std().replace(0,np.nan)
    base={}
    for n in (1,4,12,24):base['return'+str(n)]=np.log(c/c.shift(n))/(vol*math.sqrt(n))
    base['range']=(h-l)/c/vol
    base['location']=(2*c-h-l)/(h-l).replace(0,np.nan)
    base['mean_distance']=np.log(c/c.rolling(24).mean())/vol
    base['volume_surprise']=np.log(q/q.rolling(168).median().shift().replace(0,np.nan))
    base['log_volatility']=np.log(vol)
    base['whole_imbalance']=2*b/q.replace(0,np.nan)-1
    base['whole_imbalance4']=2*b.rolling(4).sum()/q.rolling(4).sum().replace(0,np.nan)-1
    def broadcast(values):return pd.DataFrame(np.repeat(np.asarray(values)[:,None],len(SYMBOLS),axis=1),index=idx,columns=SYMBOLS)
    for name,values in [('hour_sin',np.sin(2*np.pi*idx.hour/24)),('hour_cos',np.cos(2*np.pi*idx.hour/24)),
                        ('day_sin',np.sin(2*np.pi*idx.dayofweek/7)),('day_cos',np.cos(2*np.pi*idx.dayofweek/7))]:
        base[name]=broadcast(values)
    ordinary=np.stack([d.to_numpy(float) for d in base.values()],axis=2)
    ids=np.broadcast_to(np.eye(len(SYMBOLS)),(len(idx),len(SYMBOLS),len(SYMBOLS)))
    arrays={'base':np.concatenate([ordinary,ids],axis=2)};names={'base':list(base)+list(SYMBOLS)}
    for phase in ('boundary','placebo'):
        pq,pb=t(phase+'_quote'),t(phase+'_buy');oi=2*pb/pq.replace(0,np.nan)-1
        extra={'phase_imbalance':oi,'phase_imbalance4':2*pb.rolling(4).sum()/pq.rolling(4).sum().replace(0,np.nan)-1,
               'phase_turnover_share':pq/q.replace(0,np.nan),'phase_price_interaction':oi*base['location']}
        arrays[phase]=np.concatenate([ordinary,np.stack([d.to_numpy(float) for d in extra.values()],axis=2),ids],axis=2)
        names[phase]=list(base)+list(extra)+list(SYMBOLS)
    valid=t('bar_ok').astype(bool).rolling(193,min_periods=193).sum().eq(193).to_numpy()
    for x in arrays.values():valid &= np.isfinite(x).all(axis=2)
    price=t('price2');labels={}
    for horizon in HORIZONS:
        y=(np.log(price.shift(-(1+horizon))/price.shift(-1))/(vol*math.sqrt(horizon))).to_numpy()
        support=(t('bar_ok').astype(bool)&price.notna()).rolling(horizon+1,min_periods=horizon+1).sum().eq(horizon+1).shift(-(horizon+1),fill_value=False)
        y[~support.to_numpy(bool)]=np.nan;labels[horizon]=y
    return Prepared(idx,arrays,valid,labels,vol.to_numpy(float),names)


def training_rows(p,month,horizon):
    maturity=p.index+pd.Timedelta(hours=1+horizon,minutes=2)
    cutoff=month-pd.Timedelta(hours=24)
    return np.flatnonzero((p.index>=month-pd.Timedelta(days=180))&(maturity<=cutoff))


def model_text(model):
    return dict(parameters=PARAMS,sklearn=sklearn.__version__,
        baseline=model._baseline_prediction.tolist(),
        bin_thresholds=[v.tolist() for v in model._bin_mapper.bin_thresholds_],
        tree_node_dtype=model._predictors[0][0].nodes.dtype.descr,
        tree_nodes=[[tree.nodes.tolist() for tree in iteration] for iteration in model._predictors],
        purpose='audit_only_recompute_from_data_not_untrusted_model_loading')


def forecasts(p,out=None,end_month=None):
    result={(model,h):np.full(p.valid.shape,np.nan) for model in MODELS for h in HORIZONS};audit=[]
    end=p.index[-1] if end_month is None else min(end_month,p.index[-1])
    month_starts=pd.date_range(pd.Timestamp('2023-01-01',tz='UTC'),end,freq='MS')
    if out is not None:Path(out).mkdir(parents=True,exist_ok=True)
    for month in month_starts:
        qi=np.flatnonzero((p.index>=month)&(p.index<month+pd.offsets.MonthBegin(1)))
        if not len(qi):continue
        for horizon in HORIZONS:
            ti=training_rows(p,month,horizon);mask=p.valid[ti]&np.isfinite(p.y[horizon][ti])
            rowcount=int(mask.sum());valid_dates=p.index[ti][mask.any(axis=1)]
            days=len(np.unique(valid_dates.floor('D')))
            entry=dict(month=str(month.date()),horizon=horizon,sample_count=rowcount,distinct_days=days,
                cutoff=str(month-pd.Timedelta(hours=24)),fitted=False,models={})
            if days<90 or rowcount<2000:audit.append(entry);continue
            y=np.clip(p.y[horizon][ti][mask],-5,5)
            absolute_rows=np.column_stack([np.repeat(ti[:,None],len(SYMBOLS),axis=1)[mask],np.broadcast_to(np.arange(len(SYMBOLS)),mask.shape)[mask]])
            entry.update(fitted=True,training_rows_sha256=hashlib.sha256(absolute_rows.astype('<i8').tobytes()).hexdigest(),
                latest_label_maturity=str(valid_dates.max()+pd.Timedelta(hours=1+horizon,minutes=2)))
            for name,x in p.x.items():
                model=HistGradientBoostingRegressor(**PARAMS)
                model.fit(x[ti][mask],y)
                valid_q=p.valid[qi];pred=np.full(valid_q.shape,np.nan)
                if valid_q.any():pred[valid_q]=model.predict(x[qi][valid_q])
                result[name,horizon][qi]=pred
                raw=json.dumps(model_text(model),sort_keys=True,separators=(',',':'),allow_nan=False).encode()
                entry['models'][name]=hashlib.sha256(raw).hexdigest()
                if out is not None:
                    path=Path(out)/f'{month:%Y-%m}_{horizon}_{name}.json.gz'
                    path.write_bytes(gzip.compress(raw,mtime=0))
            audit.append(entry)
        print('MONTH_FIT',str(month.date()),flush=True)
    return result,audit
