"""Download the frozen August 2026 Binance archives; never substitute missing data."""
import argparse,concurrent.futures,hashlib,json
from pathlib import Path
from research.btc_flow.download import get


def download(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    base='https://data.binance.vision/data/futures/um/';tasks=[]
    for day in range(1,32):
        s=f'2026-08-{day:02d}'
        for kind in ('klines','markPriceKlines'):
            name=f'BTCUSDT-1m-{s}.zip'
            tasks.append((kind,s,kind+'-'+name,base+f'daily/{kind}/BTCUSDT/1m/{name}'))
    name='BTCUSDT-fundingRate-2026-08.zip'
    tasks.append(('fundingRate','2026-08',name,base+f'monthly/fundingRate/BTCUSDT/{name}'))
    def one(t):
        kind,period,name,url=t
        try:
            expected=get(url+'.CHECKSUM').decode().split()[0];path=root/name
            raw=path.read_bytes() if path.exists() else get(url)
            actual=hashlib.sha256(raw).hexdigest()
            if actual!=expected:raise ValueError('Checksum mismatch')
            path.write_bytes(raw)
            return dict(kind=kind,period=period,filename=name,url=url,sha256=actual,bytes=len(raw),status='verified')
        except Exception as e:
            return dict(kind=kind,period=period,filename=name,url=url,status='missing',error=str(e))
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:rows=list(pool.map(one,tasks))
    (root/'manifest.json').write_text(json.dumps(rows,indent=2))
    if any(r['status']!='verified' for r in rows):raise RuntimeError('Incomplete August evidence; see manifest')
    return rows

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    print('Verified archives:',len(download(p.parse_args().out)))
