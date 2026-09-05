"""Public spot data only. No credentials, downloads outside the fixed manifest, or orders."""
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import time
import urllib.request
import zipfile
import numpy as np
import pandas as pd

START='2018-01-01'
END='2026-09-01'
COLS=['time','open','high','low','close','volume','end','quote_volume','count','buy_volume','buy_quote','ignore']


def periods():
    return [str(x.date())[:7] for x in pd.date_range(START,END,freq='MS',inclusive='left')]


def get(url):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url,timeout=45) as r:
                data=r.read(16*1024*1024+1)
                if len(data)>16*1024*1024:raise ValueError('File budget exceeded')
                return data
        except OSError as e:
            if getattr(e,'code',None) in (401,403,404,451) or attempt==2:raise
            time.sleep(attempt+1)


def download(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=False)
    def one(month):
        name=f'BTCUSDT-1h-{month}.zip'
        url=f'https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1h/{name}'
        r=dict(month=month,filename=name,url=url)
        try:
            expected=get(url+'.CHECKSUM').decode().split()[0].lower()
            raw=get(url);h=hashlib.sha256(raw).hexdigest()
            if h!=expected:raise ValueError('Published checksum mismatch')
            (root/name).write_bytes(raw)
            r.update(sha256=h,bytes=len(raw),status='verified')
        except Exception as e:r.update(status='unavailable',error=str(e))
        return r
    with ThreadPoolExecutor(max_workers=4) as p:rows=list(p.map(one,periods()))
    manifest=dict(schema='btc-spot-hourly-v1',start=START,end_exclusive=END,files=rows,
                  acquired_utc=dt.datetime.now(dt.timezone.utc).isoformat(),orders_sent=0)
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2))
    if any(r['status']!='verified' for r in rows):raise RuntimeError('Incomplete acquisition; see retained manifest')
    return manifest


def timestamps(values):
    a=np.asarray(values,dtype=np.int64);us=a>10**14
    if us.any() and not us.all():raise ValueError('Mixed time units')
    if us.all() and len(a):
        if np.any(a%1000):raise ValueError('Nonintegral millisecond timestamps')
        a=a//1000
    if np.any(a%3600000):raise ValueError('Non-hour open time')
    return a


def load(root):
    root=Path(root);m=json.loads((root/'manifest.json').read_text());rows=m['files']
    if len(rows)!=len(periods()) or {r['month'] for r in rows}!=set(periods()):raise ValueError('Manifest identity mismatch')
    parts=[]
    for r in rows:
        name=f"BTCUSDT-1h-{r['month']}.zip";raw=(root/name).read_bytes()
        if r['status']!='verified' or hashlib.sha256(raw).hexdigest()!=r['sha256']:raise ValueError('Archive hash mismatch')
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            if len(z.namelist())!=1:raise ValueError('One CSV expected')
            with z.open(z.namelist()[0]) as stream:d=pd.read_csv(stream,names=COLS,header=None,dtype=str)
        bad=pd.to_numeric(d.time,errors='coerce').isna()
        if bad.any():
            if bad.sum()!=1 or not bad.iloc[0]:raise ValueError('Malformed timestamp row')
            d=d.iloc[1:]
        d=d.apply(pd.to_numeric,errors='raise');d['time']=timestamps(d.time)
        begin=pd.Timestamp(r['month']+'-01',tz='UTC');end=begin+pd.offsets.MonthBegin(1)
        if not ((d.time>=begin.timestamp()*1000)&(d.time<end.timestamp()*1000)).all():raise ValueError('Wrong archive month')
        needed=d[['open','high','low','close','volume']].to_numpy(float)
        if not np.isfinite(needed).all() or (needed[:,:4]<=0).any() or (needed[:,4]<0).any():raise ValueError('Nonfinite prices or volume')
        if ((d.high<d[['open','close','low']].max(axis=1))|(d.low>d[['open','close','high']].min(axis=1))).any():raise ValueError('Inconsistent OHLC')
        parts.append(d[['time','open','high','low','close','volume']])
    data=pd.concat(parts).sort_values('time')
    if data.time.duplicated().any():raise ValueError('Duplicate hourly bar')
    data.index=pd.to_datetime(data.pop('time'),unit='ms',utc=True)
    idx=pd.date_range(START,END,freq='h',inclusive='left',tz='UTC')
    data=data.reindex(idx);data['observed']=data.close.notna()
    missing=[str(t) for t in idx[~data.observed]]
    audit=dict(verified_files=len(rows),hours=len(idx),observed_hours=int(data.observed.sum()),
       missing_hours=len(missing),missing_open_times=missing,price_forward_filled=False,
       start=START,end_exclusive=END,manifest_sha256=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest())
    return data,audit
