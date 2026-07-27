#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "v75_operational_feedback_engine.py": {
        "sha256": "3303cd91511bca0be81ade21272e1e8ba6f76adf826d238e9c4bd7cbe78f69fc",
        "bytes": 17495,
    },
    "v75_original_stress_equity.csv": {
        "sha256": "0f578a56132ec9858031cc6ad5cc919f732e66990625c2fdd6ff91143e44956b",
        "bytes": 522305,
    },
    "v75_original_annual_returns.csv": {
        "sha256": "e3de37108b5d459ad9f8324388a3a34571f29c5c594a77d82477cb812c8e0d25",
        "bytes": 167,
    },
}
PATH_HINTS = (
    "v75_operational_feedback_engine",
    "v75_original_stress_equity",
    "v75_original_annual_returns",
    "v75_stress_equity",
    "operational_feedback_engine",
)


def run(
    *args: str,
    check: bool = True,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def commits_for_object(object_id: str) -> list[str]:
    return run(
        "log", "--all", "--find-object", object_id, "--format=%H%x09%s",
        check=False,
    ).stdout.decode("utf-8", errors="replace").splitlines()[:20]


def main() -> int:
    object_lines = run("rev-list", "--objects", "--all").stdout.decode(
        "utf-8", errors="replace"
    ).splitlines()
    paths_by_object: dict[str, set[str]] = defaultdict(set)
    object_ids: list[str] = []
    seen_objects: set[str] = set()
    for line in object_lines:
        object_id, separator, path = line.partition(" ")
        if object_id not in seen_objects:
            seen_objects.add(object_id)
            object_ids.append(object_id)
        if separator:
            paths_by_object[object_id].add(path)

    batch_input = ("\n".join(object_ids) + "\n").encode("ascii")
    check_output = run(
        "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        input_bytes=batch_input,
    ).stdout.decode("ascii").splitlines()
    metadata: dict[str, tuple[str, int]] = {}
    for line in check_output:
        object_id, object_type, object_size = line.split()
        metadata[object_id] = (object_type, int(object_size))

    target_sizes = {int(value["bytes"]) for value in TARGETS.values()}
    candidate_rows: list[dict[str, object]] = []
    exact_matches: list[dict[str, object]] = []
    for object_id in object_ids:
        object_type, object_size = metadata[object_id]
        if object_type != "blob" or object_size not in target_sizes:
            continue
        value = run("cat-file", "blob", object_id).stdout
        sha256 = hashlib.sha256(value).hexdigest()
        matching_targets = [
            basename
            for basename, expected in TARGETS.items()
            if int(expected["bytes"]) == object_size and expected["sha256"] == sha256
        ]
        row = {
            "git_blob_sha": object_id,
            "bytes": object_size,
            "sha256": sha256,
            "paths": sorted(paths_by_object.get(object_id, set())),
            "matching_targets": matching_targets,
            "exact_expected_match": bool(matching_targets),
            "introducing_or_removing_commits": commits_for_object(object_id),
        }
        candidate_rows.append(row)
        if matching_targets:
            exact_matches.append(row)

    hinted_rows: list[dict[str, object]] = []
    for object_id, paths in paths_by_object.items():
        hinted_paths = sorted(path for path in paths if any(hint in path for hint in PATH_HINTS))
        if not hinted_paths:
            continue
        object_type, object_size = metadata[object_id]
        if object_type != "blob":
            continue
        value = run("cat-file", "blob", object_id).stdout
        sha256 = hashlib.sha256(value).hexdigest()
        hinted_rows.append(
            {
                "git_blob_sha": object_id,
                "paths": hinted_paths,
                "bytes": object_size,
                "sha256": sha256,
                "matching_targets": [
                    basename
                    for basename, expected in TARGETS.items()
                    if expected["sha256"] == sha256
                ],
                "introducing_or_removing_commits": commits_for_object(object_id),
            }
        )

    refs = run("for-each-ref", "--format=%(refname)", "refs/remotes/origin").stdout.decode().splitlines()
    found_target_names = {
        name for row in exact_matches for name in row["matching_targets"]
    }
    output = {
        "reachable_object_count": len(object_ids),
        "remote_ref_count": len(refs),
        "remote_refs": refs,
        "candidate_blob_count_by_expected_sizes": len(candidate_rows),
        "candidate_blobs_by_expected_sizes": sorted(
            candidate_rows, key=lambda row: (int(row["bytes"]), str(row["git_blob_sha"]))
        ),
        "path_hint_matches": sorted(
            hinted_rows, key=lambda row: (str(row["paths"]), str(row["git_blob_sha"]))
        ),
        "exact_target_matches": exact_matches,
        "found_target_names": sorted(found_target_names),
        "missing_target_names": sorted(set(TARGETS) - found_target_names),
        "all_expected_targets_found": set(TARGETS) == found_target_names,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
