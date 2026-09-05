"""Download public BTC USD-M monthly bars/funding; reject unverifiable evidence."""
from __future__ import annotations
import argparse
import concurrent.futures
import hashlib
import json
import time
import urllib.request
from pathlib import Path


def get(url: str) -> bytes:
    error = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return response.read()
        except Exception as exc:
            error = exc
            if attempt < 2:
                time.sleep(attempt+1)
    raise RuntimeError(f'Download failed: {url}: {error}') from error


def download(root: Path) -> list[dict]:
    root.mkdir(parents=True, exist_ok=True)
    tasks = []
    for year in range(2022,2027):
        for month in range(1,13):
            if year==2026 and month>7:
                continue
            period=f'{year}-{month:02d}'
            for kind in ('klines','fundingRate'):
                name=(f'BTCUSDT-1m-{period}.zip' if kind=='klines' else
                      f'BTCUSDT-fundingRate-{period}.zip')
                path=(f'klines/BTCUSDT/1m/{name}' if kind=='klines' else
                      f'fundingRate/BTCUSDT/{name}')
                tasks.append((kind,period,name,'https://data.binance.vision/data/futures/um/monthly/'+path))
    def one(task):
        kind,period,name,url=task
        try:
            expected=get(url+'.CHECKSUM').decode().split()[0].lower()
            path=root/name
            raw=path.read_bytes() if path.exists() else get(url)
            actual=hashlib.sha256(raw).hexdigest()
            if actual!=expected:
                raise ValueError('SHA-256 mismatch; cached data was not overwritten')
            path.write_bytes(raw)
            return dict(kind=kind,period=period,filename=name,url=url,sha256=actual,bytes=len(raw),status='verified')
        except Exception as exc:
            return dict(kind=kind,period=period,filename=name,url=url,status='missing',error=str(exc))
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        manifest=list(pool.map(one,tasks))
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2))
    if any(x['status']!='verified' for x in manifest):
        raise RuntimeError('Incomplete data; inspect manifest.json. No zero-fill fallback.')
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    rows=download(a.out)
    print(json.dumps(dict(verified_archives=len(rows),out=str(a.out)),indent=2))
