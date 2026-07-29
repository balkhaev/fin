from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from finruntime.canonical import (
    ContractError,
    canonical_json_bytes,
    format_utc,
    parse_utc,
    require_sha256,
    sha256_id,
)
from finruntime.locking import exclusive_file_lock


class JournalCorruptionError(RuntimeError):
    """Raised when the append-only hash chain cannot be verified."""


_SINGLETON_EVENT_TYPES = {
    "SNAPSHOT_ACCEPTED",
    "TARGET_COMPUTED",
    "PLAN_CREATED",
    "STATE_COMMITTED",
    "RECONCILIATION_COMPLETED",
    "HALT_RAISED",
    "HALT_CLEARED",
}

_RUNTIME_EVENT_PHASES = {
    "SNAPSHOT_ACCEPTED": 10,
    "TARGET_COMPUTED": 20,
    "PLAN_CREATED": 30,
    "FILL_RECORDED": 40,
    "STATE_COMMITTED": 50,
    "RECONCILIATION_COMPLETED": 60,
    "HALT_RAISED": 70,
}

_RUNTIME_EVENT_PREDECESSORS = {
    "TARGET_COMPUTED": {"SNAPSHOT_ACCEPTED"},
    "PLAN_CREATED": {"TARGET_COMPUTED"},
    "FILL_RECORDED": {"PLAN_CREATED"},
    "STATE_COMMITTED": {"PLAN_CREATED"},
    "RECONCILIATION_COMPLETED": {"STATE_COMMITTED"},
    "HALT_RAISED": {"RECONCILIATION_COMPLETED"},
}


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def write_atomic_json(path: Path, value: Any) -> None:
    _write_bytes_atomic(path, canonical_json_bytes(value) + b"\n")


def write_once_json(path: Path, value: Any) -> None:
    """Materialize immutable evidence or accept an identical retry.

    A fully written temporary inode is linked into place with create-if-absent
    semantics. Concurrent writers can never replace an existing artifact.
    """

    payload = canonical_json_bytes(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.once.", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            try:
                existing = path.read_bytes()
            except OSError as exc:
                raise ContractError(
                    f"cannot read immutable runtime artifact: {path}"
                ) from exc
            if existing != payload:
                raise ContractError(f"immutable runtime artifact conflict: {path}")
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


class AppendOnlyJournal:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")

    def _read_raw(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        with self.path.open("rb") as handle:
            for line_number, raw in enumerate(handle, 1):
                if not raw.endswith(b"\n"):
                    raise JournalCorruptionError(
                        f"journal line {line_number} is not newline terminated"
                    )
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise JournalCorruptionError(
                        f"invalid JSON on journal line {line_number}"
                    ) from exc
                if not isinstance(value, dict):
                    raise JournalCorruptionError(
                        f"journal line {line_number} is not an object"
                    )
                events.append(value)
        return events

    @staticmethod
    def _verify_semantics(events: Sequence[Mapping[str, Any]]) -> None:
        last_sequence: dict[str, int] = {}
        last_time: dict[str, Any] = {}
        phase_by_cycle: dict[tuple[str, int], int] = {}
        seen_by_cycle: dict[tuple[str, int], set[str]] = {}
        halted: set[str] = set()
        for line_number, event in enumerate(events, 1):
            strategy_id = str(event["strategy_id"])
            sequence = int(event["sequence"])
            event_type = str(event["event_type"])
            event_time = parse_utc(str(event["event_time_utc"]))
            previous_sequence = last_sequence.get(strategy_id)
            if previous_sequence is not None and sequence < previous_sequence:
                raise JournalCorruptionError(
                    f"journal sequence moved backward on line {line_number}"
                )
            last_sequence[strategy_id] = sequence
            if event_type in _RUNTIME_EVENT_PHASES or event_type == "HALT_CLEARED":
                previous_time = last_time.get(strategy_id)
                if previous_time is not None and event_time < previous_time:
                    raise JournalCorruptionError(
                        f"journal event time moved backward on line {line_number}"
                    )
                last_time[strategy_id] = event_time

            cycle_key = (strategy_id, sequence)
            seen = seen_by_cycle.setdefault(cycle_key, set())
            phase = _RUNTIME_EVENT_PHASES.get(event_type)
            if phase is not None:
                prior_phase = phase_by_cycle.get(cycle_key, -1)
                if phase < prior_phase:
                    raise JournalCorruptionError(
                        f"runtime event phase moved backward on line {line_number}"
                    )
                required = _RUNTIME_EVENT_PREDECESSORS.get(event_type, set())
                missing = required - seen
                if missing:
                    raise JournalCorruptionError(
                        f"runtime event {event_type} lacks predecessors {sorted(missing)} "
                        f"on line {line_number}"
                    )
                phase_by_cycle[cycle_key] = phase
                seen.add(event_type)

            if event_type == "HALT_RAISED":
                halted.add(strategy_id)
            elif event_type == "HALT_CLEARED":
                if strategy_id not in halted:
                    raise JournalCorruptionError(
                        f"HALT_CLEARED without HALT_RAISED on line {line_number}"
                    )
                halted.remove(strategy_id)

    @staticmethod
    def _verify_events(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        previous: str | None = None
        normalized = [dict(event) for event in events]
        seen: set[str] = set()
        singleton_keys: dict[tuple[str, str, int], str] = {}
        for line_number, event in enumerate(normalized, 1):
            required = {
                "event_type",
                "event_time_utc",
                "strategy_id",
                "sequence",
                "payload",
                "payload_hash",
                "previous_event_hash",
                "event_hash",
            }
            if set(event) != required:
                raise JournalCorruptionError(
                    f"journal line {line_number} has unexpected fields"
                )
            try:
                format_utc(event["event_time_utc"])
                require_sha256(event["payload_hash"], field="payload_hash")
                require_sha256(event["event_hash"], field="event_hash")
                if event["previous_event_hash"] is not None:
                    require_sha256(
                        event["previous_event_hash"], field="previous_event_hash"
                    )
                sequence = int(event["sequence"])
            except (ContractError, TypeError, ValueError) as exc:
                raise JournalCorruptionError(
                    f"journal line {line_number} violates the contract"
                ) from exc
            if sequence < 0 or not event["event_type"] or not event["strategy_id"]:
                raise JournalCorruptionError(
                    f"journal line {line_number} has invalid identity"
                )
            if event["previous_event_hash"] != previous:
                raise JournalCorruptionError(
                    f"journal chain break on line {line_number}"
                )
            expected_payload_hash = sha256_id(event["payload"])
            if event["payload_hash"] != expected_payload_hash:
                raise JournalCorruptionError(
                    f"payload hash mismatch on line {line_number}"
                )
            body = {key: value for key, value in event.items() if key != "event_hash"}
            expected_event_hash = sha256_id(body)
            if event["event_hash"] != expected_event_hash:
                raise JournalCorruptionError(
                    f"event hash mismatch on line {line_number}"
                )
            if event["event_hash"] in seen:
                raise JournalCorruptionError(
                    f"duplicate event hash on line {line_number}"
                )
            if event["event_type"] in _SINGLETON_EVENT_TYPES:
                key = (str(event["event_type"]), str(event["strategy_id"]), sequence)
                prior_payload = singleton_keys.get(key)
                if prior_payload is not None and prior_payload != event["payload_hash"]:
                    raise JournalCorruptionError(
                        f"conflicting singleton event on line {line_number}: {key}"
                    )
                singleton_keys[key] = str(event["payload_hash"])
            seen.add(str(event["event_hash"]))
            previous = str(event["event_hash"])
        AppendOnlyJournal._verify_semantics(normalized)
        return normalized

    def verify(self) -> list[dict[str, Any]]:
        return self._verify_events(self._read_raw())

    @staticmethod
    def _spec(
        *,
        event_type: str,
        event_time_utc: str,
        strategy_id: str,
        sequence: int,
        payload: Any,
    ) -> dict[str, Any]:
        if not event_type or not strategy_id:
            raise ContractError("event_type and strategy_id are required")
        if int(sequence) < 0:
            raise ContractError("sequence must be non-negative")
        return {
            "event_type": str(event_type),
            "event_time_utc": format_utc(event_time_utc),
            "strategy_id": str(strategy_id),
            "sequence": int(sequence),
            "payload": payload,
            "payload_hash": sha256_id(payload),
        }

    def append_many(
        self,
        events: Iterable[Mapping[str, Any]],
        *,
        lock_timeout_seconds: float = 30.0,
    ) -> list[dict[str, Any]]:
        """Append one logical event batch with a single lock and fsync.

        Exact retries are idempotent. Singleton event types cannot be rewritten with a
        different payload for the same ``(event_type, strategy_id, sequence)`` key.
        """

        specs = [
            self._spec(
                event_type=str(item["event_type"]),
                event_time_utc=str(item["event_time_utc"]),
                strategy_id=str(item["strategy_id"]),
                sequence=int(item["sequence"]),
                payload=item["payload"],
            )
            for item in events
        ]
        if not specs:
            return []

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(
            self.lock_path, timeout_seconds=lock_timeout_seconds
        ):
            existing = self.verify()
            by_identity: dict[tuple[str, str, int, str], dict[str, Any]] = {
                (
                    str(event["event_type"]),
                    str(event["strategy_id"]),
                    int(event["sequence"]),
                    str(event["payload_hash"]),
                ): event
                for event in existing
            }
            singleton_payloads: dict[tuple[str, str, int], str] = {
                (
                    str(event["event_type"]),
                    str(event["strategy_id"]),
                    int(event["sequence"]),
                ): str(event["payload_hash"])
                for event in existing
                if event["event_type"] in _SINGLETON_EVENT_TYPES
            }
            previous = existing[-1]["event_hash"] if existing else None
            selected: list[dict[str, Any]] = []
            new_events: list[dict[str, Any]] = []
            batch_identity: set[tuple[str, str, int, str]] = set()
            for spec in specs:
                identity = (
                    spec["event_type"],
                    spec["strategy_id"],
                    spec["sequence"],
                    spec["payload_hash"],
                )
                if identity in batch_identity:
                    selected.append(by_identity.get(identity) or next(
                        event for event in new_events
                        if (
                            event["event_type"],
                            event["strategy_id"],
                            event["sequence"],
                            event["payload_hash"],
                        ) == identity
                    ))
                    continue
                batch_identity.add(identity)
                prior = by_identity.get(identity)
                if prior is not None:
                    selected.append(prior)
                    continue
                singleton_key = (
                    spec["event_type"],
                    spec["strategy_id"],
                    spec["sequence"],
                )
                if spec["event_type"] in _SINGLETON_EVENT_TYPES:
                    prior_payload = singleton_payloads.get(singleton_key)
                    if prior_payload is not None and prior_payload != spec["payload_hash"]:
                        raise ContractError(
                            f"conflicting singleton journal event: {singleton_key}"
                        )
                    singleton_payloads[singleton_key] = spec["payload_hash"]
                event = dict(spec)
                event["previous_event_hash"] = previous
                event["event_hash"] = sha256_id(event)
                previous = event["event_hash"]
                new_events.append(event)
                by_identity[identity] = event
                selected.append(event)

            if new_events:
                self._verify_events([*existing, *new_events])
                payload = b"".join(
                    canonical_json_bytes(event) + b"\n" for event in new_events
                )
                descriptor = os.open(
                    self.path,
                    os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                    0o600,
                )
                try:
                    view = memoryview(payload)
                    while view:
                        written = os.write(descriptor, view)
                        if written <= 0:
                            raise OSError("failed to append journal event batch")
                        view = view[written:]
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            self.verify()
            return selected

    def append(
        self,
        *,
        event_type: str,
        event_time_utc: str,
        strategy_id: str,
        sequence: int,
        payload: Any,
    ) -> dict[str, Any]:
        return self.append_many(
            (
                {
                    "event_type": event_type,
                    "event_time_utc": event_time_utc,
                    "strategy_id": strategy_id,
                    "sequence": sequence,
                    "payload": payload,
                },
            )
        )[0]

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.verify())
