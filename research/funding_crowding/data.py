"""Read-only derivatives information for a SPOT experiment; no funding cash PnL.

Archive absence is recorded, never replaced by zero. Each daily measure requires
24 hours of reported, contiguous funding intervals ending on that signal day.
It is a known-at-close trailing-rate proxy, not a forecast of future payments.
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
from research.annual_rotation.data import SYMBOLS

START='2020-01-01'
END='2026-09-01'
FIELDS=('calc_time','funding_interval_hours','last_funding_rate')


def periods():
    return [str(x.date())[:7] for x in pd.date_range(START,END,freq='MS',inclusive='left')]


def request(url):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url,timeout=30) as response:
                raw=response.read(2*1024*1024+1)
                if len(raw)>2*1024*1024:raise ValueError('Funding archive byte budget exceeded')
                return raw
        except urllib.error.HTTPError as exc:
            if exc.code not in (429,500,502,503,504) or attempt==2:raise
            time.sleep(1+attempt)
    raise RuntimeError('Unreachable retry state')


def download(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=False)
    def one(item):
        symbol,month=item;filename=f'{symbol}-fundingRate-{month}.zip'
        url=f'https://data.binance.vision/data/futures/um/monthly/fundingRate/{symbol}/{filename}'
        rec=dict(symbol=symbol,month=month,filename=filename,url=url,status='unavailable')
        try:
            raw=request(url)
            expected=request(url+'.CHECKSUM').decode().split()[0].lower()
            actual=hashlib.sha256(raw).hexdigest()
            if len(expected)!=64 or expected!=actual:raise ValueError('Published checksum mismatch')
            (root/filename).write_bytes(raw)
            rec.update(status='available',sha256=actual,bytes=len(raw),published_checksum_verified=True)
        except urllib.error.HTTPError as exc:
            if exc.code!=404:raise
            rec.update(status='missing404',error=str(exc))
        return rec
    with ThreadPoolExecutor(max_workers=4) as ex:
        records=list(ex.map(one,[(s,m) for s in SYMBOLS for m in periods()]))
    manifest=dict(schema='funding-crowding-v1',start=START,end_exclusive=END,
        acquired_utc=dt.datetime.now(dt.timezone.utc).isoformat(),records=records,
        no_authentication=True,exchange_orders=False)
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print('ACQUISITION',json.dumps(dict(requested=len(records),available=sum(x['status']=='available' for x in records),
        missing=[dict(symbol=x['symbol'],month=x['month']) for x in records if x['status']!='available'])),flush=True)
    return manifest


def parse(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if len(z.namelist())!=1:raise ValueError('Expected one funding CSV')
        with z.open(z.namelist()[0]) as file:
            data=pd.read_csv(file,dtype=str)
    if tuple(data.columns)!=FIELDS:raise ValueError('Unexpected funding columns: '+repr(list(data.columns)))
    if data.empty:raise ValueError('Empty available archive')
    data=data.apply(pd.to_numeric,errors='raise')
    if not np.isfinite(data.to_numpy()).all():raise ValueError('Nonfinite funding source')
    ts=data.calc_time.to_numpy()
    if not np.equal(ts,np.floor(ts)).all():raise ValueError('Noninteger event timestamp')
    ts=ts.astype(np.int64)
    if np.any(ts>10**14):
        if not np.all(ts>10**14) or np.any(ts%1000):raise ValueError('Mixed timestamp units or sub-ms events')
        ts=ts//1000
    if np.any(ts<10**12) or len(np.unique(ts))!=len(ts):raise ValueError('Bad or duplicate funding timestamp')
    hours=data.funding_interval_hours.to_numpy(float)
    if not np.isin(hours,[1.,2.,4.,8.,12.,24.]).all():raise ValueError('Unexpected reported funding interval')
    if (np.abs(data.last_funding_rate)>1).any():raise ValueError('Funding rate outside plausible absolute1 bound')
    frame=pd.DataFrame({'rate':data.last_funding_rate.to_numpy(float),'interval_hours':hours},
        index=pd.to_datetime(ts,unit='ms',utc=True)).sort_index()
    return frame


def daily_rates(events,index):
    if str(events.index.tz)!='UTC' or events.index.has_duplicates or not events.index.is_monotonic_increasing:
        raise ValueError('Unique chronological UTC event source required')
    if not len(events):return pd.Series(np.nan,index=index,dtype=float),dict(events=0,valid_days=0)
    # Timestamps are not rounded. A one-second settlement timestamp tolerance is
    # only used to test interval continuity, never to reassign a future event.
    stamp=pd.Series(events.index,index=events.index)
    seconds=stamp.diff().dt.total_seconds()
    continuous=(seconds-events.interval_hours*3600).abs()<=1.
    daily=events.assign(continuous=continuous).groupby(events.index.floor('D')).agg(
        rate=('rate','sum'),hours=('interval_hours','sum'),continuous=('continuous','all'),count=('rate','size'))
    last=events.groupby(events.index.floor('D')).tail(1)
    gap=((last.index.floor('D')+pd.Timedelta(days=1)-last.index).total_seconds()/3600)
    fresh=pd.Series(gap<=last.interval_hours.to_numpy()+1/3600,index=last.index.floor('D'))
    good=daily.hours.eq(24)&daily.continuous&fresh
    result=daily.rate.where(good).reindex(index)
    return result,dict(events=len(events),valid_days=int(result.notna().sum()),
        interval_hours=sorted(float(x) for x in events.interval_hours.unique()),
        days_incomplete_intervals=int((~good).sum()),missing_or_invalid_days=int(result.isna().sum()),
        first_event=str(events.index[0]),last_event=str(events.index[-1]))


def load(root):
    root=Path(root);raw=(root/'manifest.json').read_bytes();manifest=json.loads(raw)
    expected={(s,m) for s in SYMBOLS for m in periods()};records=manifest['records']
    if manifest['start']!=START or manifest['end_exclusive']!=END or len(records)!=len(expected) or {(r['symbol'],r['month']) for r in records}!=expected:
        raise ValueError('Funding universe/date identity changed')
    index=pd.date_range(START,END,freq='D',inclusive='left',tz='UTC')
    by_symbol={s:[] for s in SYMBOLS};missing=[];archive_count=0
    for rec in records:
        if rec['status']=='missing404':missing.append((rec['symbol'],rec['month']));continue
        if rec['status']!='available':raise ValueError('Unresolved acquisition error')
        if rec['filename']!=f"{rec['symbol']}-fundingRate-{rec['month']}.zip":raise ValueError('Filename changed')
        content=(root/rec['filename']).read_bytes()
        if len(content)!=rec['bytes'] or hashlib.sha256(content).hexdigest()!=rec['sha256']:raise ValueError('Archive identity mismatch')
        frame=parse(content);start=pd.Timestamp(rec['month']+'-01',tz='UTC')
        if not ((frame.index>=start)&(frame.index<start+pd.offsets.MonthBegin(1))).all():raise ValueError('Archive month mismatch')
        by_symbol[rec['symbol']].append(frame);archive_count+=1
    result=pd.DataFrame(index=index,columns=SYMBOLS,dtype=float);quality={}
    for symbol,chunks in by_symbol.items():
        if not chunks:raise ValueError('No funding history for '+symbol)
        events=pd.concat(chunks).sort_index()
        result[symbol],quality[symbol]=daily_rates(events,index)
        if quality[symbol]['valid_days']<1200:raise ValueError('Insufficient fixed-period funding coverage for '+symbol)
    return result,dict(manifest_sha256=hashlib.sha256(raw).hexdigest(),archives=archive_count,
        missing404=missing,per_asset=quality,information_only_not_cash_funding=True,
        days=len(index),no_forward_fill=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();download(a.out);_,audit=load(a.out)
    (a.out/'audit.json').write_text(json.dumps(audit,indent=2));print('DATA_AUDIT',json.dumps(audit),flush=True)
