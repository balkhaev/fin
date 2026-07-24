#!/usr/bin/env python3
from __future__ import annotations
import hashlib,json,py_compile,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent
manifest=json.loads((ROOT/'source'/'manifest.json').read_text())
parts=[]
for item in manifest['parts']:
    path=ROOT/'source'/item['path'];data=path.read_bytes()
    assert len(data)==item['bytes']
    assert hashlib.sha256(data).hexdigest()==item['sha256']
    parts.append(data)
source=b''.join(parts)
assert len(source)==manifest['source_bytes']
assert hashlib.sha256(source).hexdigest()==manifest['source_sha256']
with tempfile.TemporaryDirectory() as td:
    path=Path(td)/'run_v51.py';path.write_bytes(source);py_compile.compile(str(path),doraise=True)
summary=json.loads((ROOT/'summary.json').read_text())
assert summary['candidate']=='ACTIVE_V51_EXACT_V50_ROBUSTNESS_AUDIT'
assert summary['status']=='historical_concentration_or_latency_concern'
assert summary['parameters_changed'] is False
checks=summary['audit_checks']
assert checks['all_publication_and_execution_audits_prefinal_positive'] is True
assert checks['publication_lag_prefinal_cagr_floor_gt_8pct'] is True
assert checks['execution_delay_prefinal_cagr_floor_gt_8pct'] is True
assert checks['post_2020_cagr_gt_5pct'] is False
assert checks['best_positive_year_log_share_lt_60pct'] is False
assert checks['final_has_nonzero_exposure'] is False
assert summary['contribution']['best_positive_year_log_growth_share']>0.70
assert summary['exact_metrics']['final_average_gross']==0.0
print('Active V51 archive verified')
