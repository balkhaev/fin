#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from finruntime.canonical import ContractError  # noqa: E402
from finruntime.portfolio import PaperAccountState  # noqa: E402
from finruntime.provenance import (  # noqa: E402
    ForwardClockResetRecord,
    StrategyMigrationRecord,
    parse_forward_clock_reset,
    parse_strategy_migration,
    validate_identity_policy,
    validate_strategy_registration,
)
from finruntime.registry import registry_payload  # noqa: E402

EXPECTED_LEGACY_STRATEGY_IDS = {
    "v136_execution_shadow",
    "v28_growth_control",
    "v517_tristate_guard_shadow",
    "v75_atlas_nx",
}
IDENTITY_FIELDS = (
    "identity_origin",
    "migration_record_path",
    "forward_clock_reset_record_path",
    "predecessor_strategy_id",
    "provenance_profile",
    "forward_clock_reset",
    "forward_state_reuse_permitted",
    "historical_evidence_carried_forward",
)
SAFETY_FIELDS = (
    "live_ready",
    "real_leverage_authorized",
    "exchange_submission_available",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot load {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must be a JSON object: {path}")
    return value


def load_source_registry() -> dict[str, object]:
    path = ROOT / "docs" / "checkpoints" / "runtime-v1" / "SOURCE_REGISTRY.json"
    value = load_json_object(path, label="SOURCE_REGISTRY")
    if value["repository"] != "balkhaev/fin":
        raise SystemExit("unexpected repository in SOURCE_REGISTRY")
    if any(value["safety"].values()):
        raise SystemExit("runtime safety flags must all remain false")
    return value


def load_identity_policy() -> dict[str, object]:
    path = (
        ROOT
        / "docs"
        / "checkpoints"
        / "runtime-v1"
        / "STRATEGY_IDENTITY_POLICY.json"
    )
    return load_json_object(path, label="strategy identity policy")


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


def _strict_false(value: object, *, field: str, strategy_id: str) -> None:
    if type(value) is not bool or value:
        raise SystemExit(f"{strategy_id} requires {field}=false")


def _load_migration_record(config: Mapping[str, Any]) -> StrategyMigrationRecord:
    relative = config.get("migration_record_path")
    if not isinstance(relative, str):
        raise SystemExit("migration strategy requires migration_record_path")
    path = ROOT / relative
    if not path.is_file():
        raise SystemExit(f"migration record is missing: {relative}")
    try:
        return parse_strategy_migration(
            load_json_object(path, label="strategy migration record")
        )
    except ContractError as exc:
        raise SystemExit(f"invalid migration record {relative}: {exc}") from exc


def _load_reset_record(config: Mapping[str, Any]) -> ForwardClockResetRecord:
    relative = config.get("forward_clock_reset_record_path")
    if not isinstance(relative, str):
        raise SystemExit("migration strategy requires forward_clock_reset_record_path")
    path = ROOT / relative
    if not path.is_file():
        raise SystemExit(f"forward clock reset record is missing: {relative}")
    try:
        return parse_forward_clock_reset(
            load_json_object(path, label="forward clock reset record")
        )
    except ContractError as exc:
        raise SystemExit(f"invalid reset record {relative}: {exc}") from exc


def _verify_initial_account_state(reset: ForwardClockResetRecord) -> None:
    path = ROOT / reset.initial_account_state_path
    if not path.is_file():
        raise SystemExit(
            f"initial account state is missing: {reset.initial_account_state_path}"
        )
    actual_file_hash = f"sha256:{sha256_file(path)}"
    if actual_file_hash != reset.initial_account_state_sha256:
        raise SystemExit(
            "initial account state file hash mismatch: "
            f"{actual_file_hash} != {reset.initial_account_state_sha256}"
        )
    raw = load_json_object(path, label="initial paper account state")
    try:
        state = PaperAccountState(**raw)
        state.validate()
    except (ContractError, TypeError) as exc:
        raise SystemExit(f"invalid initial paper account state: {path}: {exc}") from exc
    if state.strategy_id != reset.strategy_id:
        raise SystemExit("initial account strategy id does not match reset record")
    if state.account_hash != reset.initial_account_hash:
        raise SystemExit("initial account object hash does not match reset record")
    if state.sequence != 0 or reset.initial_account_sequence != 0:
        raise SystemExit("migrated successor account sequence must start at zero")
    if state.spot_positions or state.perp_positions or state.perp_entry_prices:
        raise SystemExit("migrated successor account must start without positions")
    if state.last_plan_id is not None or state.applied_event_ids:
        raise SystemExit("migrated successor account cannot reuse plan or event history")
    if state.active_plan_filled_quantities or state.active_plan_fill_event_ids:
        raise SystemExit("migrated successor account cannot reuse fill progress")
    if not (
        Decimal(state.cash) == Decimal(state.equity) == Decimal(state.high_water)
    ):
        raise SystemExit("initial migrated account must be pristine cash-only equity")
    if Decimal(state.cash) <= 0:
        raise SystemExit("initial migrated account requires positive starting cash")


def _verify_registration(
    config: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
    source_registry: Mapping[str, Any],
) -> None:
    migration: StrategyMigrationRecord | None = None
    reset: ForwardClockResetRecord | None = None
    if config.get("identity_origin") == "migration":
        migration = _load_migration_record(config)
        reset = _load_reset_record(config)
    try:
        admission = validate_strategy_registration(
            config,
            policy=policy,
            source_registry=source_registry,
            migration_record=migration,
            reset_record=reset,
        )
    except ContractError as exc:
        raise SystemExit(
            f"strategy registration rejected for {config.get('strategy_id')}: {exc}"
        ) from exc
    if not admission.admitted:
        raise SystemExit(f"strategy registration was not admitted: {admission.strategy_id}")
    if reset is not None:
        _verify_initial_account_state(reset)


def verify_configs_and_schemas() -> None:
    expected = registry_payload()
    committed = load_json_object(
        ROOT / "docs" / "checkpoints" / "runtime-v1" / "STRATEGY_REGISTRY.json",
        label="committed strategy registry",
    )
    source_registry = load_source_registry()
    identity_policy = load_identity_policy()
    try:
        policy_legacy_ids = set(validate_identity_policy(identity_policy))
    except ContractError as exc:
        raise SystemExit(f"invalid strategy identity policy: {exc}") from exc
    if policy_legacy_ids != EXPECTED_LEGACY_STRATEGY_IDS:
        raise SystemExit(
            "grandfathered legacy identity set changed; new legacy registration is forbidden"
        )

    expected_strategies = expected.get("strategies")
    committed_strategies = committed.get("strategies")
    if not isinstance(expected_strategies, Mapping):
        raise SystemExit("generated strategy registry is invalid")
    if not isinstance(committed_strategies, Mapping):
        raise SystemExit("committed strategy registry is invalid")
    if expected.get("live_execution_available") is not False:
        raise SystemExit("generated registry must disable live execution")
    if committed.get("live_execution_available") is not False:
        raise SystemExit("committed registry must disable live execution")

    config_paths = sorted((ROOT / "config" / "strategies").glob("*.json"))
    configs: dict[str, dict[str, Any]] = {}
    for path in config_paths:
        value = load_json_object(path, label="strategy config")
        strategy_id = value.get("strategy_id")
        if not isinstance(strategy_id, str) or not strategy_id:
            raise SystemExit(f"strategy config lacks strategy_id: {path}")
        if path.stem != strategy_id:
            raise SystemExit(
                f"strategy config filename/id mismatch: {path.stem} != {strategy_id}"
            )
        if strategy_id in configs:
            raise SystemExit(f"duplicate strategy config id: {strategy_id}")
        configs[strategy_id] = value

    expected_ids = set(expected_strategies)
    committed_ids = set(committed_strategies)
    config_ids = set(configs)
    if expected_ids != committed_ids or expected_ids != config_ids:
        raise SystemExit(
            "generated registry, committed registry and strategy configs differ"
        )

    legacy_ids: set[str] = set()
    for strategy_id in sorted(expected_ids):
        generated = expected_strategies[strategy_id]
        profile = committed_strategies[strategy_id]
        config = configs[strategy_id]
        if not isinstance(generated, Mapping) or not isinstance(profile, Mapping):
            raise SystemExit(f"invalid registry profile for {strategy_id}")
        if profile.get("role") != generated.get("role"):
            raise SystemExit(f"role mismatch for {strategy_id}")
        if config.get("role") != generated.get("role"):
            raise SystemExit(f"config role mismatch for {strategy_id}")
        generated_modes = tuple(generated.get("allowed_modes", ()))
        if tuple(profile.get("allowed_modes", ())) != generated_modes:
            raise SystemExit(f"committed allowed mode mismatch for {strategy_id}")
        if tuple(config.get("allowed_modes", ())) != generated_modes:
            raise SystemExit(f"config allowed mode mismatch for {strategy_id}")

        for field in IDENTITY_FIELDS:
            if profile.get(field) != generated.get(field):
                raise SystemExit(f"committed {field} mismatch for {strategy_id}")
            if config.get(field) != generated.get(field):
                raise SystemExit(f"config {field} mismatch for {strategy_id}")
        for field in SAFETY_FIELDS:
            _strict_false(generated.get(field), field=field, strategy_id=strategy_id)
            _strict_false(profile.get(field), field=field, strategy_id=strategy_id)
            _strict_false(config.get(field), field=field, strategy_id=strategy_id)
        if "live" in generated_modes:
            raise SystemExit(f"live mode in generated registry: {strategy_id}")

        _verify_registration(
            config,
            policy=identity_policy,
            source_registry=source_registry,
        )
        if config.get("identity_origin") == "legacy_frozen":
            legacy_ids.add(strategy_id)

    if legacy_ids != EXPECTED_LEGACY_STRATEGY_IDS:
        raise SystemExit(
            "registered legacy identities differ from the immutable grandfathered set"
        )

    schemas = sorted((ROOT / "schemas" / "runtime").glob("*.schema.json"))
    if not schemas:
        raise SystemExit("runtime schemas are missing")
    for path in schemas:
        value = load_json_object(path, label="runtime schema")
        if value.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            raise SystemExit(f"unsupported schema declaration: {path}")
        if value.get("type") != "object":
            raise SystemExit(f"runtime schema must describe an object: {path}")


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
