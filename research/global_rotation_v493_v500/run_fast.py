#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import cached_signals
import run_optimized

# Replace only the computational transport used to materialize the exact signal
# book. Equality tests compare representative outputs and insertion order with
# the legacy V103 implementation before the full replay is allowed to run.
run_optimized.signals.process_targets = cached_signals.process_targets_cached

if __name__ == "__main__":
    run_optimized.main()
