"""Monthly, past-only paired ridge models. No parameter search or live orders."""
from dataclasses import dataclass
import hashlib
import math
import numpy as np
import pandas as pd
from research.annual_rotation.data import SYMBOLS
from research.rotation_stability.policy import Policy, risk_scale

HORIZON = 7
LAG = 2
EMBARGO = 7
PRIMARY = 'flow7_weekly'
POLICIES = (PRIMARY, 'price7_weekly', 'stale_flow7_weekly', 'flow7_daily',
            'flow7_bold', 'price7_bold', 'flow7_conviction', 'flow7_btc')


@dataclass
class Features:
    dates: pd.DatetimeIndex
    arrays: dict
    valid: np.ndarray
    labels: np.ndarray
    horizon_vol: np.ndarray
    returns: np.ndarray
    names: dict


def prepare(frames):
    if set(frames) != set(SYMBOLS):
        raise ValueError('Full fixed cohort required')
    idx = frames[SYMBOLS[0]].index
    if str(idx.tz) != 'UTC' or idx.has_duplicates or not idx.is_monotonic_increasing:
        raise ValueError('Unique chronological UTC input required')
    if len(idx) > 1 and not np.all(np.diff(idx.asi8) == 86400000000000):
        raise ValueError('Gaps must be explicit, not dropped rows')
    if any(not d.index.equals(idx) for d in frames.values()):
        raise ValueError('Misaligned daily frames')
    def table(col):
        return pd.DataFrame({s: frames[s][col] for s in SYMBOLS})
    c, o, h, l, q, b, n = [table(k) for k in
        ('close', 'open', 'high', 'low', 'quote_volume', 'buy_quote', 'trades')]
    lr = np.log(c / c.shift())
    vol = lr.rolling(60, min_periods=60).std().replace(0, np.nan)
    hv = vol * math.sqrt(HORIZON)
    price = {}
    for length in (1, 3, 7, 21, 63):
        price[f'return{length}'] = np.log(c / c.shift(length)) / (vol * math.sqrt(length))
    for length in (20, 50):
        price[f'mean_distance{length}'] = np.log(c / c.rolling(length).mean()) / vol
    price['range'] = ((h-l)/c) / vol
    location = ((2*c-h-l)/(h-l).replace(0, np.nan))
    price['close_location'] = location
    price['turnover_surprise'] = np.log(q / q.rolling(20).median().shift().replace(0, np.nan))
    price['trade_count_surprise'] = np.log(n / n.rolling(20).median().shift().replace(0, np.nan))
    for name, series in (
        ('btc_return21', price['return21'].BTCUSDT),
        ('btc_regime', np.log(c.BTCUSDT/c.BTCUSDT.rolling(200).mean()) / vol.BTCUSDT)
    ):
        price[name] = pd.DataFrame(np.repeat(series.to_numpy()[:, None], len(SYMBOLS), axis=1), index=idx, columns=SYMBOLS)
    imbalance = (2*b/q.replace(0, np.nan)-1)
    flow = {'imbalance1': imbalance}
    for length in (3, 7, 21):
        flow[f'imbalance{length}'] = 2*b.rolling(length).sum()/q.rolling(length).sum().replace(0, np.nan)-1
    prior_mean = imbalance.rolling(60).mean().shift()
    prior_var = imbalance.rolling(60).var().shift().replace(0, np.nan)
    z = (imbalance-prior_mean)/np.sqrt(prior_var)
    flow['imbalance_z'] = z
    flow['flow_trend_interaction'] = z*price['return7']
    flow['flow_location_interaction'] = z*location
    slope = lr.rolling(60).cov(imbalance).shift() / prior_var
    expected_response = lr.rolling(60).mean().shift() + slope*(imbalance-prior_mean)
    flow['price_response_residual'] = (lr-expected_response)/vol
    p = np.stack([v.to_numpy(float) for v in price.values()], axis=2)
    f = np.stack([v.to_numpy(float) for v in flow.values()], axis=2)
    stale = np.full_like(f, np.nan)
    stale[63:] = f[:-63]
    ids = np.broadcast_to(np.eye(len(SYMBOLS)), (len(idx),len(SYMBOLS),len(SYMBOLS)))
    arrays = {'price': np.concatenate((p, ids), axis=2),
              'flow': np.concatenate((p, f, ids), axis=2),
              'stale_flow': np.concatenate((p, stale, ids), axis=2)}
    valid = c.notna().rolling(201).sum().eq(201).to_numpy() & q.rolling(30).mean().ge(5e6).to_numpy()
    for a in arrays.values():
        valid &= np.isfinite(a).all(axis=2)
    labels = (np.log(o.shift(-(LAG+HORIZON))/o.shift(-LAG))/hv).to_numpy()
    # A label crossing a price gap is unusable, even if its two endpoints exist.
    support = o.notna().rolling(LAG+HORIZON+1).sum().eq(LAG+HORIZON+1).shift(-(LAG+HORIZON), fill_value=False).to_numpy(bool)
    labels[~support] = np.nan
    names = {k: list(price) + ([] if k=='price' else list(flow)) + ['id_'+s for s in SYMBOLS] for k in arrays}
    return Features(idx, arrays, valid, labels, hv.to_numpy(), c.pct_change(fill_method=None).to_numpy(), names)


def train_indices(index, month):
    """Labels mature at open(t+9), at least seven calendar days before fit."""
    cutoff = month-pd.Timedelta(days=EMBARGO)
    maturity = index + pd.Timedelta(days=LAG+HORIZON)
    return np.flatnonzero((index >= month-pd.Timedelta(days=730)) & (maturity <= cutoff))


def fit_ridge(x, y):
    if x.ndim != 2 or len(x) != len(y) or not len(x):
        raise ValueError('Invalid training dimensions')
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError('Nonfinite training input')
    center = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-8] = 1.
    z = np.clip((x-center)/scale, -5., 5.)
    clipped_y = np.clip(y, -5., 5.)
    intercept = clipped_y.mean()
    coefficient = np.linalg.solve(z.T@z + .1*len(z)*np.eye(z.shape[1]), z.T@(clipped_y-intercept))
    return dict(center=center.tolist(), scale=scale.tolist(), intercept=float(intercept), coefficient=coefficient.tolist())


def predict(model, x):
    z = np.clip((x-np.asarray(model['center']))/np.asarray(model['scale']), -5., 5.)
    return model['intercept'] + z @ np.asarray(model['coefficient'])


def forecasts(features):
    """Matured labels affect later fits only; prediction validity never uses y."""
    outputs = {name: np.full(features.valid.shape, np.nan) for name in features.arrays}
    audits = []
    for month in pd.date_range(pd.Timestamp('2022-01-01', tz='UTC'), features.dates[-1], freq='MS'):
        ti = train_indices(features.dates, month)
        mask = features.valid[ti] & np.isfinite(features.labels[ti])
        n = int(mask.sum()); ndays = int(mask.any(axis=1).sum())
        info = dict(month=str(month.date()), sample_count=n, distinct_signal_days=ndays,
                    cutoff=str((month-pd.Timedelta(days=EMBARGO)).date()), status='insufficient', fits={})
        if n < 1500 or ndays < 365:
            audits.append(info); continue
        signal_days = np.repeat(ti[:,None], len(SYMBOLS), axis=1)[mask]
        info.update(status='fitted', max_signal_day=str(features.dates[signal_days.max()].date()),
                    latest_label_maturity=str((features.dates[signal_days.max()]+pd.Timedelta(days=LAG+HORIZON)).date()),
                    training_rows_sha256=hashlib.sha256(np.argwhere(mask).astype('<i8').tobytes()).hexdigest())
        qi = np.flatnonzero((features.dates>=month)&(features.dates<month+pd.offsets.MonthBegin(1)))
        y = features.labels[ti][mask]
        for name,a in features.arrays.items():
            model = fit_ridge(a[ti][mask], y)
            result = predict(model, a[qi])
            result[~features.valid[qi]] = np.nan
            outputs[name][qi] = result
            info['fits'][name] = model
        audits.append(info)
    return outputs, audits


def targets(features, predictions, name, exclude=None):
    if name not in POLICIES or exclude not in (None,)+SYMBOLS:
        raise ValueError('Outside frozen policy set')
    which = 'price' if name.startswith('price') else ('stale_flow' if name.startswith('stale') else 'flow')
    bold = name.endswith('bold'); btc_only = name.endswith('btc')
    hurdle = .012 if name.endswith('conviction') else .006
    policy = Policy(name,'learning',False,.50 if bold else .20,1. if bold else .60,
                    1/3 if bold else (.60 if btc_only else .20))
    out = np.zeros_like(features.valid, dtype=float)
    for t in range(len(out)):
        score = predictions[which][t]
        gross_log_forecast = score*features.horizon_vol[t]
        possible = [k for k,s in enumerate(SYMBOLS) if s!=exclude and (not btc_only or k==0)
            and features.valid[t,k] and np.isfinite(score[k]) and gross_log_forecast[k]>hurdle]
        chosen = sorted(possible,key=lambda k:(-score[k],SYMBOLS[k]))[:(1 if btc_only else 3)]
        if not chosen or t<60: continue
        sample = features.returns[t-59:t+1]
        if not np.isfinite(sample).all(): continue
        w = np.zeros(len(SYMBOLS)); w[chosen] = 1. if btc_only else 1/3
        out[t],_,_ = risk_scale(w,np.cov(sample,rowvar=False,ddof=1),policy)
    return out
