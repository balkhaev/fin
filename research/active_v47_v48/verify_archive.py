#!/usr/bin/env python3
from pathlib import Path
import base64,hashlib,io,json,py_compile,tarfile,tempfile
import pandas as pd
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
    src=td/'source'/'run_v47.py'
    assert hashlib.sha256(src.read_bytes()).hexdigest()==m['source_sha256']
    py_compile.compile(str(src),doraise=True)
    proofs=json.loads((td/'selection_proof_before_final.json').read_text())
    library=pd.read_csv(td/'selection_library.csv')
    assert len(library)==30
    assert set(proofs)=={'v44_raw','v46_confirmation'}
    assert proofs['v44_raw']['selection_proof_sha256']=='c9b2f65009c4b630098fecc7b2c745159f945a6e3d80db97d75ef77751d6b78c'
    assert proofs['v46_confirmation']['selection_proof_sha256']=='3972c8f3d128fefe632d5058d5215b0289e8ccad0beb3a205b64b069b6ae30bd'
s=json.loads((ROOT/'summary.json').read_text())
assert s['status']=='rejected_or_needs_iteration'
assert s['promoted_leaders']==[]
assert s['best_near_miss']['failed_gate']=='prefinal_max_drawdown_min_-0.35'
assert s['best_near_miss']['prefinal_max_drawdown'] < -0.35
print('Active V47-V48 archive integrity passed')
