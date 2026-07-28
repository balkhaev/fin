#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "research" / "active_v103_v110"
sys.path.insert(0, str(SOURCE_ROOT))

import config  # noqa: E402
import signals  # noqa: E402


def main() -> None:
    index = pd.date_range("2010-01-01", periods=360, freq="B", tz="UTC")
    rng = np.random.default_rng(493)
    prices = pd.DataFrame(
        {
            ticker: 100.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, len(index))))
            for ticker in config.UNIVERSE
        },
        index=index,
    )
    first = signals.make_target(
        prices,
        config.GROUPS,
        (63, 126, 252),
        top_k=3,
        short_cap=0.15,
        family="combined",
    )
    changed = prices.copy()
    changed.iloc[-1] *= 7.0
    second = signals.make_target(
        changed,
        config.GROUPS,
        (63, 126, 252),
        top_k=3,
        short_cap=0.15,
        family="combined",
    )
    pd.testing.assert_frame_equal(
        first.iloc[:-1],
        second.iloc[:-1],
        check_exact=False,
        rtol=1e-13,
        atol=1e-13,
    )
    print("V493 focused V103 signal causality test passed")


if __name__ == "__main__":
    main()
