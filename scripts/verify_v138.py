#!/usr/bin/env python3
from __future__ import annotations
import json,py_compile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/"research"/"active_v131_v138"
for p in (BASE/"source").glob("*.py"):
    py_compile.compile(str(p),doraise=True)
d=json.loads((BASE/"FROZEN_DECISION.json").read_text())
assert d["live_ready"] is False
assert d["real_leverage_authorized"] is False
assert d["decision"]["promoted_candidates"]==[]
for version in ("v132","v134","v136"):
    s=json.loads((BASE/"results"/version/"summary.json").read_text())
    assert s["status"]=="rejected_or_needs_iteration"
manifest=json.loads((BASE/"DATA_REQUIREMENTS.json").read_text())
assert len(manifest["files"])>=17
print("V138 compact repository verification passed")
