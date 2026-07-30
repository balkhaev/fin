from __future__ import annotations

import copy
import hashlib
import json
import math
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from finruntime.provenance import parse_strategy_migration
from finruntime.strategies.atlas_nx_r1_paper import (
    ASSETS,
    build_engine,
    compute_forward_state,
    onchain_accelerator_scale,
    ratchet_stage,
)


def synthetic_histories() -> list[dict[str, object]]:
    start = datetime(2025, 9, 1, tzinfo=UTC)
    histories: list[dict[str, object]] = []
    for asset_index, asset in enumerate(ASSETS):
        bars: dict[str, dict[str, object]] = {}
        for day_index in range(335):
            observed = start + timedelta(days=day_index)
            growth = 1.0008 + asset_index * 0.00008
            close = (20 + asset_index) * growth**day_index
            bars[observed.date().isoformat()] = {
                "openTime": int(observed.timestamp() * 1000),
                "open": close * 0.997,
                "high": close * 1.02,
                "low": close * 0.98,
                "close": close,
                "closeTime": int((observed + timedelta(days=1)).timestamp() * 1000 - 1),
                "quoteVolume": 20_000_000 + asset_index * 1_000_000,
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
    return histories


class AtlasNxR1PaperTests(unittest.TestCase):
    def test_validated_migration_hashes_match_committed_sources(self) -> None:
        root = Path(__file__).parents[2]
        raw = json.loads(
            (
                root
                / "docs"
                / "checkpoints"
                / "runtime-v1"
                / "ATLAS_NX_R1_MIGRATION.json"
            ).read_text(encoding="utf-8")
        )
        migration = parse_strategy_migration(raw)
        self.assertEqual(migration.status, "validated")
        self.assertEqual(migration.successor_strategy_id, "atlas_nx_r1")
        manifests = (
            migration.source_audits,
            migration.successor_source_hashes,
            migration.regression_fixture_hashes,
        )
        for manifest in manifests:
            for relative_path, expected in manifest.items():
                repository_bytes = (
                    (root / relative_path).read_bytes().replace(b"\r\n", b"\n")
                )
                actual = hashlib.sha256(repository_bytes).hexdigest()
                self.assertEqual(f"sha256:{actual}", expected, relative_path)

    def test_committed_regression_fixture(self) -> None:
        fixture = json.loads(
            (
                Path(__file__).parents[1] / "fixtures" / "atlas_nx_r1_regression.json"
            ).read_text(encoding="utf-8")
        )
        engine = build_engine(synthetic_histories(), [])
        snapshot = compute_forward_state(
            synthetic_histories(),
            [],
            reset_date=fixture["paper"]["reset_date"],
            initial_nav_usd=fixture["paper"]["initial_nav_usd"],
        )
        expected = fixture["expected"]
        self.assertEqual(engine["dates"][-1], expected["as_of"])
        actual_weights = dict(zip(engine["assets"], engine["target"][-1], strict=True))
        self.assertEqual(actual_weights.keys(), expected["target_weights"].keys())
        for asset, expected_weight in expected["target_weights"].items():
            self.assertAlmostEqual(actual_weights[asset], expected_weight, places=14)
        self.assertAlmostEqual(snapshot["targetGross"], expected["target_gross"])
        self.assertAlmostEqual(snapshot["paper"]["navUsd"], expected["paper_nav_usd"])
        self.assertEqual(
            snapshot["paper"]["totalExecutions"], expected["total_executions"]
        )
        self.assertEqual(snapshot["ratchetStage"], expected["ratchet_stage"])
        self.assertEqual(
            snapshot["volatilityMultiplier"], expected["volatility_multiplier"]
        )

    def test_reconstructed_strategy_runs_with_its_own_identity_and_capital(
        self,
    ) -> None:
        snapshot = compute_forward_state(
            synthetic_histories(),
            [],
            reset_date="2026-07-20",
            initial_nav_usd=10_000.0,
        )

        self.assertEqual(snapshot["strategyId"], "atlas_nx_r1")
        self.assertEqual(snapshot["predecessorStrategyId"], "v75_atlas_nx")
        self.assertEqual(snapshot["paper"]["account"]["initialNavUsd"], 10_000.0)
        self.assertEqual(snapshot["onchainAcceleratorScale"], 0.0)
        self.assertEqual(snapshot["onchainStatus"], "disabled_stale_or_missing")
        self.assertGreater(snapshot["targetGross"], 0.0)
        self.assertTrue(snapshot["positions"])
        self.assertTrue(math.isfinite(snapshot["paper"]["navUsd"]))
        self.assertEqual(len(snapshot["candles"]), len(ASSETS))
        self.assertFalse(snapshot["exchange_submission_available"])

    def test_latest_close_cannot_change_already_executable_target(self) -> None:
        histories = synthetic_histories()
        baseline = build_engine(histories, [])
        changed = copy.deepcopy(histories)
        last_bar = list(changed[0]["bars"].values())[-1]
        last_bar["close"] = float(last_bar["close"]) * 8
        changed_engine = build_engine(changed, [])

        self.assertEqual(baseline["dates"], changed_engine["dates"])
        self.assertEqual(baseline["target"][-1], changed_engine["target"][-1])

    def test_delisted_asset_is_excluded_without_stopping_the_portfolio(self) -> None:
        histories = synthetic_histories()
        eos = next(history for history in histories if history["asset"] == "EOS")
        retained = list(eos["bars"].items())[:-100]
        eos["bars"] = dict(retained)
        eos["liveCandle"] = retained[-1][1]

        engine = build_engine(histories, [])

        self.assertNotIn("EOS", engine["assets"])
        self.assertIn("EOSUSDT", engine["inactiveSymbols"])
        self.assertGreater(sum(abs(value) for value in engine["target"][-1]), 0.0)

    def test_high_water_ratchet_is_irreversible(self) -> None:
        self.assertEqual(ratchet_stage(10_000.0, 10_000.0, 0), 0)
        self.assertEqual(ratchet_stage(18_000.0, 10_000.0, 0), 1)
        self.assertEqual(ratchet_stage(26_000.0, 10_000.0, 1), 2)
        self.assertEqual(ratchet_stage(11_000.0, 10_000.0, 2), 2)

    def test_stale_or_missing_onchain_data_fails_closed(self) -> None:
        now = datetime(2026, 7, 30, tzinfo=UTC)
        self.assertEqual(onchain_accelerator_scale(None, now, 1.0, 0), 0.0)
        self.assertEqual(
            onchain_accelerator_scale(now - timedelta(hours=49), now, 1.0, 0),
            0.0,
        )
        self.assertGreater(
            onchain_accelerator_scale(now - timedelta(hours=1), now, 1.0, 0),
            0.0,
        )


if __name__ == "__main__":
    unittest.main()
