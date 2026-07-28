#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "research" / "active_v103_v110"
sys.path.insert(0, str(SOURCE_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import signals  # noqa: E402
from cached_signals import process_targets_cached  # noqa: E402


def main() -> None:
    index = pd.date_range("2009-01-01", periods=330, freq="B", tz="UTC")
    rng = np.random.default_rng(4930)
    prices = pd.DataFrame(
        {
            ticker: 100.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, len(index))))
            for ticker in config.UNIVERSE
        },
        index=index,
    )
    cached = process_targets_cached(prices, config.GROUPS)
    cases = (
        ("sector", (63, 126, 252), 2, 0.0),
        ("sector", (21, 63, 126, 252), 4, 0.25),
        ("country", (126, 252), 3, 0.15),
        ("combined", (63, 126, 252), 3, 0.15),
        ("combined", (21, 63, 126, 252), 4, 0.0),
        ("defensive", (126, 252), 4, 0.0),
    )
    for family, lookbacks, top_k, short_cap in cases:
        expected = signals.make_target(
            prices, config.GROUPS, lookbacks, top_k, short_cap, family
        )
        name = (
            f"{family}_l{'-'.join(map(str, lookbacks))}"
            f"_k{top_k}_s{int(short_cap * 100):02d}"
        )
        actual = cached[name]
        pd.testing.assert_frame_equal(
            expected, actual, check_exact=False, rtol=0.0, atol=0.0
        )
    assert list(cached) == list(signals.process_targets(prices.iloc[:20], config.GROUPS))
    print("V493 cached V103 signal equality tests passed")


if __name__ == "__main__":
    main()
