from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .models import PositionState, PositionStatus, now_ms


class StoreError(RuntimeError):
    pass


class SQLiteStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, timeout=30.0, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=NORMAL")
            self._connection.execute("PRAGMA busy_timeout=30000")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    position_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    opened_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_positions_status
                    ON positions(status, updated_at_ms);

                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp_ms INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    position_id TEXT,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_position
                    ON events(position_id, timestamp_ms);

                CREATE TABLE IF NOT EXISTS state (
                    key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                );
                """
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "SQLiteStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def save_position(self, position: PositionState) -> None:
        payload = position.to_json()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO positions(position_id, status, payload, opened_at_ms, updated_at_ms)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(position_id) DO UPDATE SET
                    status=excluded.status,
                    payload=excluded.payload,
                    updated_at_ms=excluded.updated_at_ms
                """,
                (
                    position.position_id,
                    position.status.value,
                    payload,
                    position.opened_at_ms,
                    position.updated_at_ms,
                ),
            )

    def load_position(self, position_id: str) -> PositionState | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM positions WHERE position_id = ?", (position_id,)
            ).fetchone()
        if row is None:
            return None
        return PositionState.from_json(str(row["payload"]))

    def load_active_positions(self) -> list[PositionState]:
        placeholders = ",".join("?" for _ in (PositionStatus.OPENING, PositionStatus.OPEN, PositionStatus.CLOSING, PositionStatus.ERROR))
        statuses = tuple(
            status.value
            for status in (
                PositionStatus.OPENING,
                PositionStatus.OPEN,
                PositionStatus.CLOSING,
                PositionStatus.ERROR,
            )
        )
        with self._lock:
            rows = self._connection.execute(
                f"SELECT payload FROM positions WHERE status IN ({placeholders}) ORDER BY opened_at_ms",
                statuses,
            ).fetchall()
        return [PositionState.from_json(str(row["payload"])) for row in rows]

    def append_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        position_id: str | None = None,
        timestamp_ms: int | None = None,
    ) -> int:
        timestamp = timestamp_ms or now_ms()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO events(timestamp_ms, event_type, position_id, payload)
                VALUES (?, ?, ?, ?)
                """,
                (timestamp, event_type, position_id, encoded),
            )
            return int(cursor.lastrowid)

    def events(self, limit: int = 100, position_id: str | None = None) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        with self._lock:
            if position_id is None:
                rows = self._connection.execute(
                    """
                    SELECT event_id, timestamp_ms, event_type, position_id, payload
                    FROM events ORDER BY event_id DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT event_id, timestamp_ms, event_type, position_id, payload
                    FROM events WHERE position_id = ? ORDER BY event_id DESC LIMIT ?
                    """,
                    (position_id, limit),
                ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "event_id": int(row["event_id"]),
                    "timestamp_ms": int(row["timestamp_ms"]),
                    "event_type": str(row["event_type"]),
                    "position_id": row["position_id"],
                    "payload": json.loads(str(row["payload"])),
                }
            )
        return result

    def set_state(self, key: str, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        timestamp = now_ms()
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO state(key, payload, updated_at_ms)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    payload=excluded.payload,
                    updated_at_ms=excluded.updated_at_ms
                """,
                (key, encoded, timestamp),
            )

    def get_state(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM state WHERE key = ?", (key,)
            ).fetchone()
        return None if row is None else json.loads(str(row["payload"]))

    def status_summary(self) -> dict[str, Any]:
        with self._lock:
            counts = self._connection.execute(
                "SELECT status, COUNT(*) AS count FROM positions GROUP BY status"
            ).fetchall()
            last_event = self._connection.execute(
                "SELECT event_id, timestamp_ms, event_type, position_id FROM events ORDER BY event_id DESC LIMIT 1"
            ).fetchone()
        return {
            "database": str(self.path),
            "position_counts": {str(row["status"]): int(row["count"]) for row in counts},
            "last_event": (
                None
                if last_event is None
                else {
                    "event_id": int(last_event["event_id"]),
                    "timestamp_ms": int(last_event["timestamp_ms"]),
                    "event_type": str(last_event["event_type"]),
                    "position_id": last_event["position_id"],
                }
            ),
        }
