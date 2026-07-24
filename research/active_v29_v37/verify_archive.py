#!/usr/bin/env python3
from pathlib import Path
import base64,hashlib,io,json,py_compile,tarfile,tempfile
ROOT=Path(__file__).resolve().parent
def unpack(name):
 d=ROOT/name;M=json.loads((d/'manifest.json').read_text());parts=[]
 for x in M['parts']:
  p=d/x['path'];b=p.read_bytes();assert len(b)==x['bytes'];assert hashlib.sha256(b).hexdigest()==x['sha256'];parts.append(b)
 enc=b''.join(parts);assert len(enc)==M['encoded_bytes'];assert hashlib.sha256(enc).hexdigest()==M['encoded_sha256'];raw=base64.b64decode(enc,validate=True);assert len(raw)==M['archive_bytes'];assert hashlib.sha256(raw).hexdigest()==M['archive_sha256']
 td=tempfile.TemporaryDirectory();out=Path(td.name)
 with tarfile.open(fileobj=io.BytesIO(raw),mode='r:bz2') as tf:
  assert all(not Path(x.name).is_absolute() and '..' not in Path(x.name).parts for x in tf.getmembers());tf.extractall(out,filter='data')
 return td,out,M
src_td,src,SM=unpack('source_bundle')
for name,meta in SM['source_files'].items():
 p=src/name;assert p.exists() and p.stat().st_size==meta['bytes'] and hashlib.sha256(p.read_bytes()).hexdigest()==meta['sha256']
for p in src.rglob('*.py'):py_compile.compile(str(p),doraise=True)
res_td,res,RM=unpack('results_bundle')
for name,meta in RM['files'].items():
 p=res/name;assert p.exists() and p.stat().st_size==meta['bytes'] and hashlib.sha256(p.read_bytes()).hexdigest()==meta['sha256']
S=json.loads((res/'all_summaries.json').read_text());assert len(S)==9;assert all(x['status']=='rejected_or_needs_iteration' for x in S.values())
src_td.cleanup();res_td.cleanup();print('Active V29-V37 archive integrity passed')
