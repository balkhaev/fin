#!/usr/bin/env python3
from pathlib import Path
import base64,hashlib,io,json,tarfile,tempfile,py_compile
ROOT=Path(__file__).resolve().parent
for name in ('v26_v27','v28'):
 m=json.loads((ROOT/f'{name}_bundle_manifest.json').read_text());enc=(ROOT/f'{name}_bundle.b64').read_bytes()
 assert len(enc)==m['encoded_bytes'];assert hashlib.sha256(enc).hexdigest()==m['encoded_sha256']
 raw=base64.b64decode(enc,validate=True);assert len(raw)==m['archive_bytes'];assert hashlib.sha256(raw).hexdigest()==m['archive_sha256']
 with tempfile.TemporaryDirectory() as td:
  with tarfile.open(fileobj=io.BytesIO(raw),mode='r:bz2') as tf:
   assert all(not Path(x.name).is_absolute() and '..' not in Path(x.name).parts for x in tf.getmembers());tf.extractall(td,filter='data')
  for p in Path(td).rglob('*.py'):py_compile.compile(str(p),doraise=True)
s=json.loads((ROOT/'v28_summary.json').read_text());assert s['status']=='frozen_paper_forward_candidate';assert all(s['acceptance_checks'].values());assert s['stress_full']['annualized_return']>.30;assert s['funding_audits']['fund60']['full_cagr']>.29
for p in ('v28_run_research.py','v28_exact8h_engine.py'):py_compile.compile(str(ROOT/p),doraise=True)
print('Active V26-V28 archive integrity passed')
