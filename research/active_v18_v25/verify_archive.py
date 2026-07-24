#!/usr/bin/env python3
from __future__ import annotations

import base64
import bz2
import csv
import hashlib
import io
import json
import py_compile
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXPECTED_ARCHIVE_SHA256 = "a8d1917fcf0180d075d11799116d95f305ac8a81ace1cb93d7c9043f792f605d"
EXPECTED_ENCODED_SHA256 = "87f6cd0a0da8f963ff1ba29f8e8b9cccd572c527fc3486c8e62a8480a2c4a923"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parts = sorted((ROOT / "code_bundle").glob("part_*.b64"))
    assert len(parts) == 2, f"expected two code bundle parts, found {len(parts)}"
    encoded = b"".join(part.read_bytes().strip() for part in parts)
    assert sha256(encoded) == EXPECTED_ENCODED_SHA256
    archive = base64.b64decode(encoded, validate=True)
    assert sha256(archive) == EXPECTED_ARCHIVE_SHA256

    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:bz2") as bundle:
            names = [member.name for member in bundle.getmembers()]
            assert all(not Path(name).is_absolute() and ".." not in Path(name).parts for name in names)
            bundle.extractall(target, filter="data")
        sources = sorted(target.rglob("*.py"))
        assert len(sources) == 8, f"expected eight source files, found {len(sources)}"
        for source in sources:
            py_compile.compile(str(source), doraise=True)

    with (ROOT / "results" / "candidate_matrix.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["version"] for row in rows] == [f"V{number}" for number in range(18, 26)]
    assert all(row["status"] == "rejected_or_needs_iteration" for row in rows)

    near = json.loads((ROOT / "results" / "best_near_candidate_v24.json").read_text(encoding="utf-8"))
    assert near["candidate"] == "V24_DEEP_DRAWDOWN_CIRCUIT"
    assert near["selected_before_final"]["eligible"] is False
    assert near["stress_full_cagr"] > near["baseline_full_cagr"]
    assert near["selected_before_final"]["worst_extreme_return"] < -0.15

    policy = json.loads((ROOT / "selection_policy.json").read_text(encoding="utf-8"))
    assert policy["required_cagr_improvement_vs_v8"] == 0.015
    assert policy["result"] == "No V18-V25 candidate passed every gate."

    print("V18-V25 archive integrity, compile and rejection checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
