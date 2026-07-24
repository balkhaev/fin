#!/usr/bin/env python3
from __future__ import annotations
import csv, hashlib, json
from pathlib import Path
ROOT = Path(__file__).resolve().parent
M = json.loads((ROOT / 'MANIFEST.json').read_text())
def sha(data: bytes) -> str: return hashlib.sha256(data).hexdigest()
for rel, meta in M['files'].items():
    p = ROOT / rel
    assert p.is_file(), rel
    data = p.read_bytes()
    assert len(data) == meta['bytes'], rel
    assert sha(data) == meta['sha256'], rel
for p in (ROOT / 'research/checkpoint_v68/source').glob('*.py'):
    compile(p.read_text(), str(p), 'exec')
proof = json.loads((ROOT / 'research/checkpoint_v68/results/v67_selection_proof_before_final.json').read_text())
stored = proof.pop('selection_proof_sha256')
calculated = sha(json.dumps(proof, sort_keys=True, default=list).encode())
assert stored == calculated == '5795fc62a02e8a8ba423eedc2db4cbbcf6d0028c78e247860bf7fa555b78a6e5'
summary = json.loads((ROOT / 'research/checkpoint_v68/results/v67_summary.json').read_text())
assert summary['selection_proof_sha256'] == stored
assert summary['checks']['eligible_before_final'] and not summary['checks']['final_nonzero']
assert summary['prefinal']['liquidations'] == 0 and summary['prefinal']['max_gross'] < 1.08
audit = json.loads((ROOT / 'research/checkpoint_v68/results/v68_summary.json').read_text())
assert audit['checks']['no_liquidations'] and audit['checks']['min_buffer_positive']
assert not audit['checks']['final_nonzero']
reproof = json.loads((ROOT / 'docs/checkpoints/v68/V67_V68_LOCAL_REPROOF.json').read_text())
assert reproof['status'] == 'passed' and not reproof['failed']
ledger = list(csv.DictReader((ROOT / 'docs/checkpoints/v68/RESEARCH_LEDGER_V1_V68.csv').open()))
assert any(x['version'] == '61' and 'incomplete' in x['status'] for x in ledger)
assert any(x['version'] == '54a' for x in ledger) and any(x['version'] == '54b' for x in ledger)
assert all(any(x['version'] == str(v) for x in ledger) for v in range(1, 54))
print('Research checkpoint V68 lean repository verifier passed')
