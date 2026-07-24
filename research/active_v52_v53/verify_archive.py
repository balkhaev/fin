#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import io
import json
import py_compile
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUNDLE = ROOT / "bundle"

manifest = json.loads((BUNDLE / "manifest.json").read_text())
parts: list[bytes] = []
for item in manifest["parts"]:
    path = BUNDLE / item["path"]
    data = path.read_bytes()
    assert len(data) == item["bytes"], (path, len(data), item["bytes"])
    assert hashlib.sha256(data).hexdigest() == item["sha256"], path
    parts.append(data)
encoded = b"".join(parts)
assert len(encoded) == manifest["encoded_bytes"]
assert hashlib.sha256(encoded).hexdigest() == manifest["encoded_sha256"]
archive = base64.b64decode(encoded, validate=True)
assert len(archive) == manifest["archive_bytes"]
assert hashlib.sha256(archive).hexdigest() == manifest["archive_sha256"]

with tempfile.TemporaryDirectory() as td:
    out = Path(td)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:bz2") as tf:
        members = tf.getmembers()
        assert all(not Path(m.name).is_absolute() and ".." not in Path(m.name).parts for m in members)
        tf.extractall(out, filter="data")

    v52_source = out / "source" / "run_v52.py"
    v53_source = out / "source" / "run_v53.py"
    assert hashlib.sha256(v52_source.read_bytes()).hexdigest() == manifest["v52_source_sha256"]
    assert hashlib.sha256(v53_source.read_bytes()).hexdigest() == manifest["v53_source_sha256"]
    py_compile.compile(str(v52_source), doraise=True)
    py_compile.compile(str(v53_source), doraise=True)

    v52 = json.loads((out / "v52" / "summary.json").read_text())
    v53 = json.loads((out / "v53" / "summary.json").read_text())
    proof = json.loads((out / "v52" / "selection_proof_before_final.json").read_text())

    assert v52["candidate"] == "ACTIVE_V52_DIVERSIFIED_ONCHAIN_BASE"
    assert v52["status"] == "historical_risk_candidate_needs_nonzero_forward"
    assert v52["leader"] == "exchange75_valuation25"
    assert v52["eligible_before_final"] is True
    assert v52["selection_proof_sha256"] == "4cb6808336fb7951276b71a13e08dcd8fb4cd4c5c449de5fcbda8413889bcee1"
    assert proof["selection_proof_sha256"] == v52["selection_proof_sha256"]
    assert v52["prefinal"]["annualized_return"] > 0.16
    assert v52["prefinal"]["max_drawdown"] > -0.25
    assert v52["prefinal"]["sharpe"] > 0.96
    assert v52["prefinal"]["annual_turnover"] < 2.70
    assert v52["concentration"]["post_2020_cagr"] > 0.077
    assert v52["concentration"]["best_positive_year_log_share"] < 0.55
    assert v52["concentration"]["worst_leave_one_year_out_cagr"] > 0.077
    assert v52["strict_costs"]["catastrophic"]["full"]["annualized_return"] > 0.11
    assert v52["stress_final"]["average_gross"] == 0.0
    assert v52["stress_final"]["total_return"] == 0.0

    assert v53["candidate"] == "ACTIVE_V53_IMMUTABLE_V52_ROBUSTNESS_AUDIT"
    assert v53["status"] == "historically_robust_but_forward_unproven"
    assert v53["parameters_changed"] is False
    assert v53["exact_v52"]["base_weights"] == {"exchange_pressure": 0.75, "valuation_cycle": 0.25}
    checks = v53["checks"]
    assert checks["all_lag_prefinal_positive"] is True
    assert checks["publication_lag_cagr_floor_gt_10pct"] is True
    assert checks["execution_delay_cagr_floor_gt_10pct"] is True
    assert checks["post_2020_cagr_gt_6pct"] is True
    assert checks["best_positive_year_log_share_lt_60pct"] is True
    assert checks["worst_leave_one_year_out_cagr_gt_5pct"] is True
    assert checks["reverse_has_nonzero_exposure"] is False
    assert checks["final_has_nonzero_exposure"] is False
    assert v53["publication_lag_cagr_floor"] > 0.148
    assert v53["execution_delay_cagr_floor"] > 0.161
    assert v53["contribution"]["worst_leave_one_year_out_cagr"] > 0.059

    assert (out / "v52" / "workflow_research_outcome.txt").read_text().strip() == "success"
    assert (out / "v53" / "workflow_audit_outcome.txt").read_text().strip() == "success"
    assert (out / "v52" / "run_exit_code.txt").read_text().strip() == "0"
    assert (out / "v53" / "run_exit_code.txt").read_text().strip() == "0"

external = json.loads((ROOT / "decision.json").read_text())
assert external["status"] == "historically_robust_but_forward_unproven"
assert external["leader"] == "exchange75_valuation25"
assert external["v53_audit"]["parameters_changed"] is False
assert external["final_2026_ytd"]["average_gross"] == 0.0

print("Active V52-V53 archive verified")
