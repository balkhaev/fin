"""Finite public download only. No credentials, order submission, or access bypass."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import hashlib
import json
from pathlib import Path
import time
import urllib.request
import zipfile
import io

START=1640995200000
END=1785542400000


def get(url):
    last=None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url,timeout=60) as r:
                raw=r.read(200*1024*1024+1)
                if len(raw)>200*1024*1024:raise ValueError('Per-file download budget exceeded')
                return raw
        except (OSError,ValueError) as exc:
            last=exc
            if isinstance(exc,ValueError) or getattr(exc,'code',None) in (401,403,404,451):break
            if attempt<2:time.sleep(attempt+1)
    raise RuntimeError(f'{url}: {last}') from last


def tasks():
    for year in range(2022,2027):
        for month in range(1,13):
            if year==2026 and month>7:continue
            period=f'{year}-{month:02d}'
            for kind in ('spot','perp','mark','funding'):
                if kind=='spot':path=f'spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-{period}.zip'
                elif kind=='perp':path=f'futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-{period}.zip'
                elif kind=='mark':path=f'futures/um/monthly/markPriceKlines/BTCUSDT/1m/BTCUSDT-1m-{period}.zip'
                else:path=f'futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-{period}.zip'
                yield kind,period,'https://data.binance.vision/data/'+path


def download(root:Path):
    root.mkdir(parents=True,exist_ok=False)
    def one(task):
        kind,period,url=task
        row=dict(kind=kind,period=period,url=url,filename=f'{kind}-{period}.zip')
        try:
            check=get(url+'.CHECKSUM').decode().split()[0].lower()
            raw=get(url);digest=hashlib.sha256(raw).hexdigest()
            if check!=digest:raise ValueError('Published SHA-256 mismatch')
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                if len(z.namelist())!=1:raise ValueError('Expected one CSV')
                with z.open(z.namelist()[0]) as f:row['first_line']=f.readline().decode().strip()
            (root/row['filename']).write_bytes(raw)
            row.update(status='verified',sha256=digest,bytes=len(raw))
        except Exception as e:row.update(status='missing',error=str(e))
        return row
    with ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(one,tasks()))
    manifest=dict(schema='btc-flow-continuous-data-v1',files=rows,start_ms=START,end_ms=END,
       acquired_at=dt.datetime.now(dt.timezone.utc).isoformat(),source='Binance public archives',orders_sent=0)
    # Try official exact settlement marks once. On a restricted endpoint do not
    # change hosts, proxies, keys or locations. The failure is visible evidence.
    exact=[];cursor=START;error=None
    try:
        while cursor<END:
            url=f'https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&startTime={cursor}&endTime={END-1}&limit=1000'
            data=json.loads(get(url))
            if not isinstance(data,list):raise ValueError('Unexpected funding API response')
            if not data:break
            exact.extend(data)
            nxt=max(int(x['fundingTime']) for x in data)+1
            if nxt<=cursor:raise ValueError('Funding pagination did not advance')
            cursor=nxt;time.sleep(.25)
    except Exception as exc:error=str(exc)
    raw=json.dumps(dict(rows=exact,error=error),indent=2).encode()
    (root/'exact_funding.json').write_bytes(raw)
    manifest['exact_funding']=dict(rows=len(exact),error=error,sha256=hashlib.sha256(raw).hexdigest())
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps(dict(files=len(rows),verified=sum(x['status']=='verified' for x in rows),
        missing=[x for x in rows if x['status']!='verified'],first_rows={k:next(x.get('first_line') for x in rows if x['kind']==k) for k in ('spot','perp','mark','funding')},
        exact_funding=manifest['exact_funding']),indent=2),flush=True)
    if any(r['status']!='verified' for r in rows):raise RuntimeError('Incomplete sources; see retained manifest')
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    download(p.parse_args().out)
