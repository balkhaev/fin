#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "v75_operational_feedback_engine.py":
        "3303cd91511bca0be81ade21272e1e8ba6f76adf826d238e9c4bd7cbe78f69fc",
    "v75_original_stress_equity.csv":
        "0f578a56132ec9858031cc6ad5cc919f732e66990625c2fdd6ff91143e44956b",
    "v75_original_annual_returns.csv":
        "e3de37108b5d459ad9f8324388a3a34571f29c5c594a77d82477cb812c8e0d25",
}
PATH_HINTS = (
    "v75_operational_feedback_engine",
    "v75_original_stress_equity",
    "v75_original_annual_returns",
    "v75_stress_equity",
    "operational_feedback_engine",
)


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def main() -> int:
    object_lines = run("rev-list", "--objects", "--all").stdout.decode("utf-8", errors="replace").splitlines()
    rows: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()

    for line in object_lines:
        object_id, separator, path = line.partition(" ")
        if not separator or not any(hint in path for hint in PATH_HINTS):
            continue
        key = (object_id, path)
        if key in seen:
            continue
        seen.add(key)
        object_type = run("cat-file", "-t", object_id).stdout.decode().strip()
        if object_type != "blob":
            continue
        value = run("cat-file", "blob", object_id).stdout
        sha256 = hashlib.sha256(value).hexdigest()
        basename = Path(path).name
        expected = TARGETS.get(basename)
        commits = run(
            "log", "--all", "--find-object", object_id, "--format=%H%x09%s", "--", path,
            check=False,
        ).stdout.decode("utf-8", errors="replace").splitlines()
        rows.append(
            {
                "git_blob_sha": object_id,
                "path": path,
                "basename": basename,
                "bytes": len(value),
                "sha256": sha256,
                "expected_sha256": expected,
                "exact_expected_match": bool(expected and sha256 == expected),
                "introducing_or_removing_commits": commits[:20],
            }
        )

    refs = run("for-each-ref", "--format=%(refname)", "refs/remotes/origin").stdout.decode().splitlines()
    output = {
        "reachable_object_count": len(object_lines),
        "remote_ref_count": len(refs),
        "remote_refs": refs,
        "path_matches": sorted(rows, key=lambda row: (str(row["basename"]), str(row["path"]))),
        "all_expected_targets_found": all(
            any(row["exact_expected_match"] and row["basename"] == basename for row in rows)
            for basename in TARGETS
        ),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
