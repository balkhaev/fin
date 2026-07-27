#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from finruntime.registry import registry_payload  # noqa: E402


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_provenance() -> None:
    registry_path = ROOT / "docs" / "checkpoints" / "runtime-v1" / "SOURCE_REGISTRY.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if registry["repository"] != "balkhaev/fin":
        raise SystemExit("unexpected repository in SOURCE_REGISTRY")
    anchor = registry["frozen_base_commit"]
    subprocess.run(
        ["git", "cat-file", "-e", f"{anchor}^{{commit}}"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    for profile in registry["profiles"].values():
        for relative, expected in profile["source_paths"].items():
            path = ROOT / relative
            if not path.is_file():
                raise SystemExit(f"missing frozen source: {relative}")
            actual = sha256_file(path)
            if actual != expected:
                raise SystemExit(
                    f"frozen source hash mismatch for {relative}: {actual} != {expected}"
                )
    if any(registry["safety"].values()):
        raise SystemExit("runtime safety flags must all remain false")


def verify_configs_and_schemas() -> None:
    expected = registry_payload()
    committed = json.loads(
        (ROOT / "docs" / "checkpoints" / "runtime-v1" / "STRATEGY_REGISTRY.json").read_text(
            encoding="utf-8"
        )
    )
    for strategy_id, profile in committed["strategies"].items():
        generated = expected["strategies"][strategy_id]
        if profile["role"] != generated["role"]:
            raise SystemExit(f"role mismatch for {strategy_id}")
        if tuple(profile["allowed_modes"]) != tuple(generated["allowed_modes"]):
            raise SystemExit(f"allowed mode mismatch for {strategy_id}")
        if profile["live_ready"] or profile["real_leverage_authorized"]:
            raise SystemExit(f"unsafe strategy flags for {strategy_id}")
    if committed["live_execution_available"]:
        raise SystemExit("live execution must not be available")

    for path in sorted((ROOT / "schemas" / "runtime").glob("*.schema.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            raise SystemExit(f"unsupported schema declaration: {path}")
        if value.get("type") != "object":
            raise SystemExit(f"runtime schema must describe an object: {path}")

    for path in sorted((ROOT / "config" / "strategies").glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("live_ready") or value.get("real_leverage_authorized"):
            raise SystemExit(f"unsafe config flags: {path}")
        if "live" in value.get("allowed_modes", []):
            raise SystemExit(f"live mode in config: {path}")


def verify_no_live_surface() -> None:
    forbidden = ("submit_order", "live_execution_available = true", '"mode": "live"')
    for path in (ROOT / "src" / "finruntime").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                raise SystemExit(f"forbidden live surface {token!r} in {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provenance-only", action="store_true")
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    verify_provenance()
    if not args.provenance_only:
        verify_configs_and_schemas()
        verify_no_live_surface()
    print(
        json.dumps(
            {
                "provenance": "passed",
                "full": not args.provenance_only,
                "live_execution_available": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
