#!/usr/bin/env python3
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parent
s=json.loads((ROOT/'summary.json').read_text())
p=json.loads((ROOT/'provenance.json').read_text())
assert s['candidate']=='ACTIVE_V38_V39_EXACT8H_SESSION_BREAKOUT'
assert s['status']=='rejected_or_needs_iteration'
assert s['selected_eligible_before_final'] is False
assert s['selection_excludes_2026h1'] is True
assert s['selected_process']=='leader:breakout:slow'
assert s['prefinal']['prefinal_cagr']<0.08
assert s['stress_final_2026h1']['total_return']<0
assert s['strict_costs']['severe']['full_cagr']<0
assert s['selection_proof_sha256']==p['selection_proof_sha256']
assert p['public_compute_commit']=='8c3969d1b8fbaa42a4cd7e7e97d27ec50edac3c8'
assert p['artifact']['digest']=='sha256:f70cfbdd681a88d46b3a4de7a0464c374244befeef74b9182420abc3a9631f53'
assert len(p['source_files'])==6
print('Active V38-V39 rejection archive integrity passed')
