"""Acquire public OHLC only. No credentials, trading adapter or host fallback.

OKX spot response: ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm.
Use base volume for capacity and volCcyQuote for quote-volume eligibility.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request

from research.annual_rotation.data import SYMBOLS

DAY=86400000
START=int(dt.datetime(2023,1,1,tzinfo=dt.timezone.utc).timestamp()*1000)
END=int(dt.datetime(2026,9,1,tzinfo=dt.timezone.utc).timestamp()*1000)
HOST='www.okx.com'
MAX_PAGES=20


def parse_page(payload,cursor):
    if not isinstance(payload,dict) or payload.get('code')!='0' or not isinstance(payload.get('data'),list):
        raise ValueError('Non-success or malformed official response')
    rows=[];seen=set()
    for row in payload['data']:
        if not isinstance(row,list) or len(row)!=9:raise ValueError('Expected nine candle fields')
        if not isinstance(row[0],str) or not row[0].isdigit():raise ValueError('Timestamp must be integer milliseconds')
        t=int(row[0])
        if t<=0 or t%DAY or t>=cursor:raise ValueError('Off-grid, future or non-prior candle')
        if t in seen:raise ValueError('Duplicate candle within page')
        if row[8]!='1':raise ValueError('Unconfirmed candle is not market evidence')
        values=[float(v) for v in row[1:8]]
        if not all(math.isfinite(v) for v in values):raise ValueError('Nonfinite candle values')
        o,h,l,c,volume,quote_volume_legacy,quote_volume=values
        if min(o,h,l,c)<=0 or min(volume,quote_volume_legacy,quote_volume)<0:
            raise ValueError('Nonpositive prices or negative volume')
        if h<max(o,l,c) or l>min(o,h,c):raise ValueError('Crossed OHLC')
        rows.append(dict(time=t,open=o,high=h,low=l,close=c,volume=volume,quote_volume=quote_volume))
        seen.add(t)
    times=[r['time'] for r in rows]
    if times!=sorted(times,reverse=True):raise ValueError('Response is not reverse chronological')
    return rows


def collect_rows(pages):
    combined={}
    for values in pages:
        for r in values:
            if r['time'] in combined:raise ValueError('Duplicate candle across pages')
            combined[r['time']]=r
    return [combined[t] for t in sorted(combined) if START<=t<END]


def get_page(url):
    if urllib.parse.urlsplit(url).netloc!=HOST:raise ValueError('Unapproved host')
    for attempt in range(3):
        started=time.monotonic()
        try:
            request=urllib.request.Request(url,headers={'Accept':'application/json','User-Agent':'fin-research-readonly/1.0'})
            with urllib.request.urlopen(request,timeout=40) as response:
                if urllib.parse.urlsplit(response.geturl()).netloc!=HOST:raise ValueError('Cross-host redirect forbidden')
                body=response.read(1024*1024+1)
                if len(body)>1024*1024:raise ValueError('Page byte budget exceeded')
                return body,response.status
        except urllib.error.HTTPError as exc:
            if exc.code not in (429,500,502,503,504) or attempt==2:raise
            time.sleep(1+attempt)
        finally:
            time.sleep(max(0.,.36-(time.monotonic()-started)))
    raise RuntimeError('Unreachable request state')


def download(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=False)
    all_sources=[]
    for symbol in SYMBOLS:
        instrument=symbol[:-4]+'-USDT'
        source=dict(symbol=symbol,instrument=instrument,bar='1Dutc',pages=[],status='incomplete')
        cursor=END;parsed=[]
        try:
            for number in range(MAX_PAGES):
                query=urllib.parse.urlencode(dict(instId=instrument,bar='1Dutc',after=str(cursor),limit='100'))
                url='https://'+HOST+'/api/v5/market/history-candles?'+query
                raw,status=get_page(url)
                filename=f'{symbol}-{number:02d}.json';(root/filename).write_bytes(raw)
                source['pages'].append(dict(filename=filename,sha256=hashlib.sha256(raw).hexdigest(),
                    bytes=len(raw),cursor=cursor,url=url,http_status=status))
                values=parse_page(json.loads(raw),cursor)
                if not values:break
                parsed.append(values)
                oldest=min(r['time'] for r in values)
                if oldest>=cursor:raise ValueError('Cursor did not advance')
                cursor=oldest
                if oldest<=START:break
            rows=collect_rows(parsed)
            expected=set(range(START,END,DAY));observed={r['time'] for r in rows}
            missing=sorted(expected-observed)
            source.update(rows=len(rows),missing_days=len(missing),first_missing_ms=missing[:20],
                first_day_ms=min(observed) if observed else None,last_day_ms=max(observed) if observed else None)
            if missing:raise ValueError('Incomplete fixed-period source; no date or asset substitution')
            source['status']='complete'
        except (ValueError,OSError,RuntimeError) as exc:
            source.update(status='unavailable',error=str(exc))
        all_sources.append(source)
        print('SOURCE_STATUS',json.dumps({k:v for k,v in source.items() if k!='pages'}),flush=True)
    manifest=dict(id='okx-rotation-transfer-v1',start_ms=START,end_ms=END,bar='1Dutc',
        created_utc=dt.datetime.now(dt.timezone.utc).isoformat(),sources=all_sources,
        no_credentials=True,exchange_orders=False,alternate_host_attempted=False,
        local_hash_not_exchange_signature=True)
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2))
    if any(s['status']!='complete' for s in all_sources):raise RuntimeError('Full-cohort evidence unavailable; read manifest')
    return manifest


def load(root):
    import pandas as pd
    root=Path(root);raw=(root/'manifest.json').read_bytes();manifest=json.loads(raw)
    sources=manifest['sources']
    if manifest['start_ms']!=START or manifest['end_ms']!=END or manifest['bar']!='1Dutc':
        raise ValueError('Changed fixed data interval')
    if len(sources)!=len(SYMBOLS) or {s['symbol'] for s in sources}!=set(SYMBOLS):raise ValueError('Changed source universe')
    index=pd.to_datetime(list(range(START,END,DAY)),unit='ms',utc=True)
    frames={};audits={}
    for source in sources:
        symbol=source['symbol']
        if source['status']!='complete' or source['instrument']!=symbol[:-4]+'-USDT':raise ValueError('Incomplete instrument')
        pages=[];cursor=END
        if not 1<=len(source['pages'])<=MAX_PAGES:raise ValueError('Page count out of bounds')
        for number,page in enumerate(source['pages']):
            name=f'{symbol}-{number:02d}.json'
            if page['filename']!=name or page['cursor']!=cursor:raise ValueError('Path/pagination identity changed')
            content=(root/name).read_bytes()
            if len(content)!=page['bytes'] or hashlib.sha256(content).hexdigest()!=page['sha256']:raise ValueError('Raw page SHA/size mismatch')
            values=parse_page(json.loads(content),cursor)
            if not values:raise ValueError('Unexpected empty page in complete source')
            pages.append(values);cursor=min(r['time'] for r in values)
        values=collect_rows(pages)
        frame=pd.DataFrame(values).set_index('time')
        frame.index=pd.to_datetime(frame.index,unit='ms',utc=True)
        if not frame.index.equals(index):raise ValueError('Missing/extra normalized daily rows')
        frames[symbol]=frame
        audits[symbol]=dict(rows=len(frame),page_count=len(pages),zero_volume_days=int(frame.volume.eq(0).sum()))
    return frames,dict(manifest_sha256=hashlib.sha256(raw).hexdigest(),source='OKX spot REST',
        same_UTC_day=True,all_confirmed=True,local_hash_not_exchange_signature=True,
        start='2023-01-01',end_exclusive='2026-09-01',per_asset=audits)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    download(parser.parse_args().out)
