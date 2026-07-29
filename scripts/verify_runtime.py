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


def load_source_registry() -> dict[str, object]:
    path = ROOT / "docs" / "checkpoints" / "runtime-v1" / "SOURCE_REGISTRY.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if value["repository"] != "balkhaev/fin":
        raise SystemExit("unexpected repository in SOURCE_REGISTRY")
    if any(value["safety"].values()):
        raise SystemExit("runtime safety flags must all remain false")
    return value


def provenance_completeness_issues(profile: dict[str, object]) -> tuple[str, ...]:
    issues: list[str] = []
    if profile.get("provenance_complete") is not True:
        issues.append("provenance_complete=false")

    unmaterialized = profile.get("unmaterialized_requirements")
    if isinstance(unmaterialized, dict):
        for name, requirement in sorted(unmaterialized.items()):
            status = (
                str(requirement.get("status", "present"))
                if isinstance(requirement, dict)
                else "present"
            )
            issues.append(f"unmaterialized requirement:{name}:{status}")
    elif unmaterialized:
        issues.append("unmaterialized requirements present")

    return tuple(issues)


def verify_provenance(
    profile_name: str | None = None,
    *,
    require_complete: bool = False,
) -> dict[str, dict[str, object]]:
    registry = load_source_registry()
    anchor = registry["frozen_base_commit"]
    subprocess.run(
        ["git", "cat-file", "-e", f"{anchor}^{{commit}}"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    profiles = registry["profiles"]
    if profile_name is not None:
        if profile_name not in profiles:
            raise SystemExit(f"unknown provenance profile: {profile_name}")
        profiles = {profile_name: profiles[profile_name]}

    statuses: dict[str, dict[str, object]] = {}
    incomplete: list[str] = []
    for current_name, profile in profiles.items():
        source_paths = profile.get("source_paths")
        if not isinstance(source_paths, dict) or not source_paths:
            raise SystemExit(f"provenance profile has no source paths: {current_name}")
        for relative, expected in source_paths.items():
            path = ROOT / relative
            if not path.is_file():
                raise SystemExit(
                    f"missing frozen source for {current_name}: {relative}"
                )
            actual = sha256_file(path)
            if actual != expected:
                raise SystemExit(
                    f"frozen source hash mismatch for {current_name} {relative}: "
                    f"{actual} != {expected}"
                )

        issues = provenance_completeness_issues(profile)
        complete = not issues
        if not complete:
            incomplete.append(f"{current_name} ({'; '.join(issues)})")
        statuses[current_name] = {
            "source_hash_integrity": "passed",
            "provenance_complete": complete,
            "issues": list(issues),
            "runtime_blocker": profile.get("runtime_blocker"),
        }
        label = "complete" if complete else "incomplete"
        print(f"source hash integrity passed: {current_name} ({label})")

    if require_complete and incomplete:
        raise SystemExit("incomplete provenance profiles: " + "; ".join(incomplete))
    return statuses


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

    schemas = sorted((ROOT / "schemas" / "runtime").glob("*.schema.json"))
    if not schemas:
        raise SystemExit("runtime schemas are missing")
    for path in schemas:
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            raise SystemExit(f"unsupported schema declaration: {path}")
        if value.get("type") != "object":
            raise SystemExit(f"runtime schema must describe an object: {path}")

    configs = sorted((ROOT / "config" / "strategies").glob("*.json"))
    if set(path.stem for path in configs) != set(committed["strategies"]):
        raise SystemExit("strategy config set does not match committed registry")
    for path in configs:
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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--provenance-only",
        action="store_true",
        help="verify hashes and require complete materialized provenance",
    )
    mode.add_argument(
        "--source-hashes-only",
        action="store_true",
        help="verify committed source hashes without claiming provenance completeness",
    )
    mode.add_argument("--contracts-only", action="store_true")
    mode.add_argument("--full", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="require complete provenance when running the default/full verification",
    )
    args = parser.parse_args()

    if args.require_complete and args.contracts_only:
        parser.error("--require-complete cannot be used with --contracts-only")

    statuses: dict[str, dict[str, object]] = {}
    if args.contracts_only:
        verify_configs_and_schemas()
        verify_no_live_surface()
        provenance_status = "not_requested"
        contracts_status = "passed"
    else:
        require_complete = args.provenance_only or args.require_complete
        statuses = verify_provenance(
            args.profile,
            require_complete=require_complete,
        )
        all_complete = all(
            bool(status["provenance_complete"]) for status in statuses.values()
        )
        provenance_status = (
            "complete" if all_complete else "source_hashes_passed_incomplete"
        )
        if args.provenance_only or args.source_hashes_only:
            contracts_status = "not_requested"
        else:
            verify_configs_and_schemas()
            verify_no_live_surface()
            contracts_status = "passed"

    print(
        json.dumps(
            {
                "provenance": provenance_status,
                "profile": args.profile,
                "profile_statuses": statuses,
                "contracts": contracts_status,
                "live_execution_available": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
