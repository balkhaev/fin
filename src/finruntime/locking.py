from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from finruntime.canonical import ContractError

try:  # POSIX is the supported deployment target for the runtime services.
    import fcntl
except ImportError:  # pragma: no cover - exercised only on unsupported platforms.
    fcntl = None  # type: ignore[assignment]


class LockUnavailableError(ContractError):
    """Raised when a single-writer runtime lock cannot be acquired."""


@contextmanager
def exclusive_file_lock(
    path: str | Path,
    *,
    timeout_seconds: float = 30.0,
    poll_seconds: float = 0.05,
) -> Iterator[None]:
    """Acquire a process-safe advisory lock for a runtime mutation.

    The FIN runtime is deployed on Linux. Failing to provide POSIX locking is safer than
    silently running multiple writers against the same account or spool.
    """

    if fcntl is None:
        raise LockUnavailableError("POSIX file locking is unavailable on this platform")
    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be non-negative")
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")

    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise LockUnavailableError(
                        f"timed out acquiring runtime lock: {lock_path}"
                    ) from exc
                time.sleep(poll_seconds)
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        os.fsync(descriptor)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
