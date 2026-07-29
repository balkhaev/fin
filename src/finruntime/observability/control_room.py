from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from finruntime.journal import AppendOnlyJournal, JournalCorruptionError

from .telemetry import iso_utc, parse_timestamp, read_telemetry, telemetry_incidents


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _number(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _incident_id(item: Mapping[str, object]) -> str:
    payload = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _incident(
    *,
    timestamp: object,
    strategy_id: str,
    severity: str,
    category: str,
    title: str,
    detail: str,
    cycle_id: str | None = None,
) -> dict[str, object]:
    try:
        parsed = parse_timestamp(timestamp)
    except (TypeError, ValueError):
        parsed = _now()
    item: dict[str, object] = {
        "timestamp": iso_utc(parsed),
        "strategy_id": strategy_id,
        "severity": severity,
        "category": category,
        "title": title,
        "detail": detail,
        "cycle_id": cycle_id,
        "resolved": False,
    }
    item["incident_id"] = _incident_id(item)
    return item


def _deduplicate(items: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    unique = {str(item["incident_id"]): item for item in items}
    return sorted(unique.values(), key=lambda item: parse_timestamp(item["timestamp"]), reverse=True)


def _strategy_roots(root: Path) -> list[Path]:
    if not root.exists():
        return []
    paths: list[Path] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if any((child / name).exists() for name in ("forward_telemetry.csv", "events.jsonl", "account_state.json", "cycles")):
            paths.append(child)
    return paths


def _account(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    raw = _read_json(path)
    return {
        "sequence": int(raw.get("sequence", 0)),
        "as_of_utc": raw.get("as_of_utc"),
        "cash": _number(raw.get("cash")),
        "equity": _number(raw.get("equity")),
        "high_water": _number(raw.get("high_water")),
        "account_hash": raw.get("account_hash"),
        "spot_position_count": len(raw.get("spot_positions") or {}),
        "perp_position_count": len(raw.get("perp_positions") or {}),
    }


def _cycle_state(strategy_root: Path, strategy_id: str) -> tuple[dict[str, int], list[dict[str, object]]]:
    counts = {"committed": 0, "warn": 0, "halt": 0}
    incidents: list[dict[str, object]] = []
    cycle_root = strategy_root / "cycles"
    if not cycle_root.exists():
        return counts, incidents
    for marker in sorted(cycle_root.glob("*/COMMITTED.json")):
        counts["committed"] += 1
        cycle_id = marker.parent.name
        try:
            committed = _read_json(marker)
            primary_key = committed.get("telemetry_primary_key") or [None]
            timestamp = primary_key[0] if isinstance(primary_key, list) and primary_key else None
            reconciliation_path = marker.parent / "reconciliation.json"
            reconciliation = _read_json(reconciliation_path) if reconciliation_path.is_file() else {}
            alerts = reconciliation.get("alerts") if isinstance(reconciliation.get("alerts"), list) else []
            status = str(committed.get("reconciliation_status") or committed.get("status") or "unknown")
            if status == "halt" or committed.get("status") == "halt":
                counts["halt"] += 1
                incidents.append(
                    _incident(
                        timestamp=timestamp,
                        strategy_id=strategy_id,
                        severity="halt",
                        category="halt_cycle",
                        title="Paper cycle halted",
                        detail=", ".join(str(value) for value in alerts) or "Committed cycle has HALT status.",
                        cycle_id=cycle_id,
                    )
                )
            elif status == "warn":
                counts["warn"] += 1
                incidents.append(
                    _incident(
                        timestamp=timestamp,
                        strategy_id=strategy_id,
                        severity="warn",
                        category="warn_cycle",
                        title="Paper cycle warning",
                        detail=", ".join(str(value) for value in alerts) or "Committed cycle has WARN status.",
                        cycle_id=cycle_id,
                    )
                )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            counts["halt"] += 1
            incidents.append(
                _incident(
                    timestamp=None,
                    strategy_id=strategy_id,
                    severity="halt",
                    category="invalid_runtime_artifact",
                    title="Invalid committed cycle artifact",
                    detail=f"{marker}: {error}",
                    cycle_id=cycle_id,
                )
            )
    return counts, incidents


def _strategy_snapshot(strategy_root: Path, now: datetime, stale_after_seconds: int) -> tuple[dict[str, object], list[dict[str, object]]]:
    strategy_id = strategy_root.name
    incidents: list[dict[str, object]] = []
    errors: list[str] = []
    rows: list[dict[str, object]] = []
    try:
        rows = read_telemetry(strategy_root / "forward_telemetry.csv")
        incidents.extend(telemetry_incidents(rows, strategy_id))
    except (OSError, ValueError) as error:
        errors.append(f"telemetry: {error}")
        incidents.append(_incident(timestamp=None, strategy_id=strategy_id, severity="halt", category="invalid_runtime_artifact", title="Invalid forward telemetry", detail=str(error)))

    journal_events = 0
    journal_path = strategy_root / "events.jsonl"
    if journal_path.exists():
        try:
            journal_events = len(AppendOnlyJournal(journal_path).verify())
        except (JournalCorruptionError, OSError, ValueError) as error:
            errors.append(f"journal: {error}")
            incidents.append(_incident(timestamp=None, strategy_id=strategy_id, severity="halt", category="journal_corruption", title="Journal integrity failure", detail=str(error)))

    try:
        account = _account(strategy_root / "account_state.json")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        account = None
        errors.append(f"account: {error}")
        incidents.append(_incident(timestamp=None, strategy_id=strategy_id, severity="halt", category="invalid_runtime_artifact", title="Invalid account state", detail=str(error)))

    cycle_counts, cycle_incidents = _cycle_state(strategy_root, strategy_id)
    incidents.extend(cycle_incidents)
    latest = rows[-1] if rows else None
    latest_time = parse_timestamp(latest["timestamp"]) if latest else None
    freshness_seconds = (now - latest_time).total_seconds() if latest_time else None
    feed_stale = freshness_seconds is not None and freshness_seconds > stale_after_seconds
    if feed_stale:
        incidents.append(_incident(timestamp=latest["timestamp"], strategy_id=strategy_id, severity="warn", category="runtime_feed_stale", title="Runtime feed is stale", detail=f"Latest observation is {freshness_seconds / 3600:.1f} hours old."))

    target_changes = 0
    previous_hash: str | None = None
    for row in rows:
        current_hash = str(row["target_hash"])
        if previous_hash is not None and current_hash != previous_hash:
            target_changes += 1
        previous_hash = current_hash

    modelled_slippage = sum(float(row["modelled_slippage_bps"]) for row in rows)
    paper_slippage = sum(float(row["paper_slippage_bps"]) for row in rows)
    slippage_ratio = paper_slippage / modelled_slippage if modelled_slippage > 0 else (None if paper_slippage == 0 else math.inf)
    latest_equity = float(latest["equity"]) if latest else (_number(account.get("equity")) if account else None)
    first_equity = float(rows[0]["equity"]) if rows else None
    cumulative_return = latest_equity / first_equity - 1.0 if latest_equity is not None and first_equity not in {None, 0.0} else None
    max_drawdown = min((float(row["drawdown"]) for row in rows), default=None)

    latest_halt = bool(latest and (not latest["reconciliation_ok"] or not latest["source_hash_match"] or latest["data_stale"]))
    latest_warn = bool(latest and not latest["execution_complete"])
    if errors or latest_halt or cycle_counts["halt"]:
        health = "halt"
    elif latest_warn or feed_stale or cycle_counts["warn"] or (slippage_ratio is not None and slippage_ratio > 1.5):
        health = "warn"
    elif rows or account:
        health = "healthy"
    else:
        health = "idle"

    return {
        "strategy_id": strategy_id,
        "strategy_root": str(strategy_root),
        "health": health,
        "account": account,
        "observation_count": len(rows),
        "committed_cycles": cycle_counts["committed"],
        "halt_cycles": cycle_counts["halt"],
        "warn_cycles": cycle_counts["warn"],
        "journal_events": journal_events,
        "target_changes": target_changes,
        "first_timestamp": rows[0]["timestamp"] if rows else None,
        "latest_timestamp": latest["timestamp"] if latest else (account.get("as_of_utc") if account else None),
        "freshness_seconds": freshness_seconds,
        "feed_stale": feed_stale,
        "latest_equity": latest_equity,
        "cumulative_return": cumulative_return,
        "max_drawdown": max_drawdown,
        "latest_gross_target": float(latest["gross_target"]) if latest else None,
        "latest_gross_realized": float(latest["gross_realized"]) if latest else None,
        "total_turnover": sum(float(row["turnover"]) for row in rows),
        "slippage_ratio": slippage_ratio,
        "reconciliation_breaks": sum(not bool(row["reconciliation_ok"]) for row in rows),
        "source_hash_mismatches": sum(not bool(row["source_hash_match"]) for row in rows),
        "stale_rows": sum(bool(row["data_stale"]) for row in rows),
        "incomplete_execution_rows": sum(not bool(row["execution_complete"]) for row in rows),
        "source_errors": errors,
        "latest_telemetry": latest,
    }, _deduplicate(incidents)


def _optional_json(root: Path, *candidates: str) -> tuple[dict[str, Any] | None, str | None]:
    for relative in candidates:
        path = root / relative
        if path.is_file():
            try:
                return _read_json(path), str(path)
            except (OSError, ValueError, json.JSONDecodeError):
                return None, str(path)
    return None, None



def _scheduler_snapshot(
    root: Path,
    current: datetime,
    stale_after_seconds: int,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    scheduler_root = root / ".scheduler"
    status_path = scheduler_root / "status.json"
    events_path = scheduler_root / "events.jsonl"
    incidents: list[dict[str, object]] = []
    status: dict[str, Any] = {}
    errors: list[str] = []
    if status_path.is_file():
        try:
            status = _read_json(status_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"invalid scheduler status: {exc}")
    event_count = 0
    if events_path.exists():
        try:
            event_count = len(AppendOnlyJournal(events_path).verify())
        except (OSError, JournalCorruptionError, ValueError) as exc:
            errors.append(f"scheduler journal verification failed: {exc}")
    generated = status.get("generated_at_utc")
    age_seconds: float | None = None
    if generated:
        try:
            age_seconds = max(0.0, (current - parse_timestamp(generated)).total_seconds())
        except (TypeError, ValueError):
            errors.append("scheduler heartbeat timestamp is invalid")
    state = str(status.get("state") or ("idle" if not status_path.exists() else "unknown"))
    health = "idle"
    if errors or state == "halt":
        health = "halt"
    elif state == "warn":
        health = "warn"
    elif age_seconds is not None and age_seconds > stale_after_seconds:
        health = "warn"
        errors.append(f"scheduler heartbeat is stale by {age_seconds:.0f}s")
    elif status_path.exists():
        health = "healthy" if state in {"running", "idle"} else "idle"
    for error in errors:
        incidents.append(
            _incident(
                timestamp=generated or current,
                strategy_id="fin-paper-scheduler",
                severity="halt" if health == "halt" else "warn",
                category="scheduler_integrity" if "journal" in error or "invalid" in error else "scheduler_stale",
                title="Paper scheduler requires attention",
                detail=error,
            )
        )
    last_error = status.get("last_error")
    if last_error and not errors:
        incidents.append(
            _incident(
                timestamp=generated or current,
                strategy_id="fin-paper-scheduler",
                severity="halt" if state == "halt" else "warn",
                category="scheduler_last_error",
                title="Paper scheduler reported an error",
                detail=str(last_error),
            )
        )
    return {
        "available": status_path.is_file(),
        "health": health,
        "state": state,
        "generated_at_utc": generated,
        "age_seconds": age_seconds,
        "queued": int(status.get("queued", 0) or 0),
        "processing": int(status.get("processing", 0) or 0),
        "completed": int(status.get("completed", 0) or 0),
        "rejected": int(status.get("rejected", 0) or 0),
        "last_request_id": status.get("last_request_id"),
        "last_result": status.get("last_result"),
        "last_error": last_error,
        "heartbeat_sequence": int(status.get("heartbeat_sequence", 0) or 0),
        "event_count": event_count,
        "status_source": str(status_path) if status_path.is_file() else None,
        "events_source": str(events_path) if events_path.exists() else None,
        "exchange_submission_available": False,
    }, incidents

def build_runtime_snapshot(
    runtime_root: str | Path,
    *,
    now: datetime | None = None,
    stale_after_seconds: int = 172_800,
    incident_limit: int = 100,
) -> dict[str, object]:
    root = Path(runtime_root).expanduser().resolve()
    current = (now or _now()).astimezone(timezone.utc)
    strategies: list[dict[str, object]] = []
    incidents: list[dict[str, object]] = []
    for strategy_root in _strategy_roots(root):
        summary, current_incidents = _strategy_snapshot(strategy_root, current, stale_after_seconds)
        strategies.append(summary)
        incidents.extend(current_incidents)
    scheduler, scheduler_incidents = _scheduler_snapshot(
        root, current, stale_after_seconds
    )
    incidents.extend(scheduler_incidents)
    incidents = _deduplicate(incidents)

    rank = {"idle": 0, "healthy": 1, "warn": 2, "halt": 3}
    health_values = [str(item["health"]) for item in strategies]
    health_values.append(str(scheduler["health"]))
    status = max(health_values, key=lambda value: rank.get(value, 3), default="idle")
    latest_times = [parse_timestamp(item["latest_timestamp"]) for item in strategies if item.get("latest_timestamp")]

    v517_state, v517_state_source = _optional_json(root, "v517_state.json", "v517_tristate_guard_shadow/v517_state.json", "v517_tristate_guard_shadow/state.json")
    v517_decision, v517_decision_source = _optional_json(root, "v517_decision.json", "v517_tristate_guard_shadow/v517_decision.json", "v517_tristate_guard_shadow/decision.json")
    market_state, market_state_source = _optional_json(root, "market_state.json", "market/latest_state.json", "market/CURRENT_MARKET_CONTEXT.json")

    return {
        "schema_version": 1,
        "generated_at_utc": iso_utc(current),
        "runtime_root": str(root),
        "runtime_root_exists": root.exists(),
        "status": status,
        "exchange_submission_available": False,
        "live_ready": False,
        "real_leverage_authorized": False,
        "aggregate": {
            "strategy_count": len(strategies),
            "observation_count": sum(int(item["observation_count"]) for item in strategies),
            "committed_cycles": sum(int(item["committed_cycles"]) for item in strategies),
            "critical_incidents": sum(item["severity"] == "halt" for item in incidents),
            "warning_incidents": sum(item["severity"] == "warn" for item in incidents),
            "latest_timestamp": iso_utc(max(latest_times)) if latest_times else None,
            "scheduler_queue": int(scheduler["queued"]),
            "scheduler_rejected": int(scheduler["rejected"]),
        },
        "scheduler": scheduler,
        "strategies": strategies,
        "incidents": incidents[: max(0, incident_limit)],
        "v517": {
            "state": v517_state,
            "decision": v517_decision,
            "state_source": v517_state_source,
            "decision_source": v517_decision_source,
        },
        "market_state": market_state,
        "market_state_source": market_state_source,
        "safety": {
            "read_only": True,
            "orders_endpoint": False,
            "secrets_loaded": False,
            "capital_changes_permitted": False,
        },
    }


def snapshot_digest(value: Mapping[str, object]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
