"""Download the frozen 24 public CSVs for the one-account audit, without keys.

A fresh directory is required. Changes in source bytes are errors, not silent data
replacement. The downloader never purchases access or retries access restrictions.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import time
import urllib.request


def expected_sources() -> list[dict]:
    root=Path(__file__).parent
    old=json.loads((root/'event_sources.json').read_text())['files']
    extra=json.loads((root/'account_additional_sources.json').read_text())['files']
    rows=[{k:x[k] for k in ('date','venue','kind','bytes','sha256')} for x in old+extra]
    if len(rows)!=24 or len({(x['date'],x['venue'],x['kind']) for x in rows})!=24:
        raise ValueError('Frozen source set is not 24 unique files')
    return rows


def download(root:Path) -> dict:
    root.mkdir(parents=True,exist_ok=False)
    def one(item):
        row=dict(item)
        name=f"{row['date']}_{row['venue']}_{row['kind']}.csv.gz"
        url=f"https://datasets.tardis.dev/v1/{row['venue']}/{row['kind']}/{row['date'].replace('-','/')}/BTCUSDT.csv.gz"
        row.update(filename=name,url=url)
        target=root/name
        for attempt in range(3):
            try:
                count=0;h=hashlib.sha256()
                with urllib.request.urlopen(url,timeout=60) as response,target.open('wb') as output:
                    while True:
                        chunk=response.read(1<<20)
                        if not chunk:break
                        count+=len(chunk)
                        if count>250*(1<<20):raise ValueError('Per-file budget exceeded')
                        h.update(chunk);output.write(chunk)
                if count!=row['bytes'] or h.hexdigest()!=row['sha256']:
                    raise ValueError('Source content changed: frozen hash or length mismatch')
                row['status']='downloaded'
                return row
            except (OSError,ValueError) as exc:
                target.unlink(missing_ok=True)
                row.update(status='unavailable',error=str(exc))
                if isinstance(exc,ValueError) or getattr(exc,'code',None) in (401,403,404,451):break
                if attempt<2:time.sleep(1+attempt)
        return row
    with ThreadPoolExecutor(max_workers=3) as pool:
        files=list(pool.map(one,expected_sources()))
    result=dict(schema='btc-pressure-account-sources-v1',files=files,api_key_used=False,purchases=False)
    (root/'manifest.json').write_text(json.dumps(result,indent=2))
    if any(r['status']!='downloaded' for r in files):
        raise RuntimeError('Incomplete source download. Failures preserved in manifest.json.')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    print('Verified archives:',len(download(p.parse_args().out)['files']))
