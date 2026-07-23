#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reconstruct() -> Path:
    parts = sorted((ROOT / "payload").glob("part_*.b64"))
    if len(parts) != MANIFEST["parts"]:
        raise SystemExit(f"payload part count mismatch: {len(parts)}")
    encoded = b"".join(part.read_bytes().strip() for part in parts)
    if len(encoded) != MANIFEST["encoded_bytes"] or sha256(encoded) != MANIFEST["encoded_sha256"]:
        raise SystemExit("encoded payload integrity failure")
    archive = base64.b64decode(encoded, validate=True)
    if len(archive) != MANIFEST["archive_bytes"] or sha256(archive) != MANIFEST["archive_sha256"]:
        raise SystemExit("archive payload integrity failure")
    generated = ROOT / "generated"
    generated.mkdir(exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:bz2") as tar:
        names = [member.name for member in tar.getmembers()]
        if any(Path(name).is_absolute() or ".." in Path(name).parts for name in names):
            raise SystemExit("unsafe archive member")
        tar.extractall(generated, filter="data")
    for name, expected in MANIFEST["source_files"].items():
        path = generated / name
        if not path.exists() or sha256(path.read_bytes()) != expected:
            raise SystemExit(f"source hash mismatch: {name}")
    return generated


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--extract-only", action="store_true")
    known, rest = parser.parse_known_args()
    generated = reconstruct()
    if known.extract_only:
        print(generated)
        return 0
    return subprocess.call([sys.executable, str(generated / "run_research.py"), *rest])


if __name__ == "__main__":
    raise SystemExit(main())
