"""Read only the fixed public archive names; no network, extraction or trading."""
import hashlib
import json
import zipfile
import numpy as np
import pandas as pd
from .download import START,END,tasks

COLS=['time','open','high','low','close','volume','close_time','quote_volume','count','buy_volume','buy_quote','ignore']


def normalize_time(x):
    a=np.asarray(x,dtype=np.int64)
    micro=a>10**14
    if len(a) and micro.any() and not micro.all():raise ValueError('Mixed timestamp units')
    if len(a) and micro.all():a=a//1000
    if np.any(a%60000):raise ValueError('Non-minute timestamp')
    return a


def bars(path):
    with zipfile.ZipFile(path) as z:
        if len(z.namelist())!=1:raise ValueError('Expected one CSV')
        with z.open(z.namelist()[0]) as f:d=pd.read_csv(f,header=None,names=COLS,dtype=str)
    bad=pd.to_numeric(d.time,errors='coerce').isna()
    if bad.any():
        if not (bad.iloc[0] and bad.sum()==1):raise ValueError('Invalid timestamp row')
        d=d.iloc[1:]
    d=d.apply(pd.to_numeric,errors='raise');d['time']=normalize_time(d.time)
    p=d[['open','high','low','close']]
    if not np.isfinite(p.to_numpy()).all() or (p<=0).any().any():raise ValueError('Invalid prices')
    if ((d.high<p.max(axis=1))|(d.low>p.min(axis=1))).any():raise ValueError('Invalid OHLC')
    return d[['time','open','high','low','close','volume','quote_volume','buy_quote']]


def load(root):
    m=json.loads((root/'manifest.json').read_text())
    rows=m['files'];expected={(k,p) for k,p,u in tasks()}
    if len(rows)!=220 or {(r['kind'],r['period']) for r in rows}!=expected:raise ValueError('Incomplete manifest')
    parts={k:[] for k in ('spot','perp','mark','funding')}
    for r in rows:
        # Manifest cannot choose a path: kind and period were whitelisted above.
        path=root/(r['kind']+'-'+r['period']+'.zip')
        if r['status']!='verified' or hashlib.sha256(path.read_bytes()).hexdigest()!=r['sha256']:raise ValueError('Source integrity failure')
        if r['kind']=='funding':
            with zipfile.ZipFile(path) as z:
                with z.open(z.namelist()[0]) as f:d=pd.read_csv(f)
        else:d=bars(path)
        parts[r['kind']].append(d)
    idx=np.arange(START,END,60000,dtype=np.int64);series={};audit={}
    for k in ('spot','perp','mark'):
        d=pd.concat(parts.pop(k),ignore_index=True).sort_values('time')
        if d.time.duplicated().any():raise ValueError('Duplicate minute')
        if not ((d.time>=START)&(d.time<END)).all():raise ValueError('Wrong time range')
        if k=='perp' and not np.array_equal(d.time.to_numpy(),idx):raise ValueError('Futures minute gap')
        d=d.set_index('time').reindex(idx)
        valid=d.close.notna()
        if k!='mark':
            v=d.loc[valid,['volume','quote_volume','buy_quote']]
            if not np.isfinite(v.to_numpy()).all() or (v<0).any().any() or (v.buy_quote>v.quote_volume+1e-5).any():raise ValueError('Invalid taker volume')
        audit[k]={'observed_minutes':int(valid.sum()),'missing_minutes':int((~valid).sum())}
        series[k]=d
    f=pd.concat(parts['funding'],ignore_index=True).sort_values('calc_time')
    if not np.isfinite(f.to_numpy(float)).all() or f.calc_time.duplicated().any():raise ValueError('Invalid funding history')
    if not f.funding_interval_hours.eq(8).all() or ((f.calc_time%60000)>5000).any():raise ValueError('BTC funding schedule requires review')
    ft=f.calc_time.to_numpy(np.int64)//60000*60000
    if not np.array_equal(ft,np.arange(START,END,28800000)):raise ValueError('Incomplete realized funding')
    d=series['perp'].copy();d['time']=idx
    for col in ('open','high','low','close','volume','quote_volume','buy_quote'):d['spot_'+col]=series['spot'][col]
    n=len(d);rate=np.zeros(n);event=np.zeros(n,bool);fi=((ft-START)//60000).astype(int)
    rate[fi]=f.last_funding_rate.to_numpy();event[fi]=True
    d['funding_rate']=rate;d['funding_event']=event
    for name,col in [('funding_mark','open'),('funding_high','high'),('funding_low','low')]:d[name]=series['mark'][col]
    d.index=pd.to_datetime(idx,unit='ms',utc=True)+pd.Timedelta(minutes=1)
    audit.update(minutes=n,verified_archives=220,start='2022-01-01',end_exclusive='2026-08-01',funding_events=len(f),
       funding_missing_marks=int(d.iloc[fi].funding_mark.isna().sum()),actual_funding_rates=True,exact_settlement_marks=False,
       funding_price_method='Minute mark-price OPEN proxy; adverse minute-range sensitivity is separate and non-causal.',
       exact_funding_api=m['exact_funding'],no_forward_fill=True,
       manifest_sha256=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest())
    return d,audit
