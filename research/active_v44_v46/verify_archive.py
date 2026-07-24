#!/usr/bin/env python3
from pathlib import Path
import base64,hashlib,io,json,subprocess,sys,tarfile,tempfile
ROOT=Path(__file__).resolve().parent
B=ROOT/'bundle'
m=json.loads((B/'manifest.json').read_text())
chunks=[]
for x in m['parts']:
    p=B/x['path'];b=p.read_bytes()
    assert len(b)==x['bytes'],(p,len(b),x['bytes'])
    assert hashlib.sha256(b).hexdigest()==x['sha256']
    chunks.append(b)
enc=b''.join(chunks)
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
    subprocess.run([sys.executable,str(td/'verify_archive.py')],check=True)
print('Active V44-V46 outer bundle integrity passed')
