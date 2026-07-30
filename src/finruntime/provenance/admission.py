from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from finruntime.canonical import (
    ContractError,
    format_utc,
    require_sha256,
    sha256_id,
)
from finruntime.provenance.migration import StrategyMigrationRecord

IDENTITY_ORIGINS = {"legacy_frozen", "migration"}
ALLOWED_MODES = {"paper", "shadow"}
_STRATEGY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,127}$")
_RESET_COUNTER_FIELDS = (
    "calendar_days_observed",
    "target_changes_observed",
    "closed_paper_trades",
    "nonzero_accelerator_regimes",
    "forward_observations",
    "unexplained_delta_mismatches",
    "state_recovery_failures",
)
_SAFETY_FIELDS = (
    "capital_authorization_carried_forward",
    "live_ready",
    "real_leverage_authorized",
    "exchange_submission_available",
)


def _hash_payload(instance: Any, excluded: set[str]) -> dict[str, Any]:
    return {
        key: value
        for key, value in asdict(instance).items()
        if key not in excluded
    }


def _require_strategy_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not _STRATEGY_ID_RE.fullmatch(value):
        raise ContractError(
            f"{field} must be a lowercase strategy id containing only letters, "
            "digits, underscores or hyphens"
        )
    return value


def _require_repository_path(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{field} must be a non-empty repository-relative path")
    if value.startswith("/") or "\\" in value:
        raise ContractError(f"{field} must use a repository-relative POSIX path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ContractError(f"{field} contains a non-canonical path segment")
    if PurePosixPath(value).as_posix() != value:
        raise ContractError(f"{field} must be a canonical repository path")
    return value


def _require_false(value: object, *, field: str) -> None:
    if type(value) is not bool:
        raise ContractError(f"{field} must be a JSON boolean")
    if value:
        raise ContractError(f"{field} must remain false")


def _require_zero_integer(value: object, *, field: str) -> int:
    if type(value) is not int:
        raise ContractError(f"{field} must be a JSON integer")
    if value != 0:
        raise ContractError(f"{field} must reset to zero")
    return value


def _normalize_modes(values: object, *, field: str) -> tuple[str, ...]:
    if (
        isinstance(values, (str, bytes, bytearray))
        or not isinstance(values, Sequence)
    ):
        raise ContractError(f"{field} must be an array")
    normalized = tuple(values)
    if any(not isinstance(value, str) for value in normalized):
        raise ContractError(f"{field} must contain strings")
    if not normalized or len(set(normalized)) != len(normalized):
        raise ContractError(f"{field} must contain unique modes")
    unsupported = set(normalized) - ALLOWED_MODES
    if unsupported:
        raise ContractError(f"{field} contains unsupported modes: {sorted(unsupported)}")
    return tuple(sorted(normalized))


def _normalized_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{field} must be a SHA-256 string")
    if re.fullmatch(r"[0-9a-f]{64}", value):
        value = f"sha256:{value}"
    return require_sha256(value, field=field)


def _normalize_registry_manifest(value: object, *, field: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise ContractError(f"{field} must be a non-empty path-to-hash object")
    output: dict[str, str] = {}
    for raw_path, raw_hash in value.items():
        path = _require_repository_path(raw_path, field=f"{field} path")
        output[path] = _normalized_sha256(raw_hash, field=f"{field}.{path}")
    return dict(sorted(output.items()))


def _require_optional_null(value: object, *, field: str) -> None:
    if value is not None:
        raise ContractError(f"{field} must be null for a legacy identity")


@dataclass(frozen=True, slots=True)
class ForwardClockResetRecord:
    schema_version: str
    reset_id: str
    strategy_id: str
    migration_id: str
    created_at_utc: str
    reason: str
    initial_account_state_path: str
    initial_account_state_sha256: str
    initial_account_hash: str
    initial_account_sequence: int
    calendar_days_observed: int
    target_changes_observed: int
    closed_paper_trades: int
    nonzero_accelerator_regimes: int
    forward_observations: int
    unexplained_delta_mismatches: int
    state_recovery_failures: int
    account_state_reused: bool
    historical_evidence_reused: bool
    capital_authorization_carried_forward: bool
    live_ready: bool
    real_leverage_authorized: bool
    exchange_submission_available: bool

    @classmethod
    def create(
        cls,
        *,
        strategy_id: str,
        migration_id: str,
        created_at_utc: str,
        reason: str,
        initial_account_state_path: str,
        initial_account_state_sha256: str,
        initial_account_hash: str,
        initial_account_sequence: int = 0,
        calendar_days_observed: int = 0,
        target_changes_observed: int = 0,
        closed_paper_trades: int = 0,
        nonzero_accelerator_regimes: int = 0,
        forward_observations: int = 0,
        unexplained_delta_mismatches: int = 0,
        state_recovery_failures: int = 0,
        account_state_reused: bool = False,
        historical_evidence_reused: bool = False,
        capital_authorization_carried_forward: bool = False,
        live_ready: bool = False,
        real_leverage_authorized: bool = False,
        exchange_submission_available: bool = False,
    ) -> "ForwardClockResetRecord":
        normalized_reason = str(reason).strip()
        provisional = cls(
            schema_version="1.0",
            reset_id="sha256:" + "0" * 64,
            strategy_id=_require_strategy_id(strategy_id, field="strategy_id"),
            migration_id=_normalized_sha256(migration_id, field="migration_id"),
            created_at_utc=format_utc(created_at_utc),
            reason=normalized_reason,
            initial_account_state_path=_require_repository_path(
                initial_account_state_path,
                field="initial_account_state_path",
            ),
            initial_account_state_sha256=_normalized_sha256(
                initial_account_state_sha256,
                field="initial_account_state_sha256",
            ),
            initial_account_hash=_normalized_sha256(
                initial_account_hash,
                field="initial_account_hash",
            ),
            initial_account_sequence=initial_account_sequence,
            calendar_days_observed=calendar_days_observed,
            target_changes_observed=target_changes_observed,
            closed_paper_trades=closed_paper_trades,
            nonzero_accelerator_regimes=nonzero_accelerator_regimes,
            forward_observations=forward_observations,
            unexplained_delta_mismatches=unexplained_delta_mismatches,
            state_recovery_failures=state_recovery_failures,
            account_state_reused=account_state_reused,
            historical_evidence_reused=historical_evidence_reused,
            capital_authorization_carried_forward=(
                capital_authorization_carried_forward
            ),
            live_ready=live_ready,
            real_leverage_authorized=real_leverage_authorized,
            exchange_submission_available=exchange_submission_available,
        )
        result = replace(
            provisional,
            reset_id=sha256_id(_hash_payload(provisional, {"reset_id"})),
        )
        result.validate()
        return result

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise ContractError("unsupported ForwardClockResetRecord schema version")
        if not isinstance(self.reset_id, str):
            raise ContractError("reset_id must be a SHA-256 string")
        require_sha256(self.reset_id, field="reset_id")
        _require_strategy_id(self.strategy_id, field="strategy_id")
        _normalized_sha256(self.migration_id, field="migration_id")
        if not isinstance(self.created_at_utc, str):
            raise ContractError("created_at_utc must be a UTC string")
        format_utc(self.created_at_utc)
        if not isinstance(self.reason, str) or not self.reason:
            raise ContractError("reset reason is required")
        if self.reason != self.reason.strip():
            raise ContractError("reset reason must not contain outer whitespace")
        _require_repository_path(
            self.initial_account_state_path,
            field="initial_account_state_path",
        )
        _normalized_sha256(
            self.initial_account_state_sha256,
            field="initial_account_state_sha256",
        )
        _normalized_sha256(self.initial_account_hash, field="initial_account_hash")
        _require_zero_integer(
            self.initial_account_sequence,
            field="initial_account_sequence",
        )
        for field_name in _RESET_COUNTER_FIELDS:
            _require_zero_integer(getattr(self, field_name), field=field_name)
        _require_false(self.account_state_reused, field="account_state_reused")
        _require_false(
            self.historical_evidence_reused,
            field="historical_evidence_reused",
        )
        for field_name in _SAFETY_FIELDS:
            _require_false(getattr(self, field_name), field=field_name)
        expected = sha256_id(_hash_payload(self, {"reset_id"}))
        if self.reset_id != expected:
            raise ContractError("ForwardClockResetRecord hash mismatch")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RegistrationAdmission:
    strategy_id: str
    identity_origin: str
    predecessor_strategy_id: str | None
    migration_id: str | None
    reset_id: str | None
    allowed_modes: tuple[str, ...]
    provenance_profile: str | None
    admitted: bool


def parse_forward_clock_reset(value: Any) -> ForwardClockResetRecord:
    if not isinstance(value, Mapping):
        raise ContractError("ForwardClockResetRecord must be a JSON object")
    try:
        record = ForwardClockResetRecord(**dict(value))
    except TypeError as exc:
        raise ContractError("invalid ForwardClockResetRecord fields") from exc
    record.validate()
    return record


def validate_identity_policy(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Mapping):
        raise ContractError("strategy identity policy must be a JSON object")
    if value.get("schema_version") != "1.0":
        raise ContractError("unsupported strategy identity policy version")
    origins = value.get("allowed_identity_origins")
    if (
        isinstance(origins, (str, bytes, bytearray))
        or not isinstance(origins, Sequence)
        or set(origins) != IDENTITY_ORIGINS
        or len(origins) != len(IDENTITY_ORIGINS)
    ):
        raise ContractError("identity policy must allow exactly legacy_frozen and migration")
    grandfathered = value.get("grandfathered_legacy_strategy_ids")
    if (
        isinstance(grandfathered, (str, bytes, bytearray))
        or not isinstance(grandfathered, Sequence)
        or not grandfathered
    ):
        raise ContractError("identity policy requires grandfathered legacy ids")
    normalized = tuple(grandfathered)
    if any(not isinstance(item, str) for item in normalized):
        raise ContractError("grandfathered strategy ids must be strings")
    for strategy_id in normalized:
        _require_strategy_id(strategy_id, field="grandfathered strategy id")
    if normalized != tuple(sorted(set(normalized))):
        raise ContractError("grandfathered strategy ids must be unique and sorted")
    requirements = value.get("requirements")
    if not isinstance(requirements, Mapping):
        raise ContractError("identity policy requirements must be an object")
    required_true = (
        "validated_migration_record",
        "complete_successor_provenance",
        "separate_forward_clock_reset_record",
        "new_strategy_id_for_reconstruction",
    )
    for field in required_true:
        if requirements.get(field) is not True:
            raise ContractError(f"identity policy must require {field}")
    if requirements.get("native_registration_available") is not False:
        raise ContractError("native registration must remain unavailable")
    for field in (
        "forward_state_reuse_permitted",
        "historical_evidence_carried_forward",
        "capital_authorization_carried_forward",
        "live_ready",
        "real_leverage_authorized",
        "exchange_submission_available",
    ):
        if requirements.get(field) is not False:
            raise ContractError(f"identity policy must keep {field}=false")
    return normalized


def validate_strategy_registration(
    config: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
    source_registry: Mapping[str, Any],
    migration_record: StrategyMigrationRecord | None = None,
    reset_record: ForwardClockResetRecord | None = None,
) -> RegistrationAdmission:
    if not isinstance(config, Mapping):
        raise ContractError("strategy config must be a JSON object")
    grandfathered = set(validate_identity_policy(policy))
    strategy_id = _require_strategy_id(config.get("strategy_id"), field="strategy_id")
    origin = config.get("identity_origin")
    if not isinstance(origin, str) or origin not in IDENTITY_ORIGINS:
        raise ContractError("strategy identity_origin must be legacy_frozen or migration")
    modes = _normalize_modes(config.get("allowed_modes"), field="allowed_modes")
    for field_name in (
        "live_ready",
        "real_leverage_authorized",
        "exchange_submission_available",
        "forward_state_reuse_permitted",
        "historical_evidence_carried_forward",
    ):
        _require_false(config.get(field_name), field=field_name)

    if origin == "legacy_frozen":
        if strategy_id not in grandfathered:
            raise ContractError(
                f"strategy {strategy_id!r} is not a grandfathered legacy identity"
            )
        for field_name in (
            "migration_record_path",
            "forward_clock_reset_record_path",
            "predecessor_strategy_id",
            "provenance_profile",
        ):
            _require_optional_null(config.get(field_name), field=field_name)
        if config.get("forward_clock_reset") is not False:
            raise ContractError("legacy identity must not claim a forward clock reset")
        if migration_record is not None or reset_record is not None:
            raise ContractError("legacy identity cannot attach migration evidence")
        return RegistrationAdmission(
            strategy_id=strategy_id,
            identity_origin=origin,
            predecessor_strategy_id=None,
            migration_id=None,
            reset_id=None,
            allowed_modes=modes,
            provenance_profile=None,
            admitted=True,
        )

    if strategy_id in grandfathered:
        raise ContractError("grandfathered ids cannot be reclassified as migration")
    if migration_record is None or reset_record is None:
        raise ContractError("migration registration requires migration and reset records")
    migration_record.validate()
    reset_record.validate()
    if migration_record.migration_kind != "reconstruction":
        raise ContractError("new registry identities require reconstruction records")
    if migration_record.status != "validated":
        raise ContractError("migration registration requires status=validated")
    if not migration_record.successor_provenance_complete:
        raise ContractError("migration successor provenance must be complete")
    if migration_record.successor_strategy_id != strategy_id:
        raise ContractError("migration successor id does not match strategy config")
    predecessor = _require_strategy_id(
        config.get("predecessor_strategy_id"),
        field="predecessor_strategy_id",
    )
    if migration_record.predecessor_strategy_id != predecessor:
        raise ContractError("migration predecessor id does not match strategy config")
    if config.get("forward_clock_reset") is not True:
        raise ContractError("migration registration must reset the forward clock")
    if not migration_record.forward_clock_reset:
        raise ContractError("migration record does not authorize a forward clock reset")
    migration_path = _require_repository_path(
        config.get("migration_record_path"),
        field="migration_record_path",
    )
    reset_path = _require_repository_path(
        config.get("forward_clock_reset_record_path"),
        field="forward_clock_reset_record_path",
    )
    if reset_record.strategy_id != strategy_id:
        raise ContractError("reset record strategy id does not match successor")
    if reset_record.migration_id != migration_record.migration_id:
        raise ContractError("reset record does not reference the migration id")
    provenance_profile = _require_strategy_id(
        config.get("provenance_profile"),
        field="provenance_profile",
    )
    if provenance_profile != strategy_id:
        raise ContractError("migration provenance profile must equal successor id")
    if modes != tuple(sorted(migration_record.allowed_modes)):
        raise ContractError("strategy config modes do not match migration record")

    profiles = source_registry.get("profiles")
    if not isinstance(profiles, Mapping):
        raise ContractError("source registry profiles must be an object")
    profile = profiles.get(provenance_profile)
    if not isinstance(profile, Mapping):
        raise ContractError("successor provenance profile is missing")
    if profile.get("provenance_complete") is not True:
        raise ContractError("successor source registry provenance is incomplete")
    if profile.get("unmaterialized_requirements"):
        raise ContractError("successor provenance still has unmaterialized requirements")
    source_manifest = _normalize_registry_manifest(
        profile.get("source_paths"),
        field="successor source_paths",
    )
    fixture_manifest = _normalize_registry_manifest(
        profile.get("regression_fixture_paths"),
        field="successor regression_fixture_paths",
    )
    if source_manifest != dict(migration_record.successor_source_hashes):
        raise ContractError("source registry manifest differs from migration record")
    if fixture_manifest != dict(migration_record.regression_fixture_hashes):
        raise ContractError("regression fixture manifest differs from migration record")

    # Keep the repository-relative evidence paths in the admission result indirectly by
    # requiring that they were present and canonical before returning success.
    _ = migration_path, reset_path
    return RegistrationAdmission(
        strategy_id=strategy_id,
        identity_origin=origin,
        predecessor_strategy_id=predecessor,
        migration_id=migration_record.migration_id,
        reset_id=reset_record.reset_id,
        allowed_modes=modes,
        provenance_profile=provenance_profile,
        admitted=True,
    )
