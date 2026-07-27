#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

root = Path(__file__).resolve().parents[2]
source = root / "research" / "active_v317_v324" / "run_research.py"
target = Path(__file__).resolve().parent / "run_research.py"
text = source.read_text()

replacements = (
    (
        'SOURCE = REPO_ROOT / "research" / "active_v301_v308" / "run_research.py"',
        'SOURCE = REPO_ROOT / "research" / "active_v325_v332" / "run_research.py"',
    ),
    ('"v301_exact_coskew_source"', '"v325_corrected_coskew_source"'),
    (
        'CANDIDATE = "ACTIVE_V317_EXACT_COSKEW_BETA_HEDGE"',
        'CANDIDATE = "ACTIVE_V333_CORRECTED_COSKEW_BETA_HEDGE"',
    ),
    (
        'SOURCE_POLICY = "low_systematic_coskewness_l365_k4_r14_beta"',
        'SOURCE_POLICY = "low_systematic_coskewness_l365_k3_r14_beta"',
    ),
    ('LONG_ASSET_COUNT = 4', 'LONG_ASSET_COUNT = 3'),
    ('"source_cycle": "V301-V308",', '"source_cycle": "V325-V332",'),
    (
        'unique V301 promotable process passing every development gate except ',
        'top-ranked corrected V325 process with all development years positive; ',
    ),
    (
        'all-years-positive and cross-sectional short-leg profitability',
        'V325 OOS remained closed',
    ),
    ('root / "V317_V324_DESIGN.json"', 'root / "V333_V340_DESIGN.json"'),
    (
        'print("V317-V324 exact coskewness beta-hedge self-test passed")',
        'print("V333-V340 corrected coskewness beta-hedge self-test passed")',
    ),
)
for old, new in replacements:
    count = text.count(old)
    if count < 1:
        raise SystemExit(f"required source fragment missing: {old!r}")
    text = text.replace(old, new)

proof_needle = '        "source_cycle": "V325-V332",\n'
if proof_needle not in text:
    raise SystemExit("proof source-cycle insertion point missing")
text = text.replace(
    proof_needle,
    proof_needle
    + '        "corrected_coskewness_moment": True,\n'
    + '        "hedge_specification_source": "V317-V324 preregistration",\n',
    1,
)

text = text.replace("V317", "V333").replace("V324", "V340")
text = text.replace(
    '"hedge_specification_source": "V333-V340 preregistration"',
    '"hedge_specification_source": "V317-V324 preregistration"',
)
text = text.replace("v301_oos_opened", "v325_oos_opened")

essential = (
    'ACTIVE_V333_CORRECTED_COSKEW_BETA_HEDGE',
    'active_v325_v332',
    'low_systematic_coskewness_l365_k3_r14_beta',
    'LONG_ASSET_COUNT = 3',
    'corrected_coskewness_moment',
    'hedge_specification_source',
    'V333_V340_DESIGN.json',
)
missing = [fragment for fragment in essential if fragment not in text]
if missing:
    raise SystemExit(f"materialized source missing essentials: {missing}")

target.write_text(text)
print(f"materialized {target} ({len(text)} bytes)")
