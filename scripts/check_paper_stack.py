#!/usr/bin/env python3
"""Fail the container healthcheck unless every paper worker is current."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import urlopen

BASE_URL = "http://127.0.0.1:8000"
RUNTIME_ROOT = Path(os.environ.get("FIN_RUNTIME_ROOT", "/data/runtime"))
DS40180_SNAPSHOT = RUNTIME_ROOT / "ds40180_t50c3_paper_snapshot.json"
MAX_SCHEDULER_AGE_SECONDS = 20.0
MAX_CONSENSUS_AGE_SECONDS = 180.0
MAX_DS40180_AGE_SECONDS = 900.0
HEALTHY_SCHEDULER_STATES = {"idle", "running"}


def _get_json(path: str) -> dict[str, object]:
    with urlopen(f"{BASE_URL}{path}", timeout=3) as response:
        return json.load(response)


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("snapshot timestamp is not a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _check_ds40180_snapshot() -> None:
    if not DS40180_SNAPSHOT.is_file():
        raise RuntimeError("DS-40/180 T50-C3 snapshot is unavailable")
    try:
        snapshot = json.loads(DS40180_SNAPSHOT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"DS-40/180 snapshot is unreadable: {error}") from error
    if not isinstance(snapshot, dict):
        raise TypeError("DS-40/180 snapshot is not a JSON object")
    if snapshot.get("schema_version") != 2:
        raise RuntimeError("unexpected DS-40/180 snapshot schema")
    if snapshot.get("strategyId") != "ds40180_t50c3_okx_paper":
        raise RuntimeError("unexpected DS-40/180 paper identity")
    if snapshot.get("strategyVersion") != "okx-paper-v2":
         raise RuntimeError("unexpected DS-40/180 paper version")
    if snapshot.get("mode") != "paper" or snapshot.get("status") != "ready":
        raise RuntimeError(f"DS-40/180 paper worker is not ready: {snapshot.get('status')}")
    if snapshot.get("exchange_submission_available") is not False:
        raise RuntimeError("DS-40/180 unexpectedly exposes exchange submission")
    if snapshot.get("live_ready") is not False:
        raise RuntimeError("DS-40/180 unexpectedly reports live readiness")
    age_seconds = max(
        0.0,
        (datetime.now(UTC) - _timestamp(snapshot.get("generatedAt"))).total_seconds(),
    )
    if age_seconds > MAX_DS40180_AGE_SECONDS:
        raise RuntimeError(f"DS-40/180 paper snapshot is stale: {age_seconds:.1f}s")
    paper = snapshot.get("paper")
    if not isinstance(paper, dict) or not isinstance(paper.get("navUsd"), (int, float)):
        raise RuntimeError("DS-40/180 paper NAV is unavailable")
    dynamic_cap = snapshot.get("dynamicGrossCap")
    target_gross = snapshot.get("targetGross")
    if not isinstance(dynamic_cap, (int, float)) or not isinstance(target_gross, (int, float)):
        raise RuntimeError("DS-40/180 risk diagnostics are unavailable")
    if target_gross > dynamic_cap + 1e-9 or dynamic_cap > 1.50 + 1e-9:
        raise RuntimeError("DS-40/180 gross limits are inconsistent")
    persistence = snapshot.get("persistence")
    persistence = persistence if isinstance(persistence, dict) else {}
    journal = persistence.get("journal")
    if not isinstance(journal, dict) or journal.get("valid") is not True:
        raise RuntimeError(f"DS-40/180 journal is invalid: {journal}")
    if int(journal.get("events") or 0) < 1:
        raise RuntimeError("DS-40/180 journal is empty")
    for key in ("statePath", "journalPath"):
        value = persistence.get(key)
        if not isinstance(value, str) or not Path(value).is_file():
            raise RuntimeError(f"DS-40/180 persistence file is unavailable: {key}")


def main() -> int:
    health = _get_json("/api/v1/health")
    scheduler_payload = _get_json("/api/v1/scheduler")
    paper = _get_json("/api/v1/paper")
    scheduler = scheduler_payload.get("scheduler")
    if health.get("service") != "fin-control-room":
        raise RuntimeError("unexpected Control Room health response")
    if not isinstance(scheduler, dict) or not scheduler.get("available"):
        raise RuntimeError("paper scheduler status is unavailable")
    if (
        scheduler.get("state") not in HEALTHY_SCHEDULER_STATES
        or scheduler.get("health") != "healthy"
    ):
        raise RuntimeError(f"paper scheduler is not healthy: {scheduler}")
    age_seconds = scheduler.get("age_seconds")
    if (
        not isinstance(age_seconds, (int, float))
        or age_seconds > MAX_SCHEDULER_AGE_SECONDS
    ):
        raise RuntimeError(f"paper scheduler heartbeat is stale: {age_seconds}")
    if paper.get("mode") != "paper" or paper.get("health") != "healthy":
        raise RuntimeError(f"funding paper worker is not healthy: {paper}")
    paper_age_seconds = paper.get("age_seconds")
    if (
        not isinstance(paper_age_seconds, (int, float))
        or paper_age_seconds > MAX_SCHEDULER_AGE_SECONDS
    ):
        raise RuntimeError(f"funding paper snapshot is stale: {paper_age_seconds}")
    consensus = health.get("consensus_paper")
    if not isinstance(consensus, dict) or not consensus.get("available"):
        raise RuntimeError("WIF/DOT paper worker snapshot is unavailable")
    if consensus.get("health") != "healthy":
        raise RuntimeError(f"WIF/DOT paper worker is not healthy: {consensus}")
    consensus_age_seconds = consensus.get("age_seconds")
    if (
        not isinstance(consensus_age_seconds, (int, float))
        or consensus_age_seconds > MAX_CONSENSUS_AGE_SECONDS
    ):
        raise RuntimeError(f"WIF/DOT paper snapshot is stale: {consensus_age_seconds}")
    strategies = health.get("strategies")
    if not isinstance(strategies, dict):
        raise TypeError("strategy health is unavailable")
    dyn = strategies.get("dyn-iv113")
    if not isinstance(dyn, dict) or dyn.get("status") != "running":
        raise RuntimeError(f"DYN paper worker is not healthy: {dyn}")
    atlas = strategies.get("atlas-nx")
    if not isinstance(atlas, dict) or atlas.get("status") != "running":
        raise RuntimeError(f"Atlas NX R1 paper worker is not healthy: {atlas}")
    ds40180 = strategies.get("ds40180-t50c3")
    if not isinstance(ds40180, dict) or ds40180.get("status") != "running":
        raise RuntimeError(f"DS-40/180 paper card is not healthy: {ds40180}")
    _check_ds40180_snapshot()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
