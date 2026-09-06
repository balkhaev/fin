"""Fixed public spot archive acquisition. No credentials or exchange orders."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import datetime as dt
import hashlib
import io
import json
import time
import urllib.request
import zipfile
import numpy as np
import pandas as pd

SYMBOLS=('BTCUSDT','ETHUSDT','BNBUSDT','XRPUSDT','ADAUSDT','LTCUSDT','BCHUSDT','LINKUSDT','DOGEUSDT')
START='2020-01-01'
END='2026-09-01'
COLS=['time','open','high','low','close','volume','close_time','quote_volume','trades','buy_volume','buy_quote','ignore']


def months():
    return [str(x.date())[:7] for x in pd.date_range(START,END,freq='MS',inclusive='left')]


def days(month):
    begin=pd.Timestamp(month+'-01')
    return [str(x.date()) for x in pd.date_range(begin,begin+pd.offsets.MonthBegin(1),freq='D',inclusive='left')]


def get(url):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url,timeout=45) as response:
                raw=response.read(8*1024*1024+1)
                if len(raw)>8*1024*1024:raise ValueError('Archive byte budget exceeded')
                return raw
        except OSError as error:
            if getattr(error,'code',None) in (401,403,404,451) or attempt==2:raise
            time.sleep(attempt+1)


def download(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=False)
    def archive(symbol,period,frequency):
        name=f'{symbol}-1d-{period}.zip'
        url=f'https://data.binance.vision/data/spot/{frequency}/klines/{symbol}/1d/{name}'
        checksum=get(url+'.CHECKSUM').decode().split()[0].lower()
        raw=get(url);digest=hashlib.sha256(raw).hexdigest()
        if checksum!=digest:raise ValueError('Published checksum mismatch')
        (root/name).write_bytes(raw)
        return dict(filename=name,url=url,sha256=digest,bytes=len(raw),status='verified')
    def one(task):
        symbol,month=task;row=dict(symbol=symbol,month=month)
        try:
            try:row.update(archive(symbol,month,'monthly'),frequency='monthly')
            except OSError as error:
                if getattr(error,'code',None)!=404:raise
                row['monthly_error']=str(error)
                row.update(frequency='daily',parts=[archive(symbol,d,'daily') for d in days(month)],status='verified')
        except Exception as error:row.update(status='unavailable',error=str(error))
        return row
    tasks=[(s,m) for s in SYMBOLS for m in months()]
    with ThreadPoolExecutor(max_workers=8) as pool:rows=list(pool.map(one,tasks))
    result=dict(schema='annual-rotation-data-v1',start=START,end_exclusive=END,symbols=list(SYMBOLS),
        acquired_at=dt.datetime.now(dt.timezone.utc).isoformat(),files=rows,purchases=False,live_orders=False)
    (root/'manifest.json').write_text(json.dumps(result,indent=2))
    failures=[r for r in rows if r['status']!='verified']
    print('DATA_ACQUISITION',json.dumps(dict(month_identities=len(rows),failures=failures)),flush=True)
    if failures:raise RuntimeError('Incomplete source set; failures retained without substitution')
    return result


def normalize_time(values):
    a=np.asarray(values,dtype=np.int64);micro=a>10**14
    if micro.any() and not micro.all():raise ValueError('Mixed timestamp units')
    if len(a) and micro.all():
        if np.any(a%1000):raise ValueError('Sub-millisecond open timestamp')
        a=a//1000
    return a


def load(root):
    root=Path(root);manifest=json.loads((root/'manifest.json').read_text());rows=manifest['files']
    expected={(s,m) for s in SYMBOLS for m in months()}
    if len(rows)!=len(expected) or {(r['symbol'],r['month']) for r in rows}!=expected:
        raise ValueError('Missing or duplicate symbol/month identity')
    groups={s:[] for s in SYMBOLS};source_count=0;excluded=[]
    for row in rows:
        s,m=row['symbol'],row['month']
        if row['status']!='verified':raise ValueError('Unverified month')
        daily=row.get('frequency')=='daily'
        parts=row['parts'] if daily else [row]
        names={f'{s}-1d-{d}.zip' for d in days(m)} if daily else {f'{s}-1d-{m}.zip'}
        if len(parts)!=len(names) or {r['filename'] for r in parts}!=names:raise ValueError('Incomplete same-month daily fallback')
        for r in parts:
            raw=(root/r['filename']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=r['sha256'] or len(raw)!=r['bytes']:raise ValueError('Archive SHA/size mismatch')
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                if len(z.namelist())!=1:raise ValueError('Expected one CSV')
                with z.open(z.namelist()[0]) as f:d=pd.read_csv(f,names=COLS,header=None,dtype=str)
            bad=pd.to_numeric(d.time,errors='coerce').isna()
            if bad.any():
                if bad.sum()!=1 or not bad.iloc[0]:raise ValueError('Invalid timestamp row')
                d=d.iloc[1:]
            d=d.apply(pd.to_numeric,errors='raise');d['time']=normalize_time(d.time)
            begin=pd.Timestamp(m+'-01',tz='UTC');end=begin+pd.offsets.MonthBegin(1)
            if not ((d.time>=begin.timestamp()*1000)&(d.time<end.timestamp()*1000)).all():raise ValueError('Wrong archive date range')
            prices=d[['open','high','low','close']]
            if not np.isfinite(prices.to_numpy()).all() or (prices<=0).any().any():raise ValueError('Invalid price')
            if ((d.high<prices.max(axis=1))|(d.low>prices.min(axis=1))).any():raise ValueError('Inconsistent OHLC')
            if not np.isfinite(d[['volume','quote_volume']].to_numpy()).all() or (d[['volume','quote_volume']]<0).any().any():raise ValueError('Invalid volume')
            off=d.time%86400000!=0
            excluded.extend(dict(symbol=s,archive=r['filename'],time=int(v)) for v in d.loc[off,'time'])
            groups[s].append(d.loc[~off,['time','open','high','low','close','volume','quote_volume']]);source_count+=1
    idx=pd.date_range(START,END,freq='D',inclusive='left',tz='UTC');frames={};audits={}
    for s in SYMBOLS:
        d=pd.concat(groups[s],ignore_index=True).sort_values('time')
        if d.time.duplicated().any():raise ValueError('Duplicate daily price')
        d.index=pd.to_datetime(d.pop('time'),unit='ms',utc=True);d=d.reindex(idx)
        frames[s]=d
        audits[s]=dict(observed_days=int(d.close.notna().sum()),missing_days=[str(t.date()) for t in idx[d.close.isna()]])
    audit=dict(start=START,end_exclusive=END,days=len(idx),verified_archives=source_count,
        per_asset=audits,off_grid_excluded=excluded,no_price_forward_fill=True,
        fixed_cohort_not_survivor_bias_free=True,
        manifest_sha256=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest())
    return frames,audit


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    download(p.parse_args().out)
