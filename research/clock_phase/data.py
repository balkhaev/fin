"""Public spot minute archives -> completed-hour features, never order submission.

Raw ZIPs are retained. Hourly feature values require all60 minute observations.
Execution references are independent minute2/17 OPENs, not interpolated prices.
Short source candles remain timestamped but are masked and audited, not rounded.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import argparse
import datetime as dt
import hashlib
import io
import json
import time
import urllib.error
import urllib.request
import zipfile
import numpy as np
import pandas as pd

SYMBOLS=('BTCUSDT','ETHUSDT')
START='2022-01-01'
END='2026-09-01'
COLS=['time','open','high','low','close','volume','close_time','quote_volume','trades','buy_volume','buy_quote','ignore']
FEATURE_COLS=['open','high','low','close','volume','quote_volume','trades','buy_quote',
              'boundary_quote','boundary_buy','placebo_quote','placebo_buy']


def months():return [str(x.date())[:7] for x in pd.date_range(START,END,freq='MS',inclusive='left')]


def dates(month):
    t=pd.Timestamp(month+'-01')
    return [str(x.date()) for x in pd.date_range(t,t+pd.offsets.MonthBegin(1),freq='D',inclusive='left')]


def request(url):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url,timeout=40) as response:
                raw=response.read(16*1024*1024+1)
                if len(raw)>16*1024*1024:raise ValueError('Archive exceeds byte budget')
                return raw
        except urllib.error.HTTPError as e:
            if e.code not in (429,500,502,503,504) or attempt==2:raise
            time.sleep(attempt+1)


def download(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=False)
    def archive(symbol,period,frequency):
        name=f'{symbol}-1m-{period}.zip'
        url=f'https://data.binance.vision/data/spot/{frequency}/klines/{symbol}/1m/{name}'
        checksum=request(url+'.CHECKSUM').decode().split()[0].lower()
        raw=request(url);h=hashlib.sha256(raw).hexdigest()
        if h!=checksum:raise ValueError('Published checksum mismatch')
        (root/name).write_bytes(raw)
        return dict(filename=name,url=url,sha256=h,bytes=len(raw))
    def one(task):
        symbol,month=task;r=dict(symbol=symbol,month=month)
        try:
            try:r.update(frequency='monthly',parts=[archive(symbol,month,'monthly')])
            except urllib.error.HTTPError as e:
                if e.code!=404:raise
                r.update(monthly_error=str(e),frequency='daily',parts=[archive(symbol,d,'daily') for d in dates(month)])
            r['status']='complete'
        except (OSError,ValueError) as e:r.update(status='unavailable',error=str(e))
        return r
    with ThreadPoolExecutor(max_workers=4) as executor:
        records=list(executor.map(one,[(s,m) for s in SYMBOLS for m in months()]))
    manifest=dict(schema='clock-phase-minute-v1',start=START,end_exclusive=END,
        acquired_utc=dt.datetime.now(dt.timezone.utc).isoformat(),sources=records)
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2))
    bad=[x for x in records if x['status']!='complete']
    print('ACQUISITION',json.dumps(dict(symbol_months=len(records),failures=bad)),flush=True)
    if bad:raise RuntimeError('Incomplete source set; no data or date substitution')
    return manifest


def parse(raw,mask_partial=False):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if len(z.namelist())!=1:raise ValueError('One CSV per ZIP required')
        with z.open(z.namelist()[0]) as f:d=pd.read_csv(f,names=COLS,header=None,dtype=str)
    bad=pd.to_numeric(d.time,errors='coerce').isna()
    if bad.any():
        if bad.sum()!=1 or not bad.iloc[0]:raise ValueError('Malformed timestamp row')
        d=d.iloc[1:]
    d=d.apply(pd.to_numeric,errors='raise')
    ts=d.time.to_numpy(np.int64);micro=ts>10**14
    if micro.any() and not micro.all():raise ValueError('Mixed timestamp units')
    if micro.all() and len(ts):
        if (ts%1000).any():raise ValueError('Submillisecond bar OPEN')
        ts=ts//1000;d['close_time']=d.close_time.astype(np.int64)//1000
    if (ts%60000).any() or len(np.unique(ts))!=len(ts):raise ValueError('Off-minute or duplicate OPEN')
    complete=d.close_time.to_numpy(np.int64)==ts+59999
    duration=d.close_time.to_numpy(np.int64)-ts
    if ((duration<0)|(duration>59999)).any():raise ValueError('Invalid candle duration')
    if not complete.all() and not mask_partial:raise ValueError('Incomplete or wrong candle duration')
    d['time']=ts
    p=d[['open','high','low','close']]
    if not np.isfinite(p.to_numpy()).all() or (p<=0).any().any():raise ValueError('Invalid OHLC')
    if ((d.high<p.max(axis=1))|(d.low>p.min(axis=1))).any():raise ValueError('Crossed OHLC')
    v=d[['volume','quote_volume','trades','buy_volume','buy_quote']]
    if not np.isfinite(v.to_numpy()).all() or (v<0).any().any():raise ValueError('Invalid executed volume')
    if (d.trades!=np.floor(d.trades)).any():raise ValueError('Noninteger trade count')
    for buy,total in [('buy_volume','volume'),('buy_quote','quote_volume')]:
        if (d[buy]>d[total]+1e-8*np.maximum(1,d[total])).any():raise ValueError('Aggressor volume exceeds total')
    partial=[dict(open_ms=int(t),duration_ms=int(v)) for t,v in zip(ts[~complete],duration[~complete])]
    d.loc[~complete,['open','high','low','close','volume','quote_volume','trades','buy_volume','buy_quote']]=np.nan
    d=d.sort_values('time');d.index=pd.to_datetime(d.pop('time'),unit='ms',utc=True)
    d.attrs['partial_candles']=partial
    return d


def hourly(minutes):
    if minutes.index.has_duplicates or not minutes.index.is_monotonic_increasing:raise ValueError('Bad minute ordering')
    r=minutes.resample('h').agg({'open':'first','high':'max','low':'min','close':'last',
        'volume':'sum','quote_volume':'sum','trades':'sum','buy_quote':'sum'})
    counts=minutes.close.resample('h').count()
    for prefix,phase in [('boundary',0),('placebo',7)]:
        subset=minutes.loc[minutes.index.minute%15==phase]
        for source,suffix in [('quote_volume','quote'),('buy_quote','buy')]:
            r[prefix+'_'+suffix]=subset[source].resample('h').sum().reindex(r.index)
    for offset in (2,17):
        prices=minutes.loc[minutes.index.minute==offset,'open'].copy();prices.index=prices.index.floor('h')
        capacity=minutes.loc[minutes.index.minute==offset-1,'volume'].copy();capacity.index=capacity.index.floor('h')
        r['price'+str(offset)]=prices.reindex(r.index);r['volume'+str(offset)]=capacity.reindex(r.index)
    r['bar_ok']=counts.eq(60);r['minute_count']=counts
    r.loc[~r.bar_ok,FEATURE_COLS]=np.nan
    return r


def aggregate(root,out):
    root=Path(root);out=Path(out)
    if out.exists():raise FileExistsError('Fresh aggregate directory required')
    raw_manifest=(root/'manifest.json').read_bytes();manifest=json.loads(raw_manifest)
    rows=manifest['sources'];expect={(s,m) for s in SYMBOLS for m in months()}
    if len(rows)!=len(expect) or {(r['symbol'],r['month']) for r in rows}!=expect:raise ValueError('Changed cohort or months')
    groups={s:[] for s in SYMBOLS};raw_count={s:0 for s in SYMBOLS};zip_count=0;partial=[]
    for record in rows:
        s,m=record['symbol'],record['month']
        if record['status']!='complete':raise ValueError('Unavailable month')
        names={f'{s}-1m-{x}.zip' for x in (dates(m) if record['frequency']=='daily' else [m])}
        if len(record['parts'])!=len(names) or {x['filename'] for x in record['parts']}!=names:raise ValueError('Incomplete month fallback')
        chunks=[];begin=pd.Timestamp(m+'-01',tz='UTC');end=begin+pd.offsets.MonthBegin(1)
        for part in record['parts']:
            raw=(root/part['filename']).read_bytes()
            if len(raw)!=part['bytes'] or hashlib.sha256(raw).hexdigest()!=part['sha256']:raise ValueError('Raw archive changed')
            d=parse(raw,mask_partial=True)
            partial.extend(dict(symbol=s,file=part['filename'],**x) for x in d.attrs['partial_candles'])
            if not ((d.index>=begin)&(d.index<end)).all():raise ValueError('Wrong archive month')
            d.attrs={};chunks.append(d);zip_count+=1
        d=pd.concat(chunks).sort_index();raw_count[s]+=len(d);groups[s].append(hourly(d))
    out.mkdir(parents=True);expected=pd.date_range(START,END,freq='h',inclusive='left',tz='UTC');files=[];audit={}
    for s in SYMBOLS:
        h=pd.concat(groups[s]).sort_index()
        if h.index.has_duplicates:raise ValueError('Duplicate aggregated hour')
        h=h.reindex(expected);h['bar_ok']=h.bar_ok.fillna(False).astype(bool)
        name=s+'.csv';h.to_csv(out/name,index_label='hour_open')
        audit[s]=dict(raw_minutes=raw_count[s],hours=len(h),incomplete_hours=int((~h.bar_ok).sum()),
            missing_price2=int(h.price2.isna().sum()),first_incomplete=[str(t) for t in h.index[~h.bar_ok][:12]])
        files.append(dict(symbol=s,filename=name,sha256=hashlib.sha256((out/name).read_bytes()).hexdigest()))
    result=dict(raw_manifest_sha256=hashlib.sha256(raw_manifest).hexdigest(),zip_files=zip_count,assets=audit,
        files=files,source_interval='1m',feature_interval='1h',no_price_fill=True,partial_candles=partial,
        phase_minutes=[0,15,30,45],placebo_minutes=[7,22,37,52])
    (out/'audit.json').write_text(json.dumps(result,indent=2));print('AGGREGATE',json.dumps(result),flush=True)
    return result


def load(root):
    root=Path(root);audit=json.loads((root/'audit.json').read_text());frames={}
    for f in audit['files']:
        if f['symbol'] not in SYMBOLS or f['filename']!=f['symbol']+'.csv':raise ValueError('Changed normalized path')
        raw=(root/f['filename']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=f['sha256']:raise ValueError('Aggregate hash mismatch')
        d=pd.read_csv(io.BytesIO(raw),index_col='hour_open',parse_dates=True)
        d.index=pd.to_datetime(d.index,utc=True);d.bar_ok=d.bar_ok.astype(bool);frames[f['symbol']]=d
    if set(frames)!=set(SYMBOLS):raise ValueError('Incomplete normalized universe')
    return frames,audit


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--raw',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--download',action='store_true');a=p.parse_args()
    if a.download:download(a.raw)
    aggregate(a.raw,a.out)
