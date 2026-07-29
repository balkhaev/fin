from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from finruntime.execution import PaperQuote
from finruntime.journal import write_atomic_json
from finruntime.models import MarketSnapshot, SourceObservation, StrategySnapshot
from finruntime.operations import PaperCyclePaths, run_paper_cycle
from finruntime.portfolio import PaperAccountState
from finruntime.scheduler import (
    PaperCycleEnvelope,
    SchedulerPaths,
    enqueue_envelope,
    run_scheduler_once,
    scheduler_status,
    verify_scheduler,
)


class SchedulerTests(unittest.TestCase):
    def market(self, *, minute: int = 5, quality: str = "ok") -> MarketSnapshot:
        source = SourceObservation(
            source="spot_daily",
            source_timestamp_utc="2026-07-29T00:00:00Z",
            available_at_utc="2026-07-29T00:01:00Z",
            payload_sha256="sha256:" + "1" * 64,
            quality=quality,
        )
        return MarketSnapshot.create(
            as_of_utc="2026-07-29T00:00:00Z",
            decision_time_utc=f"2026-07-29T00:{minute:02d}:00Z",
            sources={"spot_daily": source},
            spot={"BTC/USDT": {"reference_price": "100"}},
        )

    def strategy(
        self,
        market: MarketSnapshot,
        *,
        weight: str = "0.1",
        sequence: int = 1,
    ) -> StrategySnapshot:
        return StrategySnapshot.create(
            strategy_id="v75_atlas_nx",
            strategy_version="runtime-v1",
            decision_time_utc=market.decision_time_utc,
            market_snapshot_id=market.snapshot_id,
            state_sequence=sequence,
            targets={"spot": {"BTC/USDT": weight}, "perp": {}},
            gross_target=weight,
            cash_target=str(1 - float(weight)),
            risk={"gross_cap": "1.05"},
        )

    def quote(self, *, minute: int = 6) -> PaperQuote:
        return PaperQuote(
            instrument="BTC/USDT",
            market_type="spot",
            observed_at_utc=f"2026-07-29T00:{minute:02d}:00Z",
            source_observation_hash="sha256:" + "2" * 64,
            bid="99.95",
            ask="100.05",
            mid="100",
            available_quantity="1000",
        )

    def account(self, *, sequence: int = 0) -> PaperAccountState:
        if sequence == 0:
            return PaperAccountState.empty(
                strategy_id="v75_atlas_nx",
                as_of_utc="2026-07-29T00:00:00Z",
                starting_cash="10000",
            )
        return PaperAccountState.create(
            strategy_id="v75_atlas_nx",
            sequence=sequence,
            as_of_utc="2026-07-29T00:00:00Z",
            cash="10000",
            equity="10000",
            high_water="10000",
        )

    def envelope(
        self,
        account: PaperAccountState,
        *,
        expires: str = "2026-07-30T00:00:00Z",
        source_hash_match: bool = True,
        quality: str = "ok",
    ) -> PaperCycleEnvelope:
        market = self.market(quality=quality)
        return PaperCycleEnvelope.create(
            created_at_utc="2026-07-29T00:06:00Z",
            not_before_utc="2026-07-29T00:06:00Z",
            expires_at_utc=expires,
            account=account,
            market_snapshot=market,
            strategy_snapshot=self.strategy(market),
            quotes=(self.quote(),),
            reference_prices={
                "spot": {"BTC/USDT": {"reference_price": "100"}},
                "perp": {},
            },
            critical_sources=("spot_daily",),
            modelled_cost="1",
            modelled_slippage_bps="8",
            source_hash_match=source_hash_match,
        )

    def initialize(self, root: Path, account: PaperAccountState) -> None:
        path = PaperCyclePaths.under(root, account.strategy_id).account_state
        write_atomic_json(path, account.to_dict())

    def test_enqueued_cycle_executes_and_archives(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = self.account()
            self.initialize(root, account)
            paths = SchedulerPaths.under(root)
            envelope = self.envelope(account)
            queued = enqueue_envelope(paths, envelope)
            self.assertTrue(queued.is_file())
            result = run_scheduler_once(
                paths,
                max_items=3,
                now=datetime(2026, 7, 29, 0, 10, tzinfo=timezone.utc),
            )
            self.assertEqual(result.completed, 1)
            self.assertEqual(result.rejected, 0)
            self.assertTrue(
                (paths.completed / f"{envelope.request_id.removeprefix('sha256:')}.result.json").is_file()
            )
            self.assertTrue(
                PaperCyclePaths.under(root, account.strategy_id).telemetry_csv.is_file()
            )
            status = scheduler_status(paths)
            self.assertEqual(status["queued"], 0)
            self.assertEqual(status["completed"], 1)
            self.assertFalse(status["exchange_submission_available"])
            self.assertTrue(verify_scheduler(paths)["valid"])

    def test_duplicate_enqueue_after_completion_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = self.account()
            self.initialize(root, account)
            paths = SchedulerPaths.under(root)
            envelope = self.envelope(account)
            enqueue_envelope(paths, envelope)
            run_scheduler_once(
                paths,
                now=datetime(2026, 7, 29, 0, 10, tzinfo=timezone.utc),
            )
            destination = enqueue_envelope(paths, envelope)
            self.assertTrue(destination.name.endswith(".result.json"))
            self.assertEqual(scheduler_status(paths)["queued"], 0)

    def test_expired_request_is_rejected_without_account_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = self.account()
            self.initialize(root, account)
            paths = SchedulerPaths.under(root)
            envelope = self.envelope(account, expires="2026-07-29T00:07:00Z")
            enqueue_envelope(paths, envelope)
            result = run_scheduler_once(
                paths,
                now=datetime(2026, 7, 29, 0, 10, tzinfo=timezone.utc),
            )
            self.assertEqual(result.processed, 0)
            self.assertEqual(result.rejected, 1)
            current = json.loads(
                PaperCyclePaths.under(root, account.strategy_id).account_state.read_text()
            )
            self.assertEqual(current["account_hash"], account.account_hash)

    def test_future_account_request_remains_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = self.account()
            self.initialize(root, current)
            paths = SchedulerPaths.under(root)
            future = self.account(sequence=5)
            enqueue_envelope(paths, self.envelope(future))
            result = run_scheduler_once(
                paths,
                now=datetime(2026, 7, 29, 0, 10, tzinfo=timezone.utc),
            )
            self.assertEqual(result.processed, 0)
            self.assertEqual(result.blocked, 1)
            self.assertEqual(scheduler_status(paths)["queued"], 1)

    def test_source_hash_mismatch_halts_without_opening_position(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = self.account()
            self.initialize(root, account)
            paths = SchedulerPaths.under(root)
            enqueue_envelope(paths, self.envelope(account, source_hash_match=False))
            result = run_scheduler_once(
                paths,
                now=datetime(2026, 7, 29, 0, 10, tzinfo=timezone.utc),
            )
            self.assertEqual(result.halted, 1)
            current = json.loads(
                PaperCyclePaths.under(root, account.strategy_id).account_state.read_text()
            )
            self.assertEqual(current["spot_positions"], {})


    def test_processing_request_is_recovered_after_scheduler_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = self.account()
            self.initialize(root, account)
            paths = SchedulerPaths.under(root)
            envelope = self.envelope(account)
            queued = enqueue_envelope(paths, envelope)
            paths.processing.mkdir(parents=True, exist_ok=True)
            processing = paths.processing / queued.name
            queued.replace(processing)

            result = run_scheduler_once(
                paths,
                now=datetime(2026, 7, 29, 0, 10, tzinfo=timezone.utc),
            )
            self.assertEqual(result.completed, 1)
            self.assertFalse(processing.exists())
            self.assertEqual(scheduler_status(paths)["processing"], 0)

    def test_scheduler_recovers_archive_after_cycle_committed_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = self.account()
            self.initialize(root, account)
            paths = SchedulerPaths.under(root)
            envelope = self.envelope(account)
            queued = enqueue_envelope(paths, envelope)
            processing = paths.processing / queued.name
            queued.replace(processing)

            first = run_paper_cycle(
                request=envelope.to_request(),
                paths=PaperCyclePaths.under(root, envelope.strategy_id),
            )
            self.assertFalse(first.restored_from_committed_cycle)
            self.assertTrue(processing.exists())

            recovered = run_scheduler_once(
                paths,
                now=datetime(2026, 7, 29, 0, 10, tzinfo=timezone.utc),
            )
            self.assertEqual(recovered.completed, 1)
            result_path = paths.completed / (
                envelope.request_id.removeprefix("sha256:") + ".result.json"
            )
            archived = json.loads(result_path.read_text())
            self.assertTrue(archived["restored_from_committed_cycle"])

    def test_envelope_seals_starting_account_and_execution_policies(self) -> None:
        account = self.account()
        envelope = self.envelope(account)
        request = envelope.to_request()
        self.assertEqual(request.starting_account.account_hash, account.account_hash)
        self.assertEqual(str(request.risk_limits.gross_cap), "1.10")
        self.assertEqual(str(request.planner_policy.spot_max_slippage_bps), "10")
        self.assertEqual(str(request.broker_policy.participation_rate), "0.10")

        tampered = envelope.to_dict()
        tampered["risk_limits"]["gross_cap"] = "2.0"
        with self.assertRaisesRegex(ValueError, "request_id hash mismatch"):
            PaperCycleEnvelope.from_dict(tampered)

    def test_rejected_raw_request_remains_verifiable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = SchedulerPaths.under(Path(directory))
            paths.ensure()
            (paths.inbox / "broken.json").write_text("{not-json", encoding="utf-8")
            run_scheduler_once(
                paths,
                now=datetime(2026, 7, 29, 0, 10, tzinfo=timezone.utc),
            )
            proof = verify_scheduler(paths)
            self.assertTrue(proof["valid"])
            self.assertEqual(proof["rejected"], 1)

    def test_corrupt_envelope_is_rejected_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = SchedulerPaths.under(root)
            paths.ensure()
            (paths.inbox / "broken.json").write_text("{not-json", encoding="utf-8")
            result = run_scheduler_once(
                paths,
                now=datetime(2026, 7, 29, 0, 10, tzinfo=timezone.utc),
            )
            self.assertEqual(result.rejected, 1)
            self.assertEqual(scheduler_status(paths)["queued"], 0)
            self.assertEqual(scheduler_status(paths)["rejected"], 1)


if __name__ == "__main__":
    unittest.main()
