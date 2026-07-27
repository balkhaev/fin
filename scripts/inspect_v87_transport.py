#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import io
import json
import lzma
import subprocess
import tarfile
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "f99747b9ee547848c2ecf4ad49ea29e1380ab9ec"
HEAD = "3faf7794c8fa740ce601b4c040da24d627d2501d"
BOOTSTRAP_ROOTS = (".bootstrap_v87", ".bootstrap_v87b")


def run_text(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def git_show(commit: str, path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=ROOT)


def describe_payload(payload: bytes) -> dict[str, object]:
    result: dict[str, object] = {
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "first_32_hex": payload[:32].hex(),
        "starts_xz": payload.startswith(b"\xfd7zXZ\x00"),
        "starts_zip": payload.startswith(b"PK"),
    }
    candidates = [("payload", payload)]
    try:
        expanded = lzma.decompress(payload)
        result.update(
            {
                "lzma_ok": True,
                "lzma_bytes": len(expanded),
                "lzma_sha256": hashlib.sha256(expanded).hexdigest(),
                "lzma_first_64_hex": expanded[:64].hex(),
                "lzma_first_64_repr": repr(expanded[:64]),
                "lzma_ustar_offset": expanded.find(b"ustar"),
                "lzma_zip_magic": expanded.startswith(b"PK"),
                "lzma_cpio_magic": expanded[:6] in {b"070701", b"070702", b"070707"},
            }
        )
        candidates.append(("lzma", expanded))
    except Exception as error:
        result["lzma_ok"] = False
        result["lzma_error"] = repr(error)

    trials: list[dict[str, object]] = []
    for label, candidate in candidates:
        trial: dict[str, object] = {"candidate": label}
        try:
            with tarfile.open(fileobj=io.BytesIO(candidate), mode="r:*") as tar:
                members = tar.getmembers()
                trial["tar_ok"] = True
                trial["tar_members"] = len(members)
                trial["tar_sample"] = [member.name for member in members[:20]]
        except Exception as error:
            trial["tar_ok"] = False
            trial["tar_error"] = repr(error)
        trial["zip_ok"] = zipfile.is_zipfile(io.BytesIO(candidate))
        trials.append(trial)
    result["archive_trials"] = trials
    return result


def inspect_group(commit: str, paths: list[str]) -> dict[str, object]:
    parts: list[bytes] = []
    part_rows: list[dict[str, object]] = []
    for path in paths:
        raw = b"".join(git_show(commit, path).split())
        parts.append(raw)
        part_rows.append(
            {
                "path": path,
                "git_blob_sha": run_text("rev-parse", f"{commit}:{path}"),
                "encoded_bytes": len(raw),
                "suffix": raw[-16:].decode("ascii", errors="replace"),
            }
        )
    encoded = b"".join(parts)
    row: dict[str, object] = {
        "paths": paths,
        "parts": part_rows,
        "encoded_bytes": len(encoded),
        "encoded_sha256": hashlib.sha256(encoded).hexdigest(),
    }
    try:
        row["decoded"] = describe_payload(base64.b64decode(encoded, validate=True))
    except Exception as error:
        row["decode_error"] = repr(error)
    return row


def main() -> int:
    commits = run_text("rev-list", "--reverse", f"{BASE}..{HEAD}").splitlines()
    history: list[dict[str, object]] = []
    successful_candidates: list[dict[str, object]] = []

    for commit in commits:
        tree_paths = run_text(
            "ls-tree", "-r", "--name-only", commit, "--", *BOOTSTRAP_ROOTS
        ).splitlines()
        payload_paths = [path for path in tree_paths if "/payload/" in path and path.endswith(".txt")]
        grouped: dict[str, list[str]] = defaultdict(list)
        for path in payload_paths:
            grouped[path.split("/", 1)[0]].append(path)

        groups: dict[str, object] = {}
        for root, paths in sorted(grouped.items()):
            inspection = inspect_group(commit, sorted(paths))
            groups[root] = inspection
            decoded = inspection.get("decoded", {})
            if isinstance(decoded, dict):
                trials = decoded.get("archive_trials", [])
                if decoded.get("lzma_ok") or any(
                    isinstance(trial, dict) and (trial.get("tar_ok") or trial.get("zip_ok"))
                    for trial in trials
                ):
                    successful_candidates.append(
                        {"commit": commit, "root": root, "inspection": inspection}
                    )

        changed = run_text("diff-tree", "--no-commit-id", "--name-status", "-r", commit)
        history.append(
            {
                "commit": commit,
                "message": run_text("log", "-1", "--format=%s", commit),
                "changed_files": changed.splitlines(),
                "bootstrap_tree_paths": tree_paths,
                "groups": groups,
            }
        )

    output = {
        "base": BASE,
        "head": HEAD,
        "commit_count": len(commits),
        "history": history,
        "successful_or_decompressible_candidates": successful_candidates,
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
