from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from finruntime.canonical import (
    ContractError,
    canonical_json_bytes,
    format_utc,
    require_sha256,
    sha256_id,
)

MIGRATION_KINDS = {"byte_identical_materialization", "reconstruction"}
MIGRATION_STATUSES = {"planned", "implemented", "validated"}
MIGRATION_MODES = {"paper", "shadow"}
_STRATEGY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,127}$")


def _hash_payload(instance: Any, excluded: set[str]) -> dict[str, Any]:
    return {
        key: value
        for key, value in asdict(instance).items()
        if key not in excluded
    }


def _require_strategy_id(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not _STRATEGY_ID_RE.fullmatch(value):
        raise ContractError(
            f"{field} must be a lowercase strategy id containing only letters, "
            "digits, underscores or hyphens"
        )
    return value


def _require_repository_path(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{field} must be a non-empty repository-relative path")
    if "\\" in value or value.startswith("/"):
        raise ContractError(f"{field} must use a repository-relative POSIX path")
    raw_parts = value.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise ContractError(f"{field} contains a non-canonical path segment")
    normalized = PurePosixPath(value).as_posix()
    if normalized != value:
        raise ContractError(f"{field} must be a canonical repository path")
    return value


def _normalize_hash_manifest(
    value: Mapping[str, str] | None,
    *,
    field: str,
) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ContractError(f"{field} must be an object mapping paths to SHA-256")
    output: dict[str, str] = {}
    for raw_path, raw_hash in value.items():
        path = _require_repository_path(str(raw_path), field=f"{field} path")
        if path in output:
            raise ContractError(f"duplicate path in {field}: {path}")
        output[path] = require_sha256(raw_hash, field=f"{field}.{path}")
    return dict(sorted(output.items()))


def _validate_hash_manifest(
    value: Mapping[str, str],
    *,
    field: str,
    required: bool = False,
) -> None:
    if not isinstance(value, Mapping):
        raise ContractError(f"{field} must be an object mapping paths to SHA-256")
    if required and not value:
        raise ContractError(f"{field} must contain at least one entry")
    for path, digest in value.items():
        _require_repository_path(path, field=f"{field} path")
        normalized = require_sha256(digest, field=f"{field}.{path}")
        if normalized != digest:
            raise ContractError(f"{field}.{path} must use the sha256: prefix")



def _normalize_string_sequence(
    values: Sequence[str] | None,
    *,
    field: str,
) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise ContractError(f"{field} must be an array of strings")
    normalized = tuple(str(value).strip() for value in values)
    if any(not value for value in normalized):
        raise ContractError(f"{field} cannot contain empty values")
    if len(set(normalized)) != len(normalized):
        raise ContractError(f"{field} values must be unique")
    return tuple(sorted(normalized))


def _validate_string_sequence(
    values: Sequence[str],
    *,
    field: str,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        raise ContractError(f"{field} must be an array of strings")
    normalized = tuple(values)
    if any(not isinstance(value, str) or not value for value in normalized):
        raise ContractError(f"{field} must contain non-empty strings")
    if len(set(normalized)) != len(normalized):
        raise ContractError(f"{field} values must be unique")
    if normalized != tuple(sorted(normalized)):
        raise ContractError(f"{field} values must be sorted")
    return normalized


def _normalize_parameters(
    value: Mapping[str, Any] | None,
    *,
    field: str,
) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ContractError(f"{field} must be an object")
    output = {str(key): item for key, item in value.items()}
    if any(not key for key in output):
        raise ContractError(f"{field} cannot contain an empty key")
    canonical_json_bytes(output)
    return dict(sorted(output.items()))


@dataclass(frozen=True, slots=True)
class StrategyMigrationRecord:
    schema_version: str
    migration_id: str
    migration_kind: str
    status: str
    predecessor_strategy_id: str
    successor_strategy_id: str
    created_at_utc: str
    reason: str
    source_audits: Mapping[str, str]
    predecessor_artifact_hashes: Mapping[str, str]
    successor_source_hashes: Mapping[str, str]
    regression_fixture_hashes: Mapping[str, str]
    inherited_parameters: Mapping[str, Any]
    changed_parameters: Mapping[str, Any]
    changed_components: Sequence[str]
    allowed_modes: Sequence[str]
    forward_clock_reset: bool
    successor_provenance_complete: bool
    capital_authorization_carried_forward: bool
    live_ready: bool
    real_leverage_authorized: bool
    exchange_submission_available: bool

    @classmethod
    def create(
        cls,
        *,
        migration_kind: str,
        status: str,
        predecessor_strategy_id: str,
        successor_strategy_id: str,
        created_at_utc: str,
        reason: str,
        source_audits: Mapping[str, str],
        predecessor_artifact_hashes: Mapping[str, str] | None = None,
        successor_source_hashes: Mapping[str, str] | None = None,
        regression_fixture_hashes: Mapping[str, str] | None = None,
        inherited_parameters: Mapping[str, Any] | None = None,
        changed_parameters: Mapping[str, Any] | None = None,
        changed_components: Sequence[str] | None = None,
        allowed_modes: Sequence[str] = ("paper", "shadow"),
        forward_clock_reset: bool,
        successor_provenance_complete: bool,
        capital_authorization_carried_forward: bool = False,
        live_ready: bool = False,
        real_leverage_authorized: bool = False,
        exchange_submission_available: bool = False,
    ) -> "StrategyMigrationRecord":
        normalized_reason = str(reason).strip()
        provisional = cls(
            schema_version="1.0",
            migration_id="sha256:" + "0" * 64,
            migration_kind=str(migration_kind),
            status=str(status),
            predecessor_strategy_id=_require_strategy_id(
                predecessor_strategy_id,
                field="predecessor_strategy_id",
            ),
            successor_strategy_id=_require_strategy_id(
                successor_strategy_id,
                field="successor_strategy_id",
            ),
            created_at_utc=format_utc(created_at_utc),
            reason=normalized_reason,
            source_audits=_normalize_hash_manifest(
                source_audits,
                field="source_audits",
            ),
            predecessor_artifact_hashes=_normalize_hash_manifest(
                predecessor_artifact_hashes,
                field="predecessor_artifact_hashes",
            ),
            successor_source_hashes=_normalize_hash_manifest(
                successor_source_hashes,
                field="successor_source_hashes",
            ),
            regression_fixture_hashes=_normalize_hash_manifest(
                regression_fixture_hashes,
                field="regression_fixture_hashes",
            ),
            inherited_parameters=_normalize_parameters(
                inherited_parameters,
                field="inherited_parameters",
            ),
            changed_parameters=_normalize_parameters(
                changed_parameters,
                field="changed_parameters",
            ),
            changed_components=_normalize_string_sequence(
                changed_components,
                field="changed_components",
            ),
            allowed_modes=_normalize_string_sequence(
                allowed_modes,
                field="allowed_modes",
            ),
            forward_clock_reset=bool(forward_clock_reset),
            successor_provenance_complete=bool(successor_provenance_complete),
            capital_authorization_carried_forward=bool(
                capital_authorization_carried_forward
            ),
            live_ready=bool(live_ready),
            real_leverage_authorized=bool(real_leverage_authorized),
            exchange_submission_available=bool(exchange_submission_available),
        )
        result = replace(
            provisional,
            migration_id=sha256_id(_hash_payload(provisional, {"migration_id"})),
        )
        result.validate()
        return result

    def validate(self) -> None:
        if self.schema_version != "1.0":
            raise ContractError("unsupported StrategyMigrationRecord schema version")
        require_sha256(self.migration_id, field="migration_id")
        if self.migration_kind not in MIGRATION_KINDS:
            raise ContractError(f"unsupported migration kind: {self.migration_kind}")
        if self.status not in MIGRATION_STATUSES:
            raise ContractError(f"unsupported migration status: {self.status}")
        _require_strategy_id(
            self.predecessor_strategy_id,
            field="predecessor_strategy_id",
        )
        _require_strategy_id(
            self.successor_strategy_id,
            field="successor_strategy_id",
        )
        format_utc(self.created_at_utc)
        if not isinstance(self.reason, str) or not self.reason:
            raise ContractError("migration reason is required")
        if self.reason != self.reason.strip():
            raise ContractError("migration reason must not contain outer whitespace")

        _validate_hash_manifest(
            self.source_audits,
            field="source_audits",
            required=True,
        )
        _validate_hash_manifest(
            self.predecessor_artifact_hashes,
            field="predecessor_artifact_hashes",
        )
        _validate_hash_manifest(
            self.successor_source_hashes,
            field="successor_source_hashes",
        )
        _validate_hash_manifest(
            self.regression_fixture_hashes,
            field="regression_fixture_hashes",
        )
        canonical_json_bytes(self.inherited_parameters)
        canonical_json_bytes(self.changed_parameters)
        if set(self.inherited_parameters) & set(self.changed_parameters):
            raise ContractError(
                "a parameter cannot be both inherited and changed"
            )

        changed_components = _validate_string_sequence(
            self.changed_components,
            field="changed_components",
        )
        allowed_modes = _validate_string_sequence(
            self.allowed_modes,
            field="allowed_modes",
        )
        if not allowed_modes:
            raise ContractError("allowed_modes cannot be empty")
        unsupported_modes = set(allowed_modes) - MIGRATION_MODES
        if unsupported_modes:
            raise ContractError(
                f"migration successor has unsupported modes: {sorted(unsupported_modes)}"
            )

        if (
            self.capital_authorization_carried_forward
            or self.live_ready
            or self.real_leverage_authorized
            or self.exchange_submission_available
        ):
            raise ContractError(
                "migration records cannot carry forward capital or live authorization"
            )

        if self.status == "planned":
            if self.successor_source_hashes or self.regression_fixture_hashes:
                raise ContractError(
                    "planned migration cannot claim successor sources or fixtures"
                )
            if self.successor_provenance_complete:
                raise ContractError(
                    "planned migration cannot claim complete successor provenance"
                )
        elif self.status == "implemented":
            if not self.successor_source_hashes:
                raise ContractError(
                    "implemented migration requires committed successor source hashes"
                )
            if self.successor_provenance_complete:
                raise ContractError(
                    "implemented migration is not validated provenance"
                )
        elif self.status == "validated":
            if not self.successor_source_hashes:
                raise ContractError(
                    "validated migration requires successor source hashes"
                )
            if not self.regression_fixture_hashes:
                raise ContractError(
                    "validated migration requires regression fixture hashes"
                )
            if not self.successor_provenance_complete:
                raise ContractError(
                    "validated migration must mark successor provenance complete"
                )

        if self.migration_kind == "reconstruction":
            if self.predecessor_strategy_id == self.successor_strategy_id:
                raise ContractError(
                    "reconstruction requires a new successor strategy id"
                )
            if not self.forward_clock_reset:
                raise ContractError(
                    "reconstruction must reset the frozen forward clock"
                )
            if not changed_components:
                raise ContractError(
                    "reconstruction must identify at least one changed component"
                )
        else:
            if self.predecessor_strategy_id != self.successor_strategy_id:
                raise ContractError(
                    "byte-identical materialization must preserve the strategy id"
                )
            if self.status != "validated":
                raise ContractError(
                    "byte-identical materialization must be fully validated"
                )
            if self.forward_clock_reset:
                raise ContractError(
                    "byte-identical materialization must not reset the forward clock"
                )
            if changed_components or self.changed_parameters:
                raise ContractError(
                    "byte-identical materialization cannot change components or parameters"
                )
            if not self.predecessor_artifact_hashes:
                raise ContractError(
                    "byte-identical materialization requires predecessor artifact hashes"
                )
            if dict(self.predecessor_artifact_hashes) != dict(
                self.successor_source_hashes
            ):
                raise ContractError(
                    "byte-identical materialization requires identical artifact manifests"
                )

        expected = sha256_id(_hash_payload(self, {"migration_id"}))
        if self.migration_id != expected:
            raise ContractError("StrategyMigrationRecord hash mismatch")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_strategy_migration(value: Any) -> StrategyMigrationRecord:
    if not isinstance(value, Mapping):
        raise ContractError("StrategyMigrationRecord must be a JSON object")
    try:
        record = StrategyMigrationRecord(**dict(value))
    except TypeError as exc:
        raise ContractError("invalid StrategyMigrationRecord fields") from exc
    record.validate()
    return record
