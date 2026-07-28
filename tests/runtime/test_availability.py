from __future__ import annotations

import unittest

from finruntime.canonical import ContractError
from finruntime.data.availability import evaluate_availability, seal_sources
from finruntime.models import MarketSnapshot, SourceObservation


class AvailabilityTests(unittest.TestCase):
    def observation(
        self,
        source: str,
        *,
        source_time: str = "2026-07-27T00:00:00Z",
        available: str = "2026-07-27T00:01:00Z",
        quality: str = "ok",
        digest: str = "1",
    ) -> SourceObservation:
        return SourceObservation(
            source=source,
            source_timestamp_utc=source_time,
            available_at_utc=available,
            payload_sha256="sha256:" + digest * 64,
            quality=quality,
        )

    def snapshot(self, sources: dict[str, SourceObservation]) -> MarketSnapshot:
        return MarketSnapshot.create(
            as_of_utc="2026-07-27T00:00:00Z",
            decision_time_utc="2026-07-27T00:05:00Z",
            sources=sources,
        )

    def test_future_available_source_is_rejected(self) -> None:
        with self.assertRaises(ContractError):
            self.snapshot(
                {
                    "spot_daily": self.observation(
                        "spot_daily", available="2026-07-27T00:06:00Z"
                    )
                }
            )

    def test_missing_critical_source_blocks_risk_increase(self) -> None:
        snapshot = self.snapshot({"spot_daily": self.observation("spot_daily")})
        decision = evaluate_availability(
            snapshot,
            critical_sources=["spot_daily", "perp_8h"],
        )
        self.assertFalse(decision.risk_increase_permitted)
        self.assertIn("missing critical source:perp_8h", decision.blocking_reasons)

    def test_stale_onchain_disables_accelerator_only(self) -> None:
        snapshot = self.snapshot(
            {
                "spot_daily": self.observation("spot_daily"),
                "onchain": self.observation(
                    "onchain", source_time="2026-07-24T00:00:00Z"
                ),
            }
        )
        decision = evaluate_availability(
            snapshot,
            critical_sources=["spot_daily"],
            onchain_sources=["onchain"],
        )
        self.assertTrue(decision.risk_increase_permitted)
        self.assertFalse(decision.accelerator_permitted)

    def test_conflicting_payload_for_same_source_is_rejected(self) -> None:
        with self.assertRaises(ContractError):
            seal_sources(
                [
                    self.observation("spot_daily", digest="1"),
                    self.observation("spot_daily", digest="2"),
                ]
            )


if __name__ == "__main__":
    unittest.main()
