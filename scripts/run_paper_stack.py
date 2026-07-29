#!/usr/bin/env python3
"""Run the persistent paper scheduler and read-only Control Room together."""

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
    commands = [
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
        [
            "fin-control-room",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--allow-remote",
            "--runtime-root",
            str(RUNTIME_ROOT),
        ],
    ]
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
        for command in commands:
            print(f"Starting: {' '.join(command)}", flush=True)
            processes.append(subprocess.Popen(command))
        while not stop_requested:
            for process, command in zip(processes, commands, strict=True):
                return_code = process.poll()
                if return_code is not None:
                    print(
                        f"Paper stack child exited ({return_code}): {' '.join(command)}",
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
