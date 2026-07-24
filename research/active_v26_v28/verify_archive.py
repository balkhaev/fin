#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import hashlib,json,py_compile
ROOT=Path(__file__).resolve().parent

def git_blob_sha(data:bytes)->str:
    return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()

engine=ROOT/'v28_exact8h_engine.py';runner=ROOT/'v28_run_research.py'
assert git_blob_sha(engine.read_bytes())=='9f2c5edb202a4ee4b185b3927710fe20e985fc32'
assert git_blob_sha(runner.read_bytes())=='8b0c1b835718b9d13c56a3f6c9d084c54583910d'
py_compile.compile(str(engine),doraise=True)
py_compile.compile(str(runner),doraise=True)
s=json.loads((ROOT/'v28_summary.json').read_text())
f=json.loads((ROOT/'v28_frozen_candidate.json').read_text())
prior=json.loads((ROOT/'v26_v27_compact_evidence.json').read_text())
basis=json.loads((ROOT/'delivery_basis_rejection.json').read_text())
assert s['candidate']=='ACTIVE_V28_EXACT8H_BREAKOUT_CARRY_CASH'
assert s['status']=='frozen_paper_forward_candidate'
assert all(s['acceptance_checks'].values())
assert s['stress_full']['annualized_return']>.30
assert s['stress_full']['max_drawdown']>-.30
assert s['stress_full']['annual_turnover']<25
assert s['stress_final_2026h1']['total_return']>0
assert s['funding_audits']['fund60']['full_cagr']>.29
assert s['funding_audits']['fund80_margin125']['final_return']>0
assert s['selection_proof_sha256']=='fcdf6d12bd1ba95374c8866e3a44987e0eea1d187cc2e4100a81f446d20a4828'
assert f['candidate']==s['candidate']
assert f['target_gross_cap']==.85
assert f['selection_excludes_2026h1'] is True
assert f['selection_proof_sha256']==s['selection_proof_sha256']
assert prior['v26']['status']=='frozen_paper_forward_candidate'
assert prior['v27']['status']=='frozen_paper_forward_candidate'
assert prior['v26']['stress_full_cagr']>.26
assert prior['v27']['stress_full_cagr']>.27
assert basis['status']=='rejected_or_needs_iteration'
assert basis['eligible_before_final']==0
assert basis['final_2026h1_opened'] is False
print('Active V26-V28 direct-source integrity passed')
