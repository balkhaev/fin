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
V138_COMMIT = "9e5b4c2d8324ece94a2b28cfe60137e6c0a79eb5"
V138_BOOTSTRAP_ROOT = ".bootstrap_v138"
V138_ARCHIVE_SHA256 = "5a0fbbfec2433be17e3dba0fc6ff6f22cc9d3f59c3943c71cc736e822d6232ef"
V87B_COMMIT = "3faf7794c8fa740ce601b4c040da24d627d2501d"
V87B_PARTS = {
    ".bootstrap_v87b/payload/part_000.txt": "9776599e324046f4b09cc6f7cfcd118bd0b90ad5",
    ".bootstrap_v87b/payload/part_001.txt": "183af2edd00f1b553730e8b69fe0c582d3e6231b",
}
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


def git_object_sha(commit: str, path: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", f"{commit}:{path}"],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def load_v138_archive() -> tuple[bytes, dict[str, Any]]:
    manifest_path = f"{V138_BOOTSTRAP_ROOT}/manifest.json"
    manifest = json.loads(git_show(V138_COMMIT, manifest_path).decode("utf-8"))
    if manifest.get("format") != "base64(tar.xz)":
        raise RuntimeError(f"unexpected V138 transport format: {manifest.get('format')!r}")

    encoded_parts: list[bytes] = []
    for item in manifest["parts"]:
        part_name = str(item["path"])
        part_path = f"{V138_BOOTSTRAP_ROOT}/payload/{part_name}.txt"
        raw = git_show(V138_COMMIT, part_path).rstrip(b"\r\n")
        if len(raw) != int(item["bytes"]):
            raise RuntimeError(f"V138 part length mismatch for {part_path}")
        if sha256_bytes(raw) != item["sha256"]:
            raise RuntimeError(f"V138 part hash mismatch for {part_path}")
        encoded_parts.append(raw)

    encoded = b"".join(encoded_parts)
    if len(encoded) != int(manifest["encoded_bytes"]):
        raise RuntimeError("V138 encoded payload length mismatch")
    if sha256_bytes(encoded) != manifest["encoded_sha256"]:
        raise RuntimeError("V138 encoded payload hash mismatch")

    archive = base64.b64decode(encoded, validate=True)
    if len(archive) != int(manifest["archive_bytes"]):
        raise RuntimeError("V138 archive length mismatch")
    actual_archive_hash = sha256_bytes(archive)
    if actual_archive_hash != manifest["archive_sha256"]:
        raise RuntimeError("V138 archive hash mismatch against transport manifest")
    if actual_archive_hash != V138_ARCHIVE_SHA256:
        raise RuntimeError("V138 archive hash mismatch against runtime recovery contract")
    return archive, {
        "name": "v138_compact_transport",
        "source_commit": V138_COMMIT,
        "archive_sha256": actual_archive_hash,
        "archive_bytes": len(archive),
        "encoded_sha256": sha256_bytes(encoded),
        "part_git_blob_shas": {
            f"{V138_BOOTSTRAP_ROOT}/payload/{item['path']}.txt":
                git_object_sha(V138_COMMIT, f"{V138_BOOTSTRAP_ROOT}/payload/{item['path']}.txt")
            for item in manifest["parts"]
        },
    }


def load_v87b_archive() -> tuple[bytes, dict[str, Any]]:
    encoded_parts: list[bytes] = []
    actual_blob_shas: dict[str, str] = {}
    for path, expected_blob_sha in V87B_PARTS.items():
        actual_blob_sha = git_object_sha(V87B_COMMIT, path)
        if actual_blob_sha != expected_blob_sha:
            raise RuntimeError(
                f"V87 canonical transport blob mismatch for {path}: "
                f"{actual_blob_sha} != {expected_blob_sha}"
            )
        actual_blob_shas[path] = actual_blob_sha
        encoded_parts.append(b"".join(git_show(V87B_COMMIT, path).split()))

    encoded = b"".join(encoded_parts)
    archive = base64.b64decode(encoded, validate=True)
    # Opening the archive below is also an xz/tar integrity check.
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:xz") as tar:
        file_count = sum(member.isfile() or member.islnk() or member.issym() for member in tar.getmembers())
    return archive, {
        "name": "v87_canonical_transport",
        "source_commit": V87B_COMMIT,
        "archive_sha256": sha256_bytes(archive),
        "archive_bytes": len(archive),
        "encoded_sha256": sha256_bytes(encoded),
        "part_git_blob_shas": actual_blob_shas,
        "archive_file_or_link_count": int(file_count),
    }


def validate_member_path(value: str, label: str) -> PurePosixPath:
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError(f"unsafe {label} in historical archive: {value!r}")
    return relative


def safe_extract(archive: bytes, destination: Path) -> tuple[list[str], list[str]]:
    """Dereference safe in-archive links into ordinary files; never create links/devices."""
    extracted: list[str] = []
    skipped: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:xz") as tar:
        for member in tar.getmembers():
            relative = validate_member_path(member.name, "member path")
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if member.isdev() or member.isfifo():
                skipped.append(member.name)
                continue
            if member.issym() or member.islnk():
                validate_member_path(member.linkname, "link target")
            if not (member.isfile() or member.issym() or member.islnk()):
                skipped.append(member.name)
                continue
            source = tar.extractfile(member)
            if source is None:
                skipped.append(member.name)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())
            extracted.append(member.name)
    return extracted, skipped


def matching_files(extracted_root: Path, expected_path: str, expected_sha: str) -> list[Path]:
    exact = extracted_root / expected_path
    if exact.is_file() and sha256_file(exact) == expected_sha:
        return [exact]
    return sorted(
        (
            path
            for path in extracted_root.rglob(Path(expected_path).name)
            if path.is_file() and sha256_file(path) == expected_sha
        ),
        key=lambda path: (len(path.parts), str(path)),
    )


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
    profile["source_paths"].update(EXPECTED_TARGETS)
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
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    archive_sources = [load_v138_archive(), load_v87b_archive()]
    resolved: dict[str, dict[str, Any]] = {}
    source_diagnostics: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="v75-history-recovery-") as temporary:
        temporary_root = Path(temporary)
        extracted_roots: list[tuple[Path, dict[str, Any]]] = []
        for number, (archive, metadata) in enumerate(archive_sources):
            extracted_root = temporary_root / f"source_{number}"
            extracted_root.mkdir(parents=True)
            extracted, skipped = safe_extract(archive, extracted_root)
            metadata = {
                **metadata,
                "extracted_regularized_file_count": len(extracted),
                "skipped_member_count": len(skipped),
                "skipped_members_sample": skipped[:20],
            }
            source_diagnostics.append(metadata)
            extracted_roots.append((extracted_root, metadata))

        for destination_text, expected_sha in EXPECTED_TARGETS.items():
            candidates: list[tuple[Path, Path, dict[str, Any]]] = []
            for extracted_root, metadata in extracted_roots:
                for candidate in matching_files(extracted_root, destination_text, expected_sha):
                    candidates.append((candidate, extracted_root, metadata))
            if not candidates:
                raise RuntimeError(
                    f"no pinned historical transport contains {destination_text} with sha256 {expected_sha}"
                )
            source, extracted_root, metadata = sorted(
                candidates,
                key=lambda item: (len(item[0].parts), str(item[0])),
            )[0]
            destination = ROOT / destination_text
            copy_verified(source, destination, expected_sha, args.write)
            resolved[destination_text] = {
                "historical_transport": metadata["name"],
                "historical_source_commit": metadata["source_commit"],
                "historical_archive_path": str(source.relative_to(extracted_root)),
                "sha256": expected_sha,
                "bytes": source.stat().st_size,
                "matching_copies": len(candidates),
                "written": bool(args.write),
            }

    update_source_registry(args.write)
    recovery_record = {
        "program": "runtime-v1-v75-direct-materialization",
        "historical_transports": source_diagnostics,
        "transport_fragments_committed_to_runtime_branch": False,
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
