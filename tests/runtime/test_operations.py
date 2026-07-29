from __future__ import annotations

import csv
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from finruntime.canonical import ContractError
from finruntime.execution import PaperBrokerPolicy, PaperQuote
from finruntime.journal import AppendOnlyJournal, write_atomic_json
from finruntime.models import MarketSnapshot, SourceObservation, StrategySnapshot
from finruntime.operations import (
    PaperCyclePaths,
    PaperCycleRequest,
    append_telemetry_row_atomic,
    cycle_id_for,
    run_paper_cycle,
    runtime_status,
)
from finruntime.portfolio import PaperAccountState


class OperationsTests(unittest.TestCase):
    def market(self, *, quality: str = "ok") -> MarketSnapshot:
        source = SourceObservation(
            source="spot_daily",
            source_timestamp_utc="2026-07-28T00:00:00Z",
            available_at_utc="2026-07-28T00:01:00Z",
            payload_sha256="sha256:" + "1" * 64,
            quality=quality,
        )
        return MarketSnapshot.create(
            as_of_utc="2026-07-28T00:00:00Z",
            decision_time_utc="2026-07-28T00:05:00Z",
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

    def account(self) -> PaperAccountState:
        return PaperAccountState.empty(
            strategy_id="v75_atlas_nx",
            as_of_utc="2026-07-28T00:05:00Z",
            starting_cash="10000",
        )

    def quote(self) -> PaperQuote:
        return PaperQuote(
            instrument="BTC/USDT",
            market_type="spot",
            observed_at_utc="2026-07-28T00:06:00Z",
            source_observation_hash="sha256:" + "2" * 64,
            bid="99.95",
            ask="100.05",
            mid="100",
            available_quantity="1000",
        )

    def request(
        self,
        *,
        market: MarketSnapshot | None = None,
        account: PaperAccountState | None = None,
        quotes: tuple[PaperQuote, ...] | None = None,
        data_stale: bool = False,
    ) -> PaperCycleRequest:
        market = market or self.market()
        return PaperCycleRequest(
            market_snapshot=market,
            strategy_snapshot=self.strategy(market),
            starting_account=account or self.account(),
            quotes=quotes if quotes is not None else (self.quote(),),
            reference_prices={
                "spot": {"BTC/USDT": {"reference_price": "100"}},
                "perp": {},
            },
            critical_sources=("spot_daily",),
            modelled_cost="1",
            modelled_slippage_bps="8",
            data_stale=data_stale,
            broker_policy=PaperBrokerPolicy(
                spot_commission_bps=__import__("decimal").Decimal("10"),
                perp_commission_bps=__import__("decimal").Decimal("6"),
                proxy_half_spread_bps=__import__("decimal").Decimal("4"),
                impact_bps=__import__("decimal").Decimal("2"),
                participation_rate=__import__("decimal").Decimal("1"),
            ),
        )

    def test_end_to_end_cycle_materializes_state_journal_and_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = PaperCyclePaths.under(directory, "v75_atlas_nx")
            result = run_paper_cycle(request=self.request(), paths=paths)
            self.assertEqual(result.status, "committed")
            self.assertFalse(result.restored_from_committed_cycle)
            self.assertTrue(paths.account_state.exists())
            self.assertTrue(paths.journal.exists())
            self.assertTrue(paths.telemetry_csv.exists())
            cycle = Path(result.cycle_directory)
            for name in (
                "request_identity.json",
                "risk_decision.json",
                "execution_plan.json",
                "fill_events.json",
                "account_state.json",
                "reconciliation.json",
                "forward_telemetry.json",
                "COMMITTED.json",
            ):
                self.assertTrue((cycle / name).is_file(), name)
            events = AppendOnlyJournal(paths.journal).verify()
            self.assertEqual(
                [event["event_type"] for event in events],
                [
                    "SNAPSHOT_ACCEPTED",
                    "TARGET_COMPUTED",
                    "PLAN_CREATED",
                    "FILL_RECORDED",
                    "STATE_COMMITTED",
                    "RECONCILIATION_COMPLETED",
                ],
            )
            with paths.telemetry_csv.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["strategy_id"], "v75_atlas_nx")

    def test_same_cycle_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = PaperCyclePaths.under(directory, "v75_atlas_nx")
            request = self.request()
            first = run_paper_cycle(request=request, paths=paths)
            event_count = len(AppendOnlyJournal(paths.journal).verify())
            second = run_paper_cycle(request=request, paths=paths)
            self.assertEqual(first.cycle_id, second.cycle_id)
            self.assertEqual(first.account_hash, second.account_hash)
            self.assertTrue(second.restored_from_committed_cycle)
            self.assertEqual(len(AppendOnlyJournal(paths.journal).verify()), event_count)
            with paths.telemetry_csv.open(newline="", encoding="utf-8") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 1)

    def test_committed_cycle_restores_missing_state_and_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = PaperCyclePaths.under(directory, "v75_atlas_nx")
            request = self.request()
            first = run_paper_cycle(request=request, paths=paths)
            paths.account_state.unlink()
            paths.telemetry_csv.unlink()
            restored = run_paper_cycle(request=request, paths=paths)
            self.assertTrue(restored.restored_from_committed_cycle)
            self.assertEqual(restored.account_hash, first.account_hash)
            self.assertTrue(paths.account_state.exists())
            self.assertTrue(paths.telemetry_csv.exists())

    def test_stale_market_records_halt_without_risk_increase(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            market = self.market(quality="stale")
            request = self.request(
                market=market,
                quotes=(),
                data_stale=True,
            )
            paths = PaperCyclePaths.under(directory, "v75_atlas_nx")
            result = run_paper_cycle(request=request, paths=paths)
            self.assertEqual(result.status, "halt")
            reconciliation = json.loads(
                (Path(result.cycle_directory) / "reconciliation.json").read_text()
            )
            self.assertEqual(reconciliation["status"], "halt")
            self.assertIn("stale_market_data", reconciliation["alerts"])
            events = AppendOnlyJournal(paths.journal).verify()
            self.assertEqual(events[-1]["event_type"], "HALT_RAISED")
            account = json.loads(paths.account_state.read_text())
            self.assertEqual(account["spot_positions"], {})

    def test_status_reports_latest_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = PaperCyclePaths.under(directory, "v75_atlas_nx")
            run_paper_cycle(request=self.request(), paths=paths)
            status = runtime_status(paths)
            self.assertTrue(status["account_available"])
            self.assertEqual(status["committed_cycles"], 1)
            self.assertEqual(status["telemetry_rows"], 1)
            self.assertFalse(status["live_execution_available"])

    def test_conflicting_telemetry_primary_key_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.csv"
            row = {
                "timestamp": "2026-07-28T00:06:00Z",
                "strategy_id": "v75_atlas_nx",
                "source_bundle_sha256": "sha256:" + "1" * 64,
                "target_hash": "sha256:" + "2" * 64,
                "realized_position_hash": "sha256:" + "3" * 64,
                "gross_target": "0.1",
                "gross_realized": "0.1",
                "turnover": "0.1",
                "modelled_slippage_bps": "8",
                "paper_slippage_bps": "7",
                "net_return": "-0.001",
                "equity": "9990",
                "drawdown": "-0.001",
                "reconciliation_ok": True,
                "source_hash_match": True,
                "data_stale": False,
                "execution_complete": True,
            }
            append_telemetry_row_atomic(path, row)
            changed = dict(row)
            changed["equity"] = "9991"
            with self.assertRaises(ContractError):
                append_telemetry_row_atomic(path, changed)

    def test_cycle_identity_is_bound_to_starting_account(self) -> None:
        market = self.market()
        first = self.request(market=market, account=self.account())
        alternative = PaperAccountState.create(
            strategy_id="v75_atlas_nx",
            sequence=0,
            as_of_utc="2026-07-28T00:05:00Z",
            cash="9999",
            equity="9999",
            high_water="9999",
        )
        second = self.request(market=market, account=alternative)
        self.assertNotEqual(cycle_id_for(first), cycle_id_for(second))


    def test_tampered_commit_marker_blocks_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self.request()
            paths = PaperCyclePaths.under(root, request.strategy_snapshot.strategy_id)
            write_atomic_json(paths.account_state, request.starting_account.to_dict())
            result = run_paper_cycle(request=request, paths=paths)
            committed_path = Path(result.cycle_directory) / "COMMITTED.json"
            committed = json.loads(committed_path.read_text())
            committed["plan_id"] = "sha256:" + "f" * 64
            write_atomic_json(committed_path, committed)
            with self.assertRaisesRegex(ContractError, "plan id mismatch"):
                run_paper_cycle(request=request, paths=paths)

    def test_tampered_cycle_artifact_blocks_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = PaperCyclePaths.under(directory, "v75_atlas_nx")
            request = self.request()
            result = run_paper_cycle(request=request, paths=paths)
            artifact = Path(result.cycle_directory) / "execution_plan.json"
            artifact.write_text('{\"tampered\":true}\n', encoding="utf-8")
            with self.assertRaises(ContractError):
                run_paper_cycle(request=request, paths=paths)

    def test_divergent_global_account_blocks_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = PaperCyclePaths.under(directory, "v75_atlas_nx")
            request = self.request()
            run_paper_cycle(request=request, paths=paths)
            divergent = PaperAccountState.create(
                strategy_id="v75_atlas_nx",
                sequence=request.starting_account.sequence,
                as_of_utc=request.starting_account.as_of_utc,
                cash="9999",
                equity="9999",
                high_water="9999",
            )
            paths.account_state.write_text(
                json.dumps(divergent.to_dict()), encoding="utf-8"
            )
            with self.assertRaises(ContractError):
                run_paper_cycle(request=request, paths=paths)

    def test_concurrent_retry_commits_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = PaperCyclePaths.under(directory, "v75_atlas_nx")
            request = self.request()
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(
                    pool.map(
                        lambda _: run_paper_cycle(request=request, paths=paths),
                        range(2),
                    )
                )
            self.assertEqual(results[0].cycle_id, results[1].cycle_id)
            self.assertEqual(len(AppendOnlyJournal(paths.journal).verify()), 6)
            with paths.telemetry_csv.open(newline="", encoding="utf-8") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 1)

    def test_stale_quality_sets_telemetry_stale_without_caller_flag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            market = self.market(quality="stale")
            request = self.request(market=market, quotes=(), data_stale=False)
            paths = PaperCyclePaths.under(directory, "v75_atlas_nx")
            result = run_paper_cycle(request=request, paths=paths)
            telemetry = json.loads(
                (Path(result.cycle_directory) / "forward_telemetry.json").read_text()
            )
            self.assertTrue(telemetry["data_stale"])
            self.assertEqual(result.status, "halt")


if __name__ == "__main__":
    unittest.main()
