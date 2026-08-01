from __future__ import annotations

import copy
import json
import math
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from finruntime.registry import get_strategy
from finruntime.strategies._ds40180_common import (
    ASSETS,
    INSTRUMENTS,
    PAPER_ASSET_CAP,
    PAPER_GROSS_CAP,
    STRATEGY_ID,
)
from finruntime.strategies._ds40180_engine import build_engine
from finruntime.strategies._ds40180_forward import (
    load_or_advance_forward_state,
    verify_journal,
)
from finruntime.strategies._ds40180_v2 import (
    apply_funding_guard,
    apply_no_trade_band,
    build_forward_plan,
)
from finruntime.strategies.ds40180_t50c3_paper import (
    _write_atomic,
    compute_forward_state,
)


def synthetic_histories(days: int = 820) -> list[dict[str, object]]:
    start = datetime(2024, 5, 1, tzinfo=UTC)
    histories: list[dict[str, object]] = []
    for asset_index, asset in enumerate(ASSETS):
        bars: dict[str, dict[str, object]] = {}
        price = 15.0 + asset_index * 2.0
        funding: list[dict[str, object]] = []
        bars4h: list[dict[str, object]] = []
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
        for step in range(160):
            observed = start + timedelta(days=days - 27, hours=step * 4)
            close = price * math.exp(-0.0005 * (160 - step))
            bars4h.append(
                {
                    "openTime": int(observed.timestamp() * 1000),
                    "open": close * 1.001,
                    "high": close * 1.005,
                    "low": close * 0.995,
                    "close": close,
                    "volume": 1000,
                    "quoteVolume": 1_000_000,
                    "confirmed": True,
                }
            )
        mark_time = int((start + timedelta(days=days, hours=2)).timestamp() * 1000)
        histories.append(
            {
                "asset": asset,
                "instrumentId": INSTRUMENTS[asset],
                "bars": bars,
                "bars4h": bars4h,
                "liveMark": price * 1.003,
                "markTimeMs": mark_time,
                "quote": {
                    "bidPx": price * 1.0025,
                    "askPx": price * 1.0035,
                    "ts": mark_time,
                },
                "funding": funding,
                "currentFunding": {
                    "fundingRate": 0.0001,
                    "nextFundingRate": 0.0001,
                    "fundingTime": int(
                        (start + timedelta(days=days, hours=8)).timestamp() * 1000
                    ),
                    "nextFundingTime": int(
                        (start + timedelta(days=days, hours=16)).timestamp() * 1000
                    ),
                },
                "warnings": [],
            }
        )
    return histories


class Ds40180T50C3PaperTests(unittest.TestCase):
    def test_frozen_regression_and_exact_absolute_risk_limits(self) -> None:
        engine = build_engine(synthetic_histories(), [])

        self.assertAlmostEqual(engine["riskScale"][-1], 3.0, places=12)
        self.assertEqual(engine["regimeState"][-1], 0)
        target = dict(zip(engine["assets"], engine["target"][-1], strict=True))
        self.assertAlmostEqual(target["BTC"], 0.25, places=12)
        self.assertAlmostEqual(target["LINK"], 0.25, places=12)
        self.assertAlmostEqual(target["XLM"], 0.25, places=12)
        self.assertAlmostEqual(sum(abs(value) for value in target.values()), 0.75)
        self.assertLessEqual(sum(abs(value) for value in target.values()), PAPER_GROSS_CAP)
        self.assertTrue(all(abs(weight) <= PAPER_ASSET_CAP for weight in target.values()))

    def test_latest_close_changes_only_the_next_session_target(self) -> None:
        histories = synthetic_histories()
        baseline = build_engine(histories, [])
        changed = copy.deepcopy(histories)
        latest_date = max(changed[0]["bars"])
        changed[0]["bars"][latest_date]["close"] = (
            float(changed[0]["bars"][latest_date]["close"]) * 7.0
        )
        changed[0]["bars"][latest_date]["high"] = (
            float(changed[0]["bars"][latest_date]["close"]) * 1.01
        )
        changed_engine = build_engine(changed, [])

        self.assertEqual(
            baseline["target"][baseline["latestMarketIndex"]],
            changed_engine["target"][changed_engine["latestMarketIndex"]],
        )
        self.assertNotEqual(
            baseline["target"][baseline["executionIndex"]],
            changed_engine["target"][changed_engine["executionIndex"]],
        )

    def test_early_and_confirmed_bear_have_distinct_slow_budgets(self) -> None:
        engine = build_engine(synthetic_histories(), [])
        self.assertTrue(set(engine["regimeState"]) <= {0, 1, 2})
        for state, long_budget, short_budget in zip(
            engine["regimeState"],
            engine["slowLongBudget"],
            engine["slowShortBudget"],
            strict=True,
        ):
            if state == 1:
                self.assertEqual((long_budget, short_budget), (0.25, 0.25))
            elif state == 2:
                self.assertEqual((long_budget, short_budget), (0.0, 0.50))

    def test_adverse_funding_reduces_only_the_expensive_side(self) -> None:
        histories = synthetic_histories()
        long_target = [0.1] + [0.0] * (len(ASSETS) - 1)
        short_target = [-0.1] + [0.0] * (len(ASSETS) - 1)
        guarded_long, long_details = apply_funding_guard(
            list(ASSETS), long_target, histories
        )
        guarded_short, _short_details = apply_funding_guard(
            list(ASSETS), short_target, histories
        )
        self.assertLess(guarded_long[0], long_target[0])
        self.assertAlmostEqual(guarded_short[0], short_target[0])
        self.assertGreater(long_details["maximumAdverseAnnual"], 0.05)

    def test_no_trade_band_keeps_small_risk_increase_but_allows_reduction(self) -> None:
        histories = synthetic_histories()
        current = [0.10] + [0.0] * (len(ASSETS) - 1)
        increased = [0.101] + [0.0] * (len(ASSETS) - 1)
        reduced = [0.08] + [0.0] * (len(ASSETS) - 1)
        held, held_details = apply_no_trade_band(
            list(ASSETS), current, increased, histories
        )
        cut, _cut_details = apply_no_trade_band(
            list(ASSETS), current, reduced, histories
        )
        self.assertEqual(held[0], current[0])
        self.assertIn(ASSETS[0], held_details["heldAssets"])
        self.assertEqual(cut[0], reduced[0])

    def test_persistent_state_never_rewrites_processed_history(self) -> None:
        histories = synthetic_histories()
        engine = build_engine(histories, [])
        with TemporaryDirectory() as directory:
            snapshot_path = Path(directory) / "snapshot.json"
            state1, live1, persistence1 = load_or_advance_forward_state(
                snapshot_path=snapshot_path,
                engine=engine,
                histories=histories,
                reset_date=sorted(histories[0]["bars"])[-15],
                initial_nav_usd=10_000.0,
            )
            frozen_daily = json.loads(json.dumps(state1["daily"]))
            changed = copy.deepcopy(histories)
            latest_date = max(changed[0]["bars"])
            changed[0]["bars"][latest_date]["close"] *= 5
            changed_engine = build_engine(changed, [])
            state2, live2, persistence2 = load_or_advance_forward_state(
                snapshot_path=snapshot_path,
                engine=changed_engine,
                histories=changed,
                reset_date=sorted(histories[0]["bars"])[-15],
                initial_nav_usd=10_000.0,
            )
            self.assertEqual(state2["daily"], frozen_daily)
            self.assertTrue(any("was not rewritten" in item for item in state2["warnings"]))
            self.assertTrue(verify_journal(Path(persistence2["journalPath"]))["valid"])
            self.assertGreater(live1["navUsd"], 0)
            self.assertGreater(live2["navUsd"], 0)
            self.assertTrue(Path(persistence1["statePath"]).is_file())

    def test_forward_snapshot_exposes_v2_overlays_and_journal(self) -> None:
        histories = synthetic_histories()
        with TemporaryDirectory() as directory:
            snapshot_path = Path(directory) / "snapshot.json"
            snapshot = compute_forward_state(
                histories,
                [],
                snapshot_path=snapshot_path,
                reset_date=sorted(histories[0]["bars"])[-15],
                initial_nav_usd=10_000.0,
            )

            self.assertEqual(snapshot["strategyId"], STRATEGY_ID)
            self.assertEqual(snapshot["strategyVersion"], "okx-paper-v2")
            self.assertEqual(snapshot["schema_version"], 2)
            self.assertIn("covariance", snapshot["overlays"])
            self.assertIn("funding", snapshot["overlays"])
            self.assertIn("crisis4h", snapshot["overlays"])
            self.assertIn("noTrade", snapshot["overlays"])
            self.assertTrue(snapshot["persistence"]["journal"]["valid"])
            self.assertGreater(snapshot["paper"]["navUsd"], 0)
            self.assertLessEqual(snapshot["targetGross"], PAPER_GROSS_CAP)
            self.assertFalse(snapshot["exchange_submission_available"])
            self.assertFalse(snapshot["live_ready"])
            self.assertFalse(snapshot["real_leverage_authorized"])

    def test_atomic_snapshot_is_valid_json(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            _write_atomic(path, {"schema_version": 2, "strategyId": STRATEGY_ID})
            self.assertEqual(json.loads(path.read_text())["strategyId"], STRATEGY_ID)
            self.assertFalse(list(path.parent.glob(".*.tmp")))

    def test_registry_remains_paper_only(self) -> None:
        profile = get_strategy(STRATEGY_ID)
        self.assertEqual(profile.allowed_modes, ("paper", "shadow"))
        self.assertFalse(profile.live_ready)
        self.assertFalse(profile.real_leverage_authorized)
        self.assertEqual(profile.parameters["strategy_version"], "okx-paper-v2")
        self.assertEqual(profile.parameters["target_volatility"], 0.50)
        self.assertEqual(profile.parameters["risk_scale_cap"], 3.0)
        self.assertEqual(profile.parameters["paper_gross_cap"], 1.50)
        self.assertTrue(profile.parameters["persistent_append_only_ledger"])


if __name__ == "__main__":
    unittest.main()
