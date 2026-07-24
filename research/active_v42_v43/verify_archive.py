#!/usr/bin/env python3
from __future__ import annotations
import base64, hashlib, io, json, py_compile, tarfile, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent

def sha(b:bytes)->str:
    return hashlib.sha256(b).hexdigest()

def main()->int:
    bundle=ROOT/'source_bundle';manifest=json.loads((bundle/'manifest.json').read_text());parts=[]
    for item in manifest['parts']:
        p=bundle/item['path'];b=p.read_bytes()
        assert len(b)==item['bytes'],(p,len(b),item['bytes'])
        assert sha(b)==item['sha256'],p
        parts.append(b)
    encoded=b''.join(parts)
    assert len(encoded)==manifest['encoded_bytes']
    assert sha(encoded)==manifest['encoded_sha256']
    raw=base64.b64decode(encoded,validate=True)
    assert len(raw)==manifest['archive_bytes']
    assert sha(raw)==manifest['archive_sha256']
    with tempfile.TemporaryDirectory() as td:
        out=Path(td)
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:bz2') as tf:
            members=tf.getmembers();assert members
            assert all(not Path(x.name).is_absolute() and '..' not in Path(x.name).parts for x in members)
            tf.extractall(out,filter='data')
        for name,meta in manifest['source_files'].items():
            matches=list(out.rglob(name));assert len(matches)==1,(name,matches)
            p=matches[0];assert p.stat().st_size==meta['bytes'];assert sha(p.read_bytes())==meta['sha256']
            if p.suffix=='.py':py_compile.compile(str(p),doraise=True)
    summary=json.loads((ROOT/'results/summary.json').read_text())
    evidence=json.loads((ROOT/'results/selection_evidence.json').read_text())
    quality=json.loads((ROOT/'results/data_quality.json').read_text())
    compute=json.loads((ROOT/'public_compute.json').read_text())
    assert summary['status']=='rejected_or_needs_iteration'
    assert summary['promoted_leaders']==[]
    assert all(v['status']=='rejected_or_needs_iteration' for v in summary['leaders'].values())
    assert evidence['status']==summary['status'] and evidence['promoted_leaders']==[]
    assert evidence['selection_proof_sha256']==summary['selection_proof_sha256']=='838c3a311302f1a932aee20bcdf7c96f348f3af50241e865c51d5b09c74648fa'
    assert evidence['selection_uses_2021_2025_only'] is True and evidence['final_opened_after_proof'] is True
    assert quality['checksum_failed']==0 and quality['checksum_available']==2269
    assert summary['leaders']['oi']['prefinal']['prefinal_cagr']<0
    assert summary['leaders']['crowding']['prefinal']['prefinal_cagr']<0
    assert compute['workflow_run_id']==30110715901 and compute['artifact_id']==8603441176
    assert compute['effective_data_py_sha256']==manifest['source_files']['data.py']['sha256']
    print('Active V42-V43 archive integrity passed')
    return 0

if __name__=='__main__':
    raise SystemExit(main())
