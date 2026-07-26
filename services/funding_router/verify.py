from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    manifest = json.loads((ROOT / "MANIFEST.json").read_text(encoding="utf-8"))
    errors: list[str] = []
    for relative, expected in manifest["files"].items():
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"missing: {relative}")
            continue
        actual = sha256(path)
        if actual != expected["sha256"]:
            errors.append(f"sha256 mismatch: {relative}: {actual} != {expected['sha256']}")
        if path.stat().st_size != expected["bytes"]:
            errors.append(f"size mismatch: {relative}")
    status = json.loads((ROOT / "STATUS.json").read_text(encoding="utf-8"))
    if status.get("live_ready") is not False:
        errors.append("STATUS.json must keep live_ready=false")
    if status.get("real_leverage_authorized") is not False:
        errors.append("STATUS.json must keep real_leverage_authorized=false")
    if status.get("default_private_trading") is not False:
        errors.append("STATUS.json must keep default_private_trading=false")
    if errors:
        print("\n".join(errors))
        return 1
    print(f"verified {len(manifest['files'])} funding-router files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
