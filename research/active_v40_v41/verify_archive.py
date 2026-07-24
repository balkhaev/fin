#!/usr/bin/env python3
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parent
s=json.loads((ROOT/'summary.json').read_text())
p=json.loads((ROOT/'provenance.json').read_text())
assert s['candidate']=='ACTIVE_V40_V41_EXACT8H_FLOW_LIQUIDITY'
assert s['status']=='rejected_or_needs_iteration'
assert s['selection_excludes_2026h1'] is True
assert s['promoted_leaders']==[]
assert s['leaders']['flow']['candidate']=='flow:flow_breakout+persistent_flow'
assert s['leaders']['flow']['eligible_before_final'] is False
assert s['leaders']['flow']['stress_full']['annualized_return']<0
assert s['leaders']['flow']['stress_final_2026h1']['total_return']<0
assert s['leaders']['reversal']['candidate']=='reversal:flow_divergence'
assert s['leaders']['reversal']['eligible_before_final'] is False
assert s['leaders']['reversal']['stress_full']['annualized_return']<0
assert s['leaders']['reversal']['stress_final_2026h1']['average_gross']<.001
assert s['selection_proof_sha256']==p['selection_proof_sha256']
assert p['workflow_run']['conclusion']=='success'
assert p['artifact']['digest']=='sha256:54c055eaf4663db2e5781e93e27ce6308ff60eb28e3608fe6a95d074fcb9f759'
assert p['public_compute_commit']=='2773eba1ba00bb30ebd63111890a22a3bded90bc'
assert len(p['source_files'])==6
print('Active V40-V41 rejection archive integrity passed')
