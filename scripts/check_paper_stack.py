#!/usr/bin/env python3
"""Fail the container healthcheck unless the paper scheduler is current."""

from __future__ import annotations

import json
from urllib.request import urlopen

BASE_URL = "http://127.0.0.1:8000"
MAX_SCHEDULER_AGE_SECONDS = 20.0
MAX_CONSENSUS_AGE_SECONDS = 180.0
HEALTHY_SCHEDULER_STATES = {"idle", "running"}


def _get_json(path: str) -> dict[str, object]:
    with urlopen(f"{BASE_URL}{path}", timeout=3) as response:
        return json.load(response)


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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
