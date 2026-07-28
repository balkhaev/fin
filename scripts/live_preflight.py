#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from finruntime.registry import registry_payload

EXPECTED_V75_ENGINE_SHA256 = (
    "3303cd91511bca0be81ade21272e1e8ba6f76adf826d238e9c4bd7cbe78f69fc"
)
RESEARCH_V517_MAX_LEVERAGE = 2.075
MINIMUM_MARGIN_BUFFER = 0.10
MINIMUM_FORWARD_DAYS = 180

REQUIRED_SHADOW_FILES = (
    "src/finruntime/profiles/v517_guard.py",
    "config/strategies/v517_tristate_guard_shadow.json",
    "scripts/build_v517_shadow_snapshot.py",
    "research/state_telemetry_v429_v436/evaluate_state_telemetry.py",
    "research/forward_mechanism_v445_v452/evaluate_forward_mechanism.py",
    "docs/checkpoints/runtime-v1/V517_RISK_BUDGET_CONTRACT.json",
)
PROHIBITED_SHADOW_FILES = (
    "research/state_telemetry_v429_v436/bootstrap_telemetry.py",
)


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class PreflightReport:
    mode: str
    repository_root: str
    shadow_ready: bool
    live_ready: bool
    checks: tuple[Check, ...]
    blockers: tuple[str, ...]
    exchange_submission_attempted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "repository_root": self.repository_root,
            "shadow_ready": self.shadow_ready,
            "live_ready": self.live_ready,
            "checks": [asdict(item) for item in self.checks],
            "blockers": list(self.blockers),
            "exchange_submission_attempted": self.exchange_submission_attempted,
        }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def add_check(checks: list[Check], name: str, passed: bool, detail: str) -> bool:
    checks.append(Check(name=name, passed=bool(passed), detail=detail))
    return bool(passed)


def all_truthy(values: Iterable[Any]) -> bool:
    return all(bool(value) for value in values)


def check_shadow(root: Path) -> tuple[list[Check], list[str]]:
    checks: list[Check] = []
    blockers: list[str] = []

    for relative in REQUIRED_SHADOW_FILES:
        path = root / relative
        passed = add_check(
            checks,
            f"required_file:{relative}",
            path.is_file() and path.stat().st_size > 0,
            "present" if path.is_file() else "missing",
        )
        if not passed:
            blockers.append(f"missing required shadow file: {relative}")

    for relative in PROHIBITED_SHADOW_FILES:
        path = root / relative
        passed = add_check(
            checks,
            f"prohibited_file_absent:{relative}",
            not path.exists(),
            "absent" if not path.exists() else "present",
        )
        if not passed:
            blockers.append(f"opaque/prohibited runtime source remains: {relative}")

    registry = registry_payload()
    strategies = registry.get("strategies", {})
    v517 = strategies.get("v517_tristate_guard_shadow") if isinstance(strategies, dict) else None
    registry_ok = isinstance(v517, dict)
    add_check(
        checks,
        "v517_registered",
        registry_ok,
        "registered" if registry_ok else "missing from registry",
    )
    if not registry_ok:
        blockers.append("V517 shadow profile is not registered")
    else:
        allowed_modes = tuple(v517.get("allowed_modes", ()))
        shadow_only = allowed_modes == ("shadow",) or allowed_modes == ["shadow"]
        add_check(
            checks,
            "v517_shadow_only",
            shadow_only,
            f"allowed_modes={list(allowed_modes)}",
        )
        if not shadow_only:
            blockers.append("V517 must remain shadow-only before live authorization")
        leverage_authorized = bool(v517.get("real_leverage_authorized", False))
        add_check(
            checks,
            "v517_real_leverage_not_authorized",
            not leverage_authorized,
            f"real_leverage_authorized={leverage_authorized}",
        )
        if leverage_authorized:
            blockers.append("registry prematurely authorizes real leverage")

    submission_disabled = registry.get("live_execution_available") is False
    add_check(
        checks,
        "exchange_submission_disabled",
        submission_disabled,
        f"live_execution_available={registry.get('live_execution_available')}",
    )
    if not submission_disabled:
        blockers.append("runtime registry unexpectedly enables exchange submission")

    return checks, blockers


def check_target_producer(
    root: Path,
    path_text: str | None,
    checks: list[Check],
    blockers: list[str],
) -> None:
    if not path_text:
        add_check(checks, "exact_v75_target_producer", False, "not supplied")
        blockers.append("exact V75 target producer was not supplied")
        return
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = root / path
    if not path.is_file():
        add_check(checks, "exact_v75_target_producer", False, f"missing: {path}")
        blockers.append("exact V75 target producer file is missing")
        return
    actual = sha256(path)
    passed = actual == EXPECTED_V75_ENGINE_SHA256
    add_check(
        checks,
        "exact_v75_target_producer",
        passed,
        f"sha256={actual}",
    )
    if not passed:
        blockers.append("V75 target producer SHA-256 does not match the frozen engine")


def check_margin_audit(
    root: Path,
    path_text: str | None,
    checks: list[Check],
    blockers: list[str],
) -> None:
    if not path_text:
        add_check(checks, "position_level_margin_audit", False, "not supplied")
        blockers.append("position-level margin/liquidation audit was not supplied")
        return
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = root / path
    try:
        value = read_json(path)
    except Exception as error:
        add_check(checks, "position_level_margin_audit", False, repr(error))
        blockers.append("position-level margin audit is missing or invalid")
        return
    passed = all_truthy(
        (
            value.get("template_only") is not True,
            value.get("position_level_margin_replay_complete") is True,
            value.get("source_target_hash_match") is True,
            int(value.get("liquidations", -1)) == 0,
            float(value.get("minimum_margin_buffer", -1.0)) >= MINIMUM_MARGIN_BUFFER,
            float(value.get("maximum_tested_leverage", 0.0)) >= RESEARCH_V517_MAX_LEVERAGE,
            value.get("passed") is True,
        )
    )
    add_check(
        checks,
        "position_level_margin_audit",
        passed,
        (
            f"passed={value.get('passed')}, liquidations={value.get('liquidations')}, "
            f"minimum_margin_buffer={value.get('minimum_margin_buffer')}, "
            f"maximum_tested_leverage={value.get('maximum_tested_leverage')}"
        ),
    )
    if not passed:
        blockers.append("position-level margin audit does not satisfy the frozen live gates")


def check_forward_acceptance(
    root: Path,
    path_text: str | None,
    checks: list[Check],
    blockers: list[str],
) -> None:
    if not path_text:
        add_check(checks, "forward_acceptance", False, "not supplied")
        blockers.append("forward acceptance evidence was not supplied")
        return
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = root / path
    try:
        value = read_json(path)
    except Exception as error:
        add_check(checks, "forward_acceptance", False, repr(error))
        blockers.append("forward acceptance evidence is missing or invalid")
        return
    child_checks = value.get("checks")
    passed = all_truthy(
        (
            value.get("template_only") is not True,
            value.get("passed") is True,
            int(value.get("calendar_days", 0)) >= MINIMUM_FORWARD_DAYS,
            isinstance(child_checks, dict),
            all(child_checks.values()) if isinstance(child_checks, dict) else False,
        )
    )
    add_check(
        checks,
        "forward_acceptance",
        passed,
        f"passed={value.get('passed')}, calendar_days={value.get('calendar_days')}",
    )
    if not passed:
        blockers.append("forward paper evidence has not passed every frozen acceptance gate")


def check_exchange_adapter_manifest(
    root: Path,
    path_text: str | None,
    checks: list[Check],
    blockers: list[str],
) -> None:
    if not path_text:
        add_check(checks, "exchange_adapter_manifest", False, "not supplied")
        blockers.append("validated exchange adapter manifest was not supplied")
        return
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = root / path
    try:
        value = read_json(path)
    except Exception as error:
        add_check(checks, "exchange_adapter_manifest", False, repr(error))
        blockers.append("exchange adapter manifest is missing or invalid")
        return
    required = (
        "exchange_submission_surface",
        "testnet_validated",
        "idempotent_client_order_ids",
        "reduce_only_supported",
        "kill_switch",
        "secrets_from_environment",
        "reconciliation_fail_closed",
        "passed",
    )
    passed = value.get("template_only") is not True and all(value.get(name) is True for name in required)
    add_check(
        checks,
        "exchange_adapter_manifest",
        passed,
        ", ".join(f"{name}={value.get(name)}" for name in required),
    )
    if not passed:
        blockers.append("exchange adapter has not passed every operational safety requirement")


def run_preflight(args: argparse.Namespace) -> PreflightReport:
    root = Path(args.repository_root).resolve()
    checks, blockers = check_shadow(root)
    shadow_ready = not blockers
    live_ready = False

    if args.mode == "live":
        check_target_producer(root, args.target_producer, checks, blockers)
        check_margin_audit(root, args.margin_audit, checks, blockers)
        check_forward_acceptance(root, args.forward_acceptance, checks, blockers)
        check_exchange_adapter_manifest(root, args.exchange_adapter_manifest, checks, blockers)
        live_ready = not blockers

    return PreflightReport(
        mode=args.mode,
        repository_root=str(root),
        shadow_ready=shadow_ready,
        live_ready=live_ready,
        checks=tuple(checks),
        blockers=tuple(blockers),
        exchange_submission_attempted=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed readiness check for FIN shadow or live deployment."
    )
    parser.add_argument("--mode", choices=("shadow", "live"), required=True)
    parser.add_argument("--repository-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--target-producer")
    parser.add_argument("--margin-audit")
    parser.add_argument("--forward-acceptance")
    parser.add_argument("--exchange-adapter-manifest")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = run_preflight(args)
    payload = report.to_dict()
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if (report.shadow_ready if args.mode == "shadow" else report.live_ready) else 2


if __name__ == "__main__":
    raise SystemExit(main())
