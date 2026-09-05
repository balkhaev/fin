"""Retrieve hash-pinned public tapes for the precommitted paired health study."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import time
import urllib.request
from .download_account_samples import expected_sources
from .health_study import DATES


def download(root:Path, dates= DATES):
    dates=tuple(dates)
    if not dates or any(d not in DATES for d in dates) or len(set(dates))!=len(dates):
        raise ValueError('Dates must be unique members of the precommitted set')
    spec=json.loads(Path(__file__).with_name('health_sources.json').read_text())
    all_rows=expected_sources()+spec['files']
    identities={(x['date'],x['venue'],x['kind']) for x in all_rows}
    if len(all_rows)!=48 or len(identities)!=48: raise ValueError('Source identity set changed')
    rows=[x for x in all_rows if x['date'] in dates]
    if len(rows)!=8*len(dates): raise ValueError('Eight sources required for every date')
    root.mkdir(parents=True,exist_ok=False)
    def one(x):
        row={k:x[k] for k in ('date','venue','kind','sha256','bytes')}
        name=f"{row['date']}_{row['venue']}_{row['kind']}.csv.gz"
        url=f"https://datasets.tardis.dev/v1/{row['venue']}/{row['kind']}/{row['date'].replace('-','/')}/BTCUSDT.csv.gz"
        row.update(filename=name,url=url);path=root/name
        for attempt in range(3):
            try:
                count=0;h=hashlib.sha256()
                with urllib.request.urlopen(url,timeout=60) as response,path.open('wb') as output:
                    for block in iter(lambda:response.read(1<<20),b''):
                        count+=len(block)
                        if count>250*(1<<20): raise ValueError('File budget exceeded')
                        h.update(block);output.write(block)
                if count!=row['bytes'] or h.hexdigest()!=row['sha256']:
                    raise ValueError('Frozen source hash/size mismatch')
                row['status']='downloaded';return row
            except (OSError,ValueError) as exc:
                path.unlink(missing_ok=True);row.update(status='unavailable',error=str(exc))
                if isinstance(exc,ValueError) or getattr(exc,'code',None) in (401,403,404,451): break
                if attempt<2:time.sleep(attempt+1)
        return row
    with ThreadPoolExecutor(max_workers=3) as pool:files=list(pool.map(one,rows))
    result=dict(files=files,protocol_sha256=spec['protocol_sha256'],api_key_used=False,purchases=False,live_orders=False)
    (root/'manifest.json').write_text(json.dumps(result,indent=2))
    if any(x['status']!='downloaded' for x in files):raise RuntimeError('Incomplete data; see manifest. No substitution.')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--dates',nargs='+',choices=DATES,default=DATES)
    a=p.parse_args();print('Verified files:',len(download(a.out,a.dates)['files']))
