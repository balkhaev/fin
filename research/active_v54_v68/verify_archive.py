#!/usr/bin/env python3
from pathlib import Path
import base64,hashlib,io,json,py_compile,tarfile,tempfile
ROOT=Path(__file__).resolve().parent;M=json.loads((ROOT/'bundle/manifest.json').read_text())
def sha(b):return hashlib.sha256(b).hexdigest()
parts=[]
for x in M['parts']:
 p=ROOT/'bundle'/x['path'];b=p.read_bytes();assert len(b)==x['bytes'];assert sha(b)==x['sha256'];parts.append(b)
enc=b''.join(parts);assert len(enc)==M['encoded_bytes'];assert sha(enc)==M['encoded_sha256'];raw=base64.b64decode(enc,validate=True);assert len(raw)==M['archive_bytes'];assert sha(raw)==M['archive_sha256']
with tempfile.TemporaryDirectory() as td:
 out=Path(td)
 with tarfile.open(fileobj=io.BytesIO(raw),mode='r:bz2') as tf:
  ms=tf.getmembers();assert all(not Path(x.name).is_absolute() and '..' not in Path(x.name).parts for x in ms);tf.extractall(out,filter='data')
 for name,meta in M['source_files'].items():
  p=out/'source'/name;assert p.exists();assert p.stat().st_size==meta['bytes'];assert sha(p.read_bytes())==meta['sha256'];py_compile.compile(str(p),doraise=True)
d=json.loads((ROOT/'DECISION.json').read_text());assert d['active_growth_benchmark']=='V28';assert d['promoted_live_candidates']==[];assert d['frozen_forward_candidates']['V65']['target_gross_cap']==1.10;assert d['frozen_forward_candidates']['V67']['target_gross_cap']==1.15;assert d['frozen_forward_candidates']['V67']['liquidations']==0
print('Active V54-V68 archive verified')
