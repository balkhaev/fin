"""Retrieve fixed public event samples and verify the recorded content hashes.

No subscription, API key, exchange credentials or real order submission.
A changed vendor file is an integrity failure, not a silently replaced dataset.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.request


def download(root: Path) -> dict:
    if root.exists():
        raise FileExistsError('Use a new data directory; failed evidence is retained')
    root.mkdir(parents=True)
    spec=json.loads(Path(__file__).with_name('event_sources.json').read_text())
    def one(expected):
        row=dict(expected)
        name=f"{row['date']}_{row['venue']}_{row['kind']}.csv.gz"
        url=f"https://datasets.tardis.dev/v1/{row['venue']}/{row['kind']}/{row['date'].replace('-','/')}/BTCUSDT.csv.gz"
        target=root/name;row.update(filename=name,url=url)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url,timeout=60) as response, target.open('wb') as stream:
                    digest=hashlib.sha256();count=0
                    while True:
                        chunk=response.read(1024*1024)
                        if not chunk:break
                        count+=len(chunk)
                        if count>250*1024*1024:raise ValueError('Per-file budget exceeded')
                        digest.update(chunk);stream.write(chunk)
                if digest.hexdigest()!=row['sha256'] or count!=row['bytes']:
                    raise ValueError('Source changed or incomplete; frozen SHA-256/size mismatch')
                row['status']='downloaded';return row
            except (OSError,ValueError) as error:
                target.unlink(missing_ok=True)
                row.update(status='unavailable',error=str(error))
                if isinstance(error,ValueError) or getattr(error,'code',None) in (401,403,404,451):break
                if attempt<2:time.sleep(1+attempt)
        return row
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        rows=list(pool.map(one,spec['files']))
    manifest=dict(protocol_sha256=spec['protocol_sha256'],files=rows,api_key_used=False,purchases=False,live_orders=False)
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2))
    if any(row['status']!='downloaded' for row in rows):
        raise RuntimeError('Incomplete frozen sample. See manifest; no data replacement or access-control bypass.')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    print('Verified archives:',len(download(args.out)['files']))
