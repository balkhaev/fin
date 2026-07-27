from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .canonical import canonical_json_text
from .data.availability import evaluate_availability
from .journal import AppendOnlyJournal
from .models import MarketSnapshot, SourceObservation
from .registry import registry_payload


def _load_snapshot(path: Path) -> MarketSnapshot:
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["sources"] = {
        name: SourceObservation(**value)
        for name, value in raw["sources"].items()
    }
    snapshot = MarketSnapshot(**raw)
    snapshot.validate()
    return snapshot


def command_registry(_: argparse.Namespace) -> int:
    print(json.dumps(registry_payload(), ensure_ascii=False, indent=2))
    return 0


def command_validate_snapshot(args: argparse.Namespace) -> int:
    snapshot = _load_snapshot(args.path)
    decision = evaluate_availability(
        snapshot,
        critical_sources=args.critical_source,
        onchain_sources=args.onchain_source,
    )
    print(
        json.dumps(
            {
                "snapshot_id": snapshot.snapshot_id,
                "risk_increase_permitted": decision.risk_increase_permitted,
                "accelerator_permitted": decision.accelerator_permitted,
                "blocking_reasons": decision.blocking_reasons,
                "quality_flags": decision.quality_flags,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if decision.risk_increase_permitted else 2


def command_verify_journal(args: argparse.Namespace) -> int:
    events = AppendOnlyJournal(args.path).verify()
    print(json.dumps({"events": len(events), "valid": True}, indent=2))
    return 0


def command_self_test(_: argparse.Namespace) -> int:
    source = SourceObservation(
        source="spot_daily",
        source_timestamp_utc="2026-07-27T00:00:00Z",
        available_at_utc="2026-07-27T00:01:00Z",
        payload_sha256="sha256:" + "1" * 64,
    )
    snapshot = MarketSnapshot.create(
        as_of_utc="2026-07-27T00:00:00Z",
        decision_time_utc="2026-07-27T00:05:00Z",
        sources={"spot_daily": source},
        spot={"BTC/USDT": {"close": "100000"}},
    )
    snapshot.validate()
    with tempfile.TemporaryDirectory() as directory:
        journal = AppendOnlyJournal(Path(directory) / "events.jsonl")
        event = journal.append(
            event_type="SNAPSHOT_ACCEPTED",
            event_time_utc="2026-07-27T00:05:01Z",
            strategy_id="v75_atlas_nx",
            sequence=1,
            payload=snapshot.to_dict(),
        )
        assert journal.verify()[0]["event_hash"] == event["event_hash"]
    print(
        canonical_json_text(
            {
                "self_test": "passed",
                "live_execution_available": False,
                "snapshot_id": snapshot.snapshot_id,
            }
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="finruntime")
    subparsers = parser.add_subparsers(dest="command", required=True)

    registry = subparsers.add_parser("registry")
    registry.set_defaults(function=command_registry)

    validate = subparsers.add_parser("validate-snapshot")
    validate.add_argument("path", type=Path)
    validate.add_argument("--critical-source", action="append", default=[])
    validate.add_argument("--onchain-source", action="append", default=[])
    validate.set_defaults(function=command_validate_snapshot)

    journal = subparsers.add_parser("verify-journal")
    journal.add_argument("path", type=Path)
    journal.set_defaults(function=command_verify_journal)

    self_test = subparsers.add_parser("self-test")
    self_test.set_defaults(function=command_self_test)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.function(args))
