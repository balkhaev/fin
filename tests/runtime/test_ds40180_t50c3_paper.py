from __future__ import annotations

import copy
import json
import math
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from finruntime.registry import get_strategy
from finruntime.strategies.ds40180_t50c3_paper import (
    ASSETS,
    INSTRUMENTS,
    PAPER_ASSET_CAP,
    PAPER_GROSS_CAP,
    STRATEGY_ID,
    _funding_return_for_weight,
    _gross,
    _write_atomic,
    build_engine,
    compute_forward_state,
)


def synthetic_histories(days: int = 820) -> list[dict[str, object]]:
    start = datetime(2024, 5, 1, tzinfo=UTC)
    histories: list[dict[str, object]] = []
    for asset_index, asset in enumerate(ASSETS):
        bars: dict[str, dict[str, object]] = {}
        price = 15.0 + asset_index * 2.0
        funding: list[dict[str, object]] = []
        for day_index in range(days):
            observed = start + timedelta(days=day_index)
            if day_index < 380:
                drift = 0.0012 + asset_index * 0.00002
            elif day_index < 660:
                drift = -0.0018 - asset_index * 0.00001
            else:
                drift = 0.0010 + asset_index * 0.000015
            cycle = 0.006 * math.sin(day_index / (11 + asset_index % 4))
            price *= math.exp(drift + cycle)
            open_price = price * (1.0 - 0.002 * math.sin(day_index / 3))
            bars[observed.date().isoformat()] = {
                "openTime": int(observed.timestamp() * 1000),
                "open": open_price,
                "high": max(open_price, price) * 1.018,
                "low": min(open_price, price) * 0.982,
                "close": price,
                "volume": 1_000_000 + asset_index * 10_000,
                "quoteVolume": 30_000_000 + asset_index * 1_000_000,
                "confirmed": True,
            }
            if day_index >= days - 60:
                for hour in (8, 16, 24):
                    funding_time = observed + timedelta(hours=hour)
                    funding.append(
                        {
                            "fundingTime": int(funding_time.timestamp() * 1000),
                            "rate": 0.0001,
                        }
                    )
        histories.append(
            {
                "asset": asset,
                "instrumentId": INSTRUMENTS[asset],
                "bars": bars,
                "liveMark": price * 1.003,
                "markTimeMs": int((start + timedelta(days=days)).timestamp() * 1000),
                "funding": funding,
                "warnings": [],
            }
        )
    return histories


class Ds40180T50C3PaperTests(unittest.TestCase):
    def test_frozen_regression_and_exact_risk_limits(self) -> None:
        engine = build_engine(synthetic_histories(), [])
        decision_index = engine["executionIndex"]

        self.assertEqual(engine["marketDates"][-1], "2026-07-29")
        self.assertEqual(engine["executionDate"], "2026-07-30")
        self.assertEqual(engine["dates"][decision_index], "2026-07-30")
        self.assertAlmostEqual(engine["riskScale"][decision_index], 3.0, places=12)
        self.assertFalse(engine["combinedBear"][decision_index])
        target = dict(
            zip(engine["assets"], engine["target"][decision_index], strict=True)
        )
        self.assertAlmostEqual(target["BTC"], 0.30, places=12)
        self.assertAlmostEqual(target["LINK"], 0.30, places=12)
        self.assertAlmostEqual(target["XLM"], 0.30, places=12)
        self.assertAlmostEqual(_gross(engine["target"][decision_index]), 0.90, places=12)
        self.assertLessEqual(_gross(engine["target"][decision_index]), PAPER_GROSS_CAP)
        self.assertTrue(
            all(
                abs(weight) <= PAPER_ASSET_CAP
                for weight in engine["target"][decision_index]
            )
        )

    def test_latest_close_changes_only_the_next_session_target(self) -> None:
        histories = synthetic_histories()
        baseline = build_engine(histories, [])
        changed = copy.deepcopy(histories)
        for history in changed:
            latest_date = max(history["bars"])
            changed_close = float(history["bars"][latest_date]["close"]) * 0.01
            history["bars"][latest_date]["close"] = changed_close
            history["bars"][latest_date]["high"] = max(
                float(history["bars"][latest_date]["open"]), changed_close
            ) * 1.01
            history["bars"][latest_date]["low"] = changed_close * 0.99
        changed_engine = build_engine(changed, [])

        latest_market_index = baseline["latestMarketIndex"]
        for expected, actual in zip(
            baseline["target"][latest_market_index],
            changed_engine["target"][latest_market_index],
            strict=True,
        ):
            self.assertAlmostEqual(actual, expected, places=12)
        self.assertTrue(
            any(
                abs(expected - actual) > 1e-9
                for expected, actual in zip(
                    baseline["target"][baseline["executionIndex"]],
                    changed_engine["target"][changed_engine["executionIndex"]],
                    strict=True,
                )
            )
        )

    def test_new_paper_identity_uses_isolated_capital_and_realized_funding(self) -> None:
        histories = synthetic_histories()
        reset_date = sorted(histories[0]["bars"])[-15]
        snapshot = compute_forward_state(
            histories,
            [],
            reset_date=reset_date,
            initial_nav_usd=10_000.0,
        )

        expected_effective_date = (
            date.fromisoformat(snapshot["asOf"]) + timedelta(days=1)
        ).isoformat()
        self.assertEqual(snapshot["strategyId"], STRATEGY_ID)
        self.assertEqual(snapshot["identityKind"], "new_okx_paper_port")
        self.assertFalse(snapshot["historicalMetricsInherited"])
        self.assertEqual(snapshot["mode"], "paper")
        self.assertEqual(snapshot["effectiveDate"], expected_effective_date)
        self.assertEqual(
            snapshot["paper"]["targetEffectiveDate"], expected_effective_date
        )
        self.assertEqual(snapshot["paper"]["account"]["initialNavUsd"], 10_000.0)
        self.assertEqual(
            snapshot["paper"]["account"]["requestedResetDate"], reset_date
        )
        self.assertGreater(snapshot["paper"]["totalExecutions"], 0)
        self.assertGreater(snapshot["funding"]["actualIntervals"], 0)
        self.assertEqual(snapshot["funding"]["fallbackIntervals"], 0)
        self.assertGreaterEqual(snapshot["funding"]["liveActualIntervals"], 0)
        self.assertGreater(snapshot["paper"]["navUsd"], 0)
        self.assertTrue(math.isfinite(snapshot["paper"]["navUsd"]))
        self.assertLessEqual(snapshot["targetGross"], PAPER_GROSS_CAP)
        self.assertTrue(
            all(
                execution["effectiveDate"] > execution["orderDate"]
                for execution in snapshot["paper"]["executions"]
            )
        )
        self.assertFalse(snapshot["exchange_submission_available"])
        self.assertFalse(snapshot["live_ready"])
        self.assertFalse(snapshot["real_leverage_authorized"])

    def test_positive_funding_debits_long_and_credits_short(self) -> None:
        self.assertAlmostEqual(_funding_return_for_weight(0.5, [0.001]), -0.0005)
        self.assertAlmostEqual(_funding_return_for_weight(-0.5, [0.001]), 0.0005)

    def test_atomic_snapshot_is_valid_json(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            _write_atomic(path, {"schema_version": 1, "strategyId": STRATEGY_ID})
            self.assertEqual(json.loads(path.read_text())["strategyId"], STRATEGY_ID)
            self.assertFalse(list(path.parent.glob(".*.tmp")))

    def test_registry_remains_paper_only(self) -> None:
        profile = get_strategy(STRATEGY_ID)
        self.assertEqual(profile.allowed_modes, ("paper", "shadow"))
        self.assertFalse(profile.live_ready)
        self.assertFalse(profile.real_leverage_authorized)
        self.assertEqual(profile.parameters["target_volatility"], 0.50)
        self.assertEqual(profile.parameters["risk_scale_cap"], 3.0)
        self.assertEqual(profile.parameters["paper_gross_cap"], 1.25)


if __name__ == "__main__":
    unittest.main()
