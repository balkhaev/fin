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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "3faf7794c8fa740ce601b4c040da24d627d2501d"
PARTS = (
    ".bootstrap_v87b/payload/part_000.txt",
    ".bootstrap_v87b/payload/part_001.txt",
)


def git_show(path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{COMMIT}:{path}"], cwd=ROOT)


def describe(name: str, payload: bytes) -> dict[str, object]:
    result: dict[str, object] = {
        "name": name,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "first_32_hex": payload[:32].hex(),
        "starts_xz": payload.startswith(b"\xfd7zXZ\x00"),
        "starts_zip": payload.startswith(b"PK"),
    }
    candidates = [("payload", payload)]
    try:
        expanded = lzma.decompress(payload)
        result["lzma_bytes"] = len(expanded)
        result["lzma_sha256"] = hashlib.sha256(expanded).hexdigest()
        result["lzma_first_64_hex"] = expanded[:64].hex()
        result["lzma_first_64_repr"] = repr(expanded[:64])
        result["lzma_ustar_offset"] = expanded.find(b"ustar")
        result["lzma_zip_magic"] = expanded.startswith(b"PK")
        result["lzma_cpio_magic"] = expanded[:6] in {b"070701", b"070702", b"070707"}
        candidates.append(("lzma", expanded))
    except Exception as error:  # diagnostic only
        result["lzma_error"] = repr(error)

    archive_trials: list[dict[str, object]] = []
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
        archive_trials.append(trial)
    result["archive_trials"] = archive_trials
    return result


def main() -> int:
    raw_parts = [b"".join(git_show(path).split()) for path in PARTS]
    candidates: list[tuple[str, bytes]] = []
    candidates.append(("concatenated_base64", base64.b64decode(b"".join(raw_parts), validate=True)))
    individually_decoded: list[bytes] = []
    individual_error = None
    for raw in raw_parts:
        try:
            individually_decoded.append(base64.b64decode(raw, validate=True))
        except Exception as error:
            individual_error = repr(error)
            break
    if individual_error is None:
        candidates.append(("per_part_decode_then_concat", b"".join(individually_decoded)))

    output = {
        "source_commit": COMMIT,
        "part_lengths": [len(value) for value in raw_parts],
        "part_suffixes": [value[-16:].decode("ascii", errors="replace") for value in raw_parts],
        "individual_decode_error": individual_error,
        "candidates": [describe(name, payload) for name, payload in candidates],
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
