"""Read-only public USD-M data. No exchange authentication or order endpoint.
Price/funding gaps are retained rather than interpolated. Settlement marking
uses an explicitly approximate hour OPEN; raw event timestamps remain audited.
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import argparse,datetime as dt,hashlib,io,json,time,urllib.error,urllib.request,zipfile
import numpy as np
import pandas as pd
from research.funding_crowding.data import parse as parse_funding

SYMBOLS=('BTCUSDT','ETHUSDT')
START='2020-01-01'; END='2026-09-01'
PRICE_COLS=['time','open','high','low','close','volume','close_time','quote_volume','trades','buy_volume','buy_quote','ignore']
KINDS=('klines','markPriceKlines','fundingRate')

def request(url):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url,timeout=40) as response:
                raw=response.read(4*1024*1024+1)
                if len(raw)>4*1024*1024:raise ValueError('Archive exceeds fixed byte budget')
                return raw
        except urllib.error.HTTPError as exc:
            if exc.code not in (429,500,502,503,504) or attempt==2:raise
            time.sleep(1+attempt)
    raise RuntimeError('Unreachable')

def acquire(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=False)
    def archive(symbol,kind,date,freq):
        suffix=f'fundingRate-{date}' if kind=='fundingRate' else f'1h-{date}'
        filename=f'{symbol}-{suffix}.zip'
        tail=f'{symbol}/{filename}' if kind=='fundingRate' else f'{symbol}/1h/{filename}'
        url=f'https://data.binance.vision/data/futures/um/{freq}/{kind}/{tail}'
        expected=request(url+'.CHECKSUM').decode().split()[0].lower()
        raw=request(url);sha=hashlib.sha256(raw).hexdigest()
        if len(expected)!=64 or sha!=expected:raise ValueError('Published archive hash differs')
        name=kind+'_'+filename;(root/name).write_bytes(raw)
        return dict(filename=name,url=url,bytes=len(raw),sha256=sha)
    def one(item):
        symbol,kind,month=item;row=dict(symbol=symbol,kind=kind,month=month,parts=[])
        try:
            row['parts']=[archive(symbol,kind,month,'monthly')];row['status']='monthly'
        except urllib.error.HTTPError as exc:
            if exc.code!=404:raise
            if kind=='fundingRate':row.update(status='missing404')
            else:
                start=pd.Timestamp(month+'-01');parts=[];missing=[]
                for day in pd.date_range(start,start+pd.offsets.MonthBegin(1),freq='D',inclusive='left'):
                    try:parts.append(archive(symbol,kind,str(day.date()),'daily'))
                    except urllib.error.HTTPError as de:
                        if de.code!=404:raise
                        missing.append(str(day.date()))
                row.update(status='daily_fallback',parts=parts,missing_days=missing)
        return row
    months=[str(t.date())[:7] for t in pd.date_range(START,END,freq='MS',inclusive='left')]
    with ThreadPoolExecutor(max_workers=4) as pool:
        records=list(pool.map(one,[(s,k,m) for s in SYMBOLS for k in KINDS for m in months]))
    manifest=dict(start=START,end_exclusive=END,created_utc=dt.datetime.now(dt.timezone.utc).isoformat(),records=records)
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print('ACQUIRED',json.dumps(dict(identities=len(records),zip_files=sum(len(r['parts']) for r in records),
        missing=[{k:v for k,v in r.items() if k!='parts'} for r in records if r['status']=='missing404' or r.get('missing_days')])),flush=True)

def parse_prices(raw,kind):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if len(z.namelist())!=1:raise ValueError('One CSV per archive required')
        with z.open(z.namelist()[0]) as f:d=pd.read_csv(f,header=None,dtype=str)
    if len(d.columns)!=12:raise ValueError('Unknown price schema width: '+str(len(d.columns)))
    d.columns=PRICE_COLS
    bad=pd.to_numeric(d.time,errors='coerce').isna()
    if bad.any():
        if bad.sum()!=1 or not bad.iloc[0]:raise ValueError('Malformed timestamp row')
        d=d.iloc[1:]
    d=d.apply(pd.to_numeric,errors='raise')
    ts=d.time.to_numpy(np.int64);end=d.close_time.to_numpy(np.int64)
    if (ts>10**14).any():
        if not (ts>10**14).all() or (ts%1000).any():raise ValueError('Mixed timestamp units')
        ts=ts//1000;end=end//1000
    if (ts%3600000).any() or len(np.unique(ts))!=len(ts):raise ValueError('Off-hour/duplicate price')
    duration=end-ts
    if ((duration<0)|(duration>3599999)).any():raise ValueError('Invalid duration')
    p=d[['open','high','low','close']]
    if not np.isfinite(p.to_numpy()).all() or (p<=0).any().any():raise ValueError('Invalid price')
    if ((d.high<p.max(axis=1))|(d.low>p.min(axis=1))).any():raise ValueError('Crossed OHLC')
    if kind=='klines' and (not np.isfinite(d.volume).all() or (d.volume<0).any()):raise ValueError('Bad base volume')
    d.index=pd.to_datetime(ts,unit='ms',utc=True)
    d=d[['open','high','low','close','volume']].astype(float)
    d.loc[duration!=3599999,:]=np.nan
    return d.sort_index()

def normalize(rawdir,out):
    root=Path(rawdir);out=Path(out)
    if out.exists():raise FileExistsError('Fresh normalized output required')
    raw=(root/'manifest.json').read_bytes();manifest=json.loads(raw)
    months=[str(t.date())[:7] for t in pd.date_range(START,END,freq='MS',inclusive='left')]
    expected={(s,k,m) for s in SYMBOLS for k in KINDS for m in months}
    rows=manifest['records']
    if len(rows)!=len(expected) or {(r['symbol'],r['kind'],r['month']) for r in rows}!=expected:raise ValueError('Source set changed')
    groups={(s,k):[] for s in SYMBOLS for k in KINDS};events_audit={};nfiles=0
    for record in rows:
        for part in record['parts']:
            if '/' in part['filename'] or '..' in part['filename']:raise ValueError('Unsafe path')
            content=(root/part['filename']).read_bytes()
            if len(content)!=part['bytes'] or hashlib.sha256(content).hexdigest()!=part['sha256']:raise ValueError('Raw SHA mismatch')
            frame=parse_funding(content) if record['kind']=='fundingRate' else parse_prices(content,record['kind'])
            start=pd.Timestamp(record['month']+'-01',tz='UTC')
            if not ((frame.index>=start)&(frame.index<start+pd.offsets.MonthBegin(1))).all():raise ValueError('Wrong month')
            groups[record['symbol'],record['kind']].append(frame);nfiles+=1
    index=pd.date_range(START,END,freq='h',inclusive='left',tz='UTC');out.mkdir(parents=True)
    files=[];quality={}
    for symbol in SYMBOLS:
        prepared={}
        for kind in KINDS:
            pieces=groups[symbol,kind]
            if not pieces:raise ValueError('Entire required source missing')
            d=pd.concat(pieces).sort_index()
            if d.index.has_duplicates:raise ValueError('Duplicate across source parts')
            prepared[kind]=d
        trade=prepared['klines'].reindex(index)
        mark=prepared['markPriceKlines'].reindex(index).drop(columns='volume').add_prefix('mark_')
        event=prepared['fundingRate']
        if not event.interval_hours.eq(8).all():raise ValueError('Observed funding interval needs explicit new handling')
        hours=event.index.floor('h');offset=(event.index-hours).total_seconds()
        if (offset>1).any() or hours.has_duplicates or not np.isin(hours.hour,[0,8,16]).all():raise ValueError('Funding not at documented8h boundary')
        rates=pd.Series(event.rate.to_numpy(),index=hours)
        frame=trade.join(mark)
        frame['funding_event']=np.isin(index.hour,[0,8,16])
        frame['funding_known']=~frame.funding_event | rates.reindex(index).notna()
        frame['funding_rate']=rates.reindex(index).fillna(0.)
        # Zero outside scheduled events is no payment, not repaired unknown funding.
        name=symbol+'.csv';frame.to_csv(out/name,index_label='time')
        events_audit[symbol]=dict(events=len(event),maximum_boundary_offset_seconds=float(offset.max()),
            settlement_mark='hour_open_proxy_not_exact_event_mark')
        quality[symbol]=dict(hours=len(index),missing_trade_hours=int(frame.open.isna().sum()),
            missing_mark_hours=int(frame.mark_open.isna().sum()),unknown_settlement_hours=int((~frame.funding_known).sum()))
        files.append(dict(symbol=symbol,filename=name,sha256=hashlib.sha256((out/name).read_bytes()).hexdigest()))
    audit=dict(raw_manifest_sha256=hashlib.sha256(raw).hexdigest(),raw_files=nfiles,files=files,
        quality=quality,funding=events_audit,no_price_interpolation=True)
    (out/'audit.json').write_text(json.dumps(audit,indent=2));print('DATA_AUDIT',json.dumps(audit),flush=True)

def load(root):
    root=Path(root);audit=json.loads((root/'audit.json').read_text());frames={}
    for item in audit['files']:
        if item['filename']!=item['symbol']+'.csv':raise ValueError('Changed normalized path')
        content=(root/item['filename']).read_bytes()
        if hashlib.sha256(content).hexdigest()!=item['sha256']:raise ValueError('Normalized hash mismatch')
        d=pd.read_csv(io.BytesIO(content),index_col='time',parse_dates=True);d.index=pd.to_datetime(d.index,utc=True)
        for col in ('funding_event','funding_known'):
            if d[col].dtype!=bool:raise ValueError('Unexpected boolean encoding')
        frames[item['symbol']]=d
    if set(frames)!=set(SYMBOLS):raise ValueError('Changed instrument universe')
    return frames,audit

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--raw',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--download',action='store_true');a=p.parse_args()
    if a.download:acquire(a.raw)
    normalize(a.raw,a.out)
