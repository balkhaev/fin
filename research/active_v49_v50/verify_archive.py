#!/usr/bin/env python3
from pathlib import Path
import base64,hashlib,io,json,subprocess,sys,tarfile,tempfile
ROOT=Path(__file__).resolve().parent
B=ROOT/'source_bundle'
m=json.loads((B/'manifest.json').read_text())
parts=[]
for x in m['parts']:
    p=B/x['path'];b=p.read_bytes()
    assert len(b)==x['bytes'],(p,len(b),x['bytes'])
    assert hashlib.sha256(b).hexdigest()==x['sha256']
    parts.append(b)
enc=b''.join(parts)
assert len(enc)==m['encoded_bytes']
assert hashlib.sha256(enc).hexdigest()==m['encoded_sha256']
raw=base64.b64decode(enc,validate=True)
assert len(raw)==m['archive_bytes']
assert hashlib.sha256(raw).hexdigest()==m['archive_sha256']
with tempfile.TemporaryDirectory() as td:
    td=Path(td)
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:bz2') as tf:
        members=tf.getmembers()
        assert members
        assert all(not Path(x.name).is_absolute() and '..' not in Path(x.name).parts for x in members)
        tf.extractall(td,filter='data')
    subprocess.run([sys.executable,str(td/'verify_inner.py')],check=True)
d=json.loads((ROOT/'decision.json').read_text())
assert d['promoted_leaders']==[]
assert d['historical_candidates_needing_nonzero_forward']==['v49_vol_budget','v50_multiplicative']
assert d['v50']['prefinal_max_drawdown']>-0.35
assert d['v50']['prefinal_cagr']>0.13
assert d['v50']['prefinal_sharpe']>0.90
assert d['v50']['final_2026_ytd_return']==0.0
print('Active V49-V50 outer archive integrity passed')
