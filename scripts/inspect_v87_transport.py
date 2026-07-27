#!/usr/bin/env python3
from __future__ import annotations

import base64
import bz2
import gzip
import hashlib
import io
import json
import lzma
import subprocess
import tarfile
import zipfile
import zlib
from collections import defaultdict
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
BASE = "f99747b9ee547848c2ecf4ad49ea29e1380ab9ec"
HEAD = "3faf7794c8fa740ce601b4c040da24d627d2501d"
BOOTSTRAP_ROOTS = (".bootstrap_v87", ".bootstrap_v87b")


def run_text(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def git_show(commit: str, path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=ROOT)


def archive_trial(label: str, candidate: bytes) -> dict[str, object]:
    trial: dict[str, object] = {
        "candidate": label,
        "bytes": len(candidate),
        "sha256": hashlib.sha256(candidate).hexdigest(),
        "first_32_hex": candidate[:32].hex(),
        "first_64_repr": repr(candidate[:64]),
        "starts_xz": candidate.startswith(b"\xfd7zXZ\x00"),
        "starts_zip": candidate.startswith(b"PK"),
        "starts_gzip": candidate.startswith(b"\x1f\x8b"),
        "starts_bzip2": candidate.startswith(b"BZh"),
        "starts_zlib_common": candidate[:2] in {b"\x78\x01", b"\x78\x5e", b"\x78\x9c", b"\x78\xda"},
        "ustar_offset": candidate.find(b"ustar"),
        "cpio_magic": candidate[:6] in {b"070701", b"070702", b"070707"},
    }
    try:
        with tarfile.open(fileobj=io.BytesIO(candidate), mode="r:*") as tar:
            members = tar.getmembers()
            trial["tar_ok"] = True
            trial["tar_members"] = len(members)
            trial["tar_sample"] = [member.name for member in members[:40]]
    except Exception as error:
        trial["tar_ok"] = False
        trial["tar_error"] = repr(error)
    trial["zip_ok"] = zipfile.is_zipfile(io.BytesIO(candidate))
    if trial["zip_ok"]:
        with zipfile.ZipFile(io.BytesIO(candidate)) as archive:
            trial["zip_members"] = len(archive.namelist())
            trial["zip_sample"] = archive.namelist()[:40]
    return trial


def describe_payload(payload: bytes) -> dict[str, object]:
    result: dict[str, object] = archive_trial("decoded", payload)
    decompression: list[dict[str, object]] = []
    methods: tuple[tuple[str, Callable[[bytes], bytes]], ...] = (
        ("lzma", lzma.decompress),
        ("zlib", zlib.decompress),
        ("zlib_raw", lambda value: zlib.decompress(value, -zlib.MAX_WBITS)),
        ("gzip", gzip.decompress),
        ("bz2", bz2.decompress),
    )
    for name, method in methods:
        try:
            expanded = method(payload)
            row = archive_trial(name, expanded)
            row["ok"] = True
        except Exception as error:
            row = {"candidate": name, "ok": False, "error": repr(error)}
        decompression.append(row)
    result["decompression_trials"] = decompression
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
                "prefix": raw[:16].decode("ascii", errors="replace"),
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
    decoders: tuple[tuple[str, Callable[[bytes], bytes]], ...] = (
        ("base64", lambda value: base64.b64decode(value, validate=True)),
        ("base85", base64.b85decode),
        ("ascii85", lambda value: base64.a85decode(value, adobe=False)),
        ("ascii85_adobe", lambda value: base64.a85decode(value, adobe=True)),
    )
    decoded_trials: list[dict[str, object]] = []
    for name, decoder in decoders:
        try:
            payload = decoder(encoded)
            decoded_trials.append(
                {"decoder": name, "ok": True, "payload": describe_payload(payload)}
            )
        except Exception as error:
            decoded_trials.append({"decoder": name, "ok": False, "error": repr(error)})
    row["decoded_trials"] = decoded_trials
    return row


def is_useful(inspection: dict[str, object]) -> bool:
    for decoded in inspection.get("decoded_trials", []):
        if not isinstance(decoded, dict) or not decoded.get("ok"):
            continue
        payload = decoded.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if payload.get("tar_ok") or payload.get("zip_ok"):
            return True
        for expanded in payload.get("decompression_trials", []):
            if isinstance(expanded, dict) and expanded.get("ok") and (
                expanded.get("tar_ok") or expanded.get("zip_ok")
            ):
                return True
    return False


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
            if is_useful(inspection):
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
        "successful_archive_candidates": successful_candidates,
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
