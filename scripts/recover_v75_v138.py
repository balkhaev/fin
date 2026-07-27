#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_COMMIT = "9e5b4c2d8324ece94a2b28cfe60137e6c0a79eb5"
BOOTSTRAP_ROOT = ".bootstrap_v138"
EXPECTED_ARCHIVE_SHA256 = "5a0fbbfec2433be17e3dba0fc6ff6f22cc9d3f59c3943c71cc736e822d6232ef"
EXPECTED_TARGETS = {
    "research/active_v131_v138/dependencies/atlas/source/v75_operational_feedback_engine.py":
        "3303cd91511bca0be81ade21272e1e8ba6f76adf826d238e9c4bd7cbe78f69fc",
    "research/active_v131_v138/results/v136/v75_original_stress_equity.csv":
        "0f578a56132ec9858031cc6ad5cc919f732e66990625c2fdd6ff91143e44956b",
    "research/active_v131_v138/results/v136/v75_original_annual_returns.csv":
        "e3de37108b5d459ad9f8324388a3a34571f29c5c594a77d82477cb812c8e0d25",
}
REGISTRY_PATH = ROOT / "docs/checkpoints/runtime-v1/SOURCE_REGISTRY.json"
RECOVERY_RECORD_PATH = ROOT / "docs/checkpoints/runtime-v1/V75_RECOVERY_PROVENANCE.json"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def git_show(commit: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def load_historical_archive(commit: str) -> tuple[bytes, dict[str, Any]]:
    manifest_path = f"{BOOTSTRAP_ROOT}/manifest.json"
    manifest = json.loads(git_show(commit, manifest_path).decode("utf-8"))
    if manifest.get("format") != "base64(tar.xz)":
        raise RuntimeError(f"unexpected historical transport format: {manifest.get('format')!r}")

    encoded_parts: list[bytes] = []
    for item in manifest["parts"]:
        part_name = str(item["path"])
        part_path = f"{BOOTSTRAP_ROOT}/payload/{part_name}.txt"
        raw = git_show(commit, part_path).rstrip(b"\r\n")
        if len(raw) != int(item["bytes"]):
            raise RuntimeError(
                f"historical part length mismatch for {part_path}: {len(raw)} != {item['bytes']}"
            )
        actual = sha256_bytes(raw)
        if actual != item["sha256"]:
            raise RuntimeError(
                f"historical part hash mismatch for {part_path}: {actual} != {item['sha256']}"
            )
        encoded_parts.append(raw)

    encoded = b"".join(encoded_parts)
    if len(encoded) != int(manifest["encoded_bytes"]):
        raise RuntimeError("historical encoded payload length mismatch")
    if sha256_bytes(encoded) != manifest["encoded_sha256"]:
        raise RuntimeError("historical encoded payload hash mismatch")

    archive = base64.b64decode(encoded, validate=True)
    if len(archive) != int(manifest["archive_bytes"]):
        raise RuntimeError("historical archive length mismatch")
    actual_archive_hash = sha256_bytes(archive)
    if actual_archive_hash != manifest["archive_sha256"]:
        raise RuntimeError("historical archive hash mismatch against transport manifest")
    if actual_archive_hash != EXPECTED_ARCHIVE_SHA256:
        raise RuntimeError("historical archive hash mismatch against frozen runtime recovery contract")
    return archive, manifest


def safe_extract(archive: bytes, destination: Path) -> list[str]:
    """Extract only regular files and directories; historical links are ignored."""
    extracted: list[str] = []
    skipped: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:xz") as tar:
        for member in tar.getmembers():
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError(f"unsafe path in historical archive: {member.name!r}")
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                skipped.append(member.name)
                continue
            source = tar.extractfile(member)
            if source is None:
                raise RuntimeError(f"cannot read historical archive member: {member.name!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())
            extracted.append(member.name)
    if skipped:
        print(json.dumps({"skipped_non_regular_archive_members": skipped}, ensure_ascii=False))
    return extracted


def find_by_expected_hash(extracted_root: Path, expected_path: str, expected_sha: str) -> Path:
    exact = extracted_root / expected_path
    if exact.is_file() and sha256_file(exact) == expected_sha:
        return exact

    matches = [
        path
        for path in extracted_root.rglob(Path(expected_path).name)
        if path.is_file() and sha256_file(path) == expected_sha
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one historical file for {expected_path} with sha256 {expected_sha}; "
            f"found {len(matches)}"
        )
    return matches[0]


def copy_verified(source: Path, destination: Path, expected_sha: str, write: bool) -> None:
    if destination.exists():
        actual = sha256_file(destination)
        if actual != expected_sha:
            raise RuntimeError(
                f"refusing to overwrite mismatched committed file {destination}: {actual} != {expected_sha}"
            )
        return
    if not write:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    actual = sha256_file(destination)
    if actual != expected_sha:
        raise RuntimeError(f"copied file hash mismatch for {destination}: {actual} != {expected_sha}")


def update_source_registry(write: bool) -> dict[str, Any]:
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    profile = registry["profiles"]["v75_atlas_nx"]
    profile["source_paths"].update(
        {
            "research/active_v131_v138/dependencies/atlas/source/v75_operational_feedback_engine.py":
                EXPECTED_TARGETS[
                    "research/active_v131_v138/dependencies/atlas/source/v75_operational_feedback_engine.py"
                ],
            "research/active_v131_v138/results/v136/v75_original_stress_equity.csv":
                EXPECTED_TARGETS[
                    "research/active_v131_v138/results/v136/v75_original_stress_equity.csv"
                ],
            "research/active_v131_v138/results/v136/v75_original_annual_returns.csv":
                EXPECTED_TARGETS[
                    "research/active_v131_v138/results/v136/v75_original_annual_returns.csv"
                ],
        }
    )
    profile["provenance_complete"] = True
    profile.pop("unmaterialized_requirements", None)
    profile["runtime_blocker"] = (
        "exact V75 source and equity fixture are materialized; M2 still requires a committed daily target "
        "fixture and exact runtime target regression"
    )
    registry["checkpoint_status"] = "v75_source_materialized_v28_provenance_still_blocked"
    if write:
        REGISTRY_PATH.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    return registry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-commit", default=DEFAULT_SOURCE_COMMIT)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    archive, transport_manifest = load_historical_archive(args.source_commit)
    with tempfile.TemporaryDirectory(prefix="v75-v138-recovery-") as temporary:
        extracted_root = Path(temporary)
        extracted_files = safe_extract(archive, extracted_root)
        resolved: dict[str, dict[str, Any]] = {}
        for destination_text, expected_sha in EXPECTED_TARGETS.items():
            source = find_by_expected_hash(extracted_root, destination_text, expected_sha)
            destination = ROOT / destination_text
            copy_verified(source, destination, expected_sha, args.write)
            resolved[destination_text] = {
                "historical_archive_path": str(source.relative_to(extracted_root)),
                "sha256": expected_sha,
                "bytes": source.stat().st_size,
                "written": bool(args.write),
            }

    update_source_registry(args.write)
    recovery_record = {
        "program": "runtime-v1-v75-direct-materialization",
        "source_commit": args.source_commit,
        "source_transport_manifest_path": f"{BOOTSTRAP_ROOT}/manifest.json",
        "transport_archive_sha256": transport_manifest["archive_sha256"],
        "transport_archive_bytes": transport_manifest["archive_bytes"],
        "transport_encoded_sha256": transport_manifest["encoded_sha256"],
        "transport_fragments_committed_to_runtime_branch": False,
        "historical_archive_file_count": len(extracted_files),
        "recovered_targets": resolved,
        "write_mode": bool(args.write),
        "safety": {
            "strategy_parameters_changed": False,
            "live_execution_available": False,
            "live_ready": False,
            "real_leverage_authorized": False,
        },
    }
    if args.write:
        RECOVERY_RECORD_PATH.parent.mkdir(parents=True, exist_ok=True)
        RECOVERY_RECORD_PATH.write_text(
            json.dumps(recovery_record, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )

    print(json.dumps(recovery_record, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
