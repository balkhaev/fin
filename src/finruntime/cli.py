from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from .canonical import canonical_json_text
from .data.availability import evaluate_availability
from .io import (
    load_market_snapshot,
    load_paper_account,
    load_paper_quotes,
    load_reference_prices,
    load_strategy_snapshot,
)
from .journal import AppendOnlyJournal, write_atomic_json
from .models import MarketSnapshot, SourceObservation
from .operations import PaperCyclePaths, PaperCycleRequest, run_paper_cycle, runtime_status
from .portfolio import PaperAccountState
from .registry import get_strategy, registry_payload


def command_registry(_: argparse.Namespace) -> int:
    print(json.dumps(registry_payload(), ensure_ascii=False, indent=2))
    return 0


def command_validate_snapshot(args: argparse.Namespace) -> int:
    snapshot = load_market_snapshot(args.path)
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


def command_init_account(args: argparse.Namespace) -> int:
    profile = get_strategy(args.strategy)
    if args.output is not None:
        path = args.output
    else:
        path = PaperCyclePaths.under(args.root, args.strategy).account_state
    if path.exists() and not args.force:
        existing = load_paper_account(path)
        print(
            json.dumps(
                {
                    "status": "already_initialized",
                    "strategy_id": existing.strategy_id,
                    "account_hash": existing.account_hash,
                    "path": str(path),
                },
                indent=2,
            )
        )
        return 0
    state = PaperAccountState.empty(
        strategy_id=profile.strategy_id,
        as_of_utc=args.as_of_utc,
        starting_cash=args.starting_cash,
    )
    write_atomic_json(path, state.to_dict())
    print(
        json.dumps(
            {
                "status": "initialized",
                "strategy_id": state.strategy_id,
                "account_hash": state.account_hash,
                "path": str(path),
                "live_execution_available": False,
            },
            indent=2,
        )
    )
    return 0


def command_paper_cycle(args: argparse.Namespace) -> int:
    market = load_market_snapshot(args.market_snapshot)
    strategy = load_strategy_snapshot(args.strategy_snapshot)
    paths = PaperCyclePaths.under(args.root, strategy.strategy_id)
    account_path = args.account_state or paths.account_state
    account = load_paper_account(account_path)
    quotes = load_paper_quotes(args.quotes)
    prices = load_reference_prices(args.reference_prices)
    request = PaperCycleRequest(
        market_snapshot=market,
        strategy_snapshot=strategy,
        starting_account=account,
        quotes=quotes,
        reference_prices=prices,
        critical_sources=tuple(args.critical_source),
        onchain_sources=tuple(args.onchain_source),
        modelled_cost=args.modelled_cost,
        modelled_slippage_bps=args.modelled_slippage_bps,
        source_hash_match=args.source_hash_match,
        data_stale=args.data_stale,
    )
    result = run_paper_cycle(request=request, paths=paths)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 2 if result.status == "halt" else 0


def command_status(args: argparse.Namespace) -> int:
    paths = PaperCyclePaths.under(args.root, args.strategy)
    print(json.dumps(runtime_status(paths), ensure_ascii=False, indent=2))
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

    init_account = subparsers.add_parser("init-account")
    init_account.add_argument("--root", type=Path, default=Path("runtime-state"))
    init_account.add_argument("--strategy", required=True)
    init_account.add_argument("--as-of-utc", required=True)
    init_account.add_argument("--starting-cash", required=True)
    init_account.add_argument("--output", type=Path)
    init_account.add_argument("--force", action="store_true")
    init_account.set_defaults(function=command_init_account)

    paper_cycle = subparsers.add_parser("paper-cycle")
    paper_cycle.add_argument("--root", type=Path, default=Path("runtime-state"))
    paper_cycle.add_argument("--market-snapshot", type=Path, required=True)
    paper_cycle.add_argument("--strategy-snapshot", type=Path, required=True)
    paper_cycle.add_argument("--account-state", type=Path)
    paper_cycle.add_argument("--quotes", type=Path, required=True)
    paper_cycle.add_argument("--reference-prices", type=Path, required=True)
    paper_cycle.add_argument("--critical-source", action="append", required=True)
    paper_cycle.add_argument("--onchain-source", action="append", default=[])
    paper_cycle.add_argument("--modelled-cost", default="0")
    paper_cycle.add_argument("--modelled-slippage-bps", default="0")
    paper_cycle.add_argument(
        "--source-hash-match",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    paper_cycle.add_argument("--data-stale", action="store_true")
    paper_cycle.set_defaults(function=command_paper_cycle)

    status = subparsers.add_parser("status")
    status.add_argument("--root", type=Path, default=Path("runtime-state"))
    status.add_argument("--strategy", required=True)
    status.set_defaults(function=command_status)

    self_test = subparsers.add_parser("self-test")
    self_test.set_defaults(function=command_self_test)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.function(args))
