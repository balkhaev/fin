from __future__ import annotations

import math
import unittest
from datetime import UTC, datetime, timedelta

from finruntime.strategies.dyn_paper import (
    ASSETS,
    _percentile_ranks,
    compute_forward_state,
)


class DynPaperTests(unittest.TestCase):
    def test_percentile_ranks_match_fin2_average_tie_ranking(self) -> None:
        ranks = _percentile_ranks([1.0, 2.0, 2.0, None])
        self.assertAlmostEqual(ranks[0] or 0, 1 / 3)
        self.assertAlmostEqual(ranks[1] or 0, 2.5 / 3)
        self.assertAlmostEqual(ranks[2] or 0, 2.5 / 3)
        self.assertIsNone(ranks[3])

    def test_bullish_synthetic_market_opens_paper_positions(self) -> None:
        start = datetime(2025, 9, 1, tzinfo=UTC)
        histories = []
        for asset_index, asset in enumerate(ASSETS[:8]):
            bars = {}
            for day_index in range(330):
                observed = start + timedelta(days=day_index)
                growth = 1.0015 + asset_index * 0.00008
                close = (10 + asset_index) * growth**day_index
                bars[observed.date().isoformat()] = {
                    "openTime": int(observed.timestamp() * 1000),
                    "open": close * 0.995,
                    "high": close * 1.02,
                    "low": close * 0.98,
                    "close": close,
                    "closeTime": int(
                        (observed + timedelta(days=1)).timestamp() * 1000 - 1
                    ),
                    "quoteVolume": 10_000_000 + asset_index * 1_000_000,
                    "closed": True,
                }
            histories.append(
                {
                    "asset": asset,
                    "symbol": f"{asset}USDT",
                    "bars": bars,
                    "liveCandle": list(bars.values())[-1],
                }
            )

        snapshot = compute_forward_state(
            histories,
            [],
            reset_date="2026-07-20",
            initial_nav_usd=10_000.0,
        )

        self.assertEqual(snapshot["strategyId"], "DYN-IV113")
        self.assertEqual(snapshot["paper"]["account"]["initialNavUsd"], 10_000.0)
        self.assertGreater(snapshot["targetGross"], 0)
        self.assertTrue(snapshot["positions"])
        self.assertTrue(math.isfinite(snapshot["paper"]["navUsd"]))
        self.assertEqual(len(snapshot["candles"]), 8)
        self.assertFalse(snapshot["exchange_submission_available"])


if __name__ == "__main__":
    unittest.main()
