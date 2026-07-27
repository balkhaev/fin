from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterator

from finruntime.canonical import (
    ContractError,
    canonical_json_bytes,
    format_utc,
    require_sha256,
    sha256_id,
)


class JournalCorruptionError(RuntimeError):
    """Raised when the append-only hash chain cannot be verified."""


def write_atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes(value) + b"\n"
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


class AppendOnlyJournal:
    def __init__(self, path: str | Path):
        self.path = Path(path)

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

    def verify(self) -> list[dict[str, Any]]:
        previous: str | None = None
        events = self._read_raw()
        seen: set[str] = set()
        for line_number, event in enumerate(events, 1):
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
            except ContractError as exc:
                raise JournalCorruptionError(
                    f"journal line {line_number} violates the contract"
                ) from exc
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
            seen.add(event["event_hash"])
            previous = event["event_hash"]
        return events

    def append(
        self,
        *,
        event_type: str,
        event_time_utc: str,
        strategy_id: str,
        sequence: int,
        payload: Any,
    ) -> dict[str, Any]:
        if not event_type or not strategy_id:
            raise ContractError("event_type and strategy_id are required")
        if sequence < 0:
            raise ContractError("sequence must be non-negative")
        events = self.verify()
        event_time = format_utc(event_time_utc)
        payload_hash = sha256_id(payload)
        for existing in events:
            if (
                existing["event_type"] == event_type
                and existing["strategy_id"] == strategy_id
                and existing["sequence"] == int(sequence)
                and existing["payload_hash"] == payload_hash
            ):
                return existing
        previous = events[-1]["event_hash"] if events else None
        event = {
            "event_type": event_type,
            "event_time_utc": event_time,
            "strategy_id": strategy_id,
            "sequence": int(sequence),
            "payload": payload,
            "payload_hash": payload_hash,
            "previous_event_hash": previous,
        }
        event["event_hash"] = sha256_id(event)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = canonical_json_bytes(event) + b"\n"
        descriptor = os.open(
            self.path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            view = memoryview(line)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("failed to append journal event")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self.verify()
        return event

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.verify())
