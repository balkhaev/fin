#!/usr/bin/env python3
"""Run real-market paper trading, the scheduler and Control Room together."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType

RUNTIME_ROOT = Path(os.environ.get("FIN_RUNTIME_ROOT", "/data/runtime"))
STRATEGY_ID = os.environ.get("FIN_PAPER_STRATEGY", "v75_atlas_nx")
STARTING_CASH = os.environ.get("FIN_PAPER_STARTING_CASH", "10000")
DS40180_STARTING_CASH = os.environ.get("FIN_DS40180_STARTING_CASH", "10000")
DS40180_RESET_DATE = os.environ.get("FIN_DS40180_RESET_DATE", "2026-07-31")
DS40180_POLL_SECONDS = os.environ.get("FIN_DS40180_POLL_SECONDS", "300")
DYN_SHADOW_PROFILES = tuple(
    profile.strip()
    for profile in os.environ.get("FIN_DYN_SHADOW_PROFILES", "").split(",")
    if profile.strip()
)
TERMINATION_TIMEOUT_SECONDS = 10.0


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def initialize_account() -> None:
    account_path = RUNTIME_ROOT / STRATEGY_ID / "account_state.json"
    if account_path.is_file():
        print(f"Paper account already initialized: {account_path}", flush=True)
        return
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "finruntime",
            "init-account",
            "--root",
            str(RUNTIME_ROOT),
            "--strategy",
            STRATEGY_ID,
            "--as-of-utc",
            _utc_now(),
            "--starting-cash",
            STARTING_CASH,
        ],
        check=True,
    )


def _terminate(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + TERMINATION_TIMEOUT_SECONDS
    for process in processes:
        if process.poll() is not None:
            continue
        remaining = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main() -> int:
    initialize_account()
    funding_snapshot = RUNTIME_ROOT / "funding_router_snapshot.json"
    consensus_snapshot = RUNTIME_ROOT / "consensus_paper_snapshot.json"
    dyn_snapshot = RUNTIME_ROOT / "dyn_paper_snapshot.json"
    atlas_snapshot = RUNTIME_ROOT / "atlas_nx_r1_paper_snapshot.json"
    ds40180_snapshot = RUNTIME_ROOT / "ds40180_t50c3_paper_snapshot.json"
    funding_environment = dict(os.environ)
    funding_environment.update(
        {
            "FUNDING_ROUTER_DATABASE_PATH": str(
                RUNTIME_ROOT / "funding_router.sqlite3"
            ),
            "FUNDING_ROUTER_SNAPSHOT_PATH": str(funding_snapshot),
        }
    )
    process_specs: list[tuple[list[str], dict[str, str] | None]] = [
        (
            [
                "funding-router",
                "--config",
                "services/funding_router/config.example.toml",
                "paper",
            ],
            funding_environment,
        ),
        (
            [
                sys.executable,
                "scripts/run_consensus_paper.py",
                "--snapshot",
                str(consensus_snapshot),
                "--poll-seconds",
                "60",
            ],
            None,
        ),
        (
            [
                sys.executable,
                "-m",
                "finruntime.strategies.dyn_paper",
                "--snapshot",
                str(dyn_snapshot),
                "--poll-seconds",
                "60",
                "--starting-cash",
                "10000",
            ],
            None,
        ),
        (
            [
                sys.executable,
                "-m",
                "finruntime.strategies.atlas_nx_r1_paper",
                "--snapshot",
                str(atlas_snapshot),
                "--poll-seconds",
                "60",
                "--starting-cash",
                "10000",
            ],
            None,
        ),
        (
            [
                sys.executable,
                "-m",
                "finruntime.strategies.ds40180_t50c3_paper",
                "--snapshot",
                str(ds40180_snapshot),
                "--poll-seconds",
                DS40180_POLL_SECONDS,
                "--reset-date",
                DS40180_RESET_DATE,
                "--starting-cash",
                DS40180_STARTING_CASH,
            ],
            None,
        ),
        (
            [
                "fin-paper-scheduler",
                "daemon",
                "--runtime-root",
                str(RUNTIME_ROOT),
                "--poll-seconds",
                "5",
                "--max-items-per-pass",
                "10",
            ],
            None,
        ),
        (
            [
                "fin-control-room",
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
                "--allow-remote",
                "--runtime-root",
                str(RUNTIME_ROOT),
                "--paper-snapshot",
                str(funding_snapshot),
                "--consensus-snapshot",
                str(consensus_snapshot),
                "--dyn-snapshot",
                str(dyn_snapshot),
                "--atlas-snapshot",
                str(atlas_snapshot),
            ],
            None,
        ),
    ]
    shadow_specs: list[tuple[list[str], dict[str, str] | None]] = []
    for profile in DYN_SHADOW_PROFILES:
        snapshot = RUNTIME_ROOT / f"dyn_{profile}_snapshot.json"
        shadow_specs.append(
            (
                [
                    sys.executable,
                    "-m",
                    "finruntime.strategies.dyn_paper",
                    "--snapshot",
                    str(snapshot),
                    "--poll-seconds",
                    "60",
                    "--starting-cash",
                    "10000",
                    "--profile",
                    profile,
                ],
                None,
            )
        )
    process_specs[3:3] = shadow_specs
    processes: list[subprocess.Popen[bytes]] = []
    stop_requested = False

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        nonlocal stop_requested
        stop_requested = True

    previous_handlers = {
        signum: signal.signal(signum, request_stop)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        for command, environment in process_specs:
            print(f"Starting: {' '.join(command)}", flush=True)
            processes.append(subprocess.Popen(command, env=environment))
        while not stop_requested:
            for process, (command, _environment) in zip(
                processes, process_specs, strict=True
            ):
                return_code = process.poll()
                if return_code is not None:
                    print(
                        "Paper stack child exited "
                        f"({return_code}): {' '.join(command)}",
                        file=sys.stderr,
                        flush=True,
                    )
                    return return_code or 1
            time.sleep(0.5)
        return 0
    finally:
        _terminate(processes)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
