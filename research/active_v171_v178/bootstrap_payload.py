#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import io
import shutil
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TRANSPORT = ROOT / ".transport"
ARCHIVE_SHA256 = "088663e9dd6b0472463443c0273aa5b165983a3c2710cceff693cc6ffb334888"
EXPECTED = {
    "README.md": "89126133abd4118c7ffbb2e5cd66047940cc1301b1992bf3b6a73bafb66d80c5",
    "V171_V178_DESIGN.json": "5e70508ff40f3e1ec85eecccae4395f3acaef3697cffa161541bee63f3fa5e5a",
    "config.py": "095fb8e26a9400819c26bb051b9e588316f3f9d88c96899ed33432f68965ad11",
    "data.py": "4aec225f11c1cae39407f3210efa9aca1151be0760b89e4673e1736f396963a6",
    "engine.py": "1aec7805a3e13ebda086b793a14a664b42f9d82d986706d1d8e52b507336632a",
    "run_research.py": "851e68f0e97f0b126b11c2fec11af2fc0a73b2fc1d316f490e13a176f4e3da10",
    "requirements.txt": "39dbeb92512a19992fe7c27e816a62219b067e0220281a2019a7ca3364c0d52e",
}


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def main() -> int:
    parts = sorted(TRANSPORT.glob("part_*.b85"))
    if not parts:
        raise SystemExit("missing V171-V178 transport parts")
    encoded = "".join(path.read_text().strip() for path in parts)
    archive = base64.b85decode(encoded.encode("ascii"))
    if digest(archive) != ARCHIVE_SHA256:
        raise SystemExit("V171-V178 archive checksum mismatch")

    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        members = bundle.getmembers()
        names = {member.name for member in members}
        if names != set(EXPECTED):
            raise SystemExit(f"unexpected archive members: {sorted(names)}")
        for member in members:
            if member.isdir() or member.issym() or member.islnk():
                raise SystemExit(f"unsafe archive member: {member.name}")
            target = (ROOT / member.name).resolve()
            if ROOT not in target.parents:
                raise SystemExit(f"path traversal: {member.name}")
        bundle.extractall(ROOT, members=members, filter="data")

    for name, expected in EXPECTED.items():
        actual = digest((ROOT / name).read_bytes())
        if actual != expected:
            raise SystemExit(f"materialized checksum mismatch: {name}")

    shutil.rmtree(TRANSPORT)
    Path(__file__).unlink()
    print("V171-V178 readable source materialized and verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
