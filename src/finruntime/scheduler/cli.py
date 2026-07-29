from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

from finruntime.canonical import format_utc, parse_utc
from finruntime.io import (
    load_market_snapshot,
    load_paper_account,
    load_paper_quotes,
    load_reference_prices,
    load_strategy_snapshot,
)
from finruntime.operations import PaperCyclePaths

from .core import (
    PaperCycleEnvelope,
    SchedulerPaths,
    enqueue_envelope,
    run_scheduler_once,
    scheduler_status,
    serve_scheduler,
    utc_now,
    verify_scheduler,
)


def _json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def command_enqueue(args: argparse.Namespace) -> int:
    paths = SchedulerPaths.under(args.runtime_root)
    strategy = load_strategy_snapshot(args.strategy_snapshot)
    account_path = args.account_state or PaperCyclePaths.under(
        args.runtime_root, strategy.strategy_id
    ).account_state
    account = load_paper_account(account_path)
    market = load_market_snapshot(args.market_snapshot)
    quotes = load_paper_quotes(args.quotes)
    prices = load_reference_prices(args.reference_prices)
    created = parse_utc(args.created_at_utc) if args.created_at_utc else utc_now()
    not_before = (
        parse_utc(args.not_before_utc) if args.not_before_utc else created
    )
    expires = (
        parse_utc(args.expires_at_utc)
        if args.expires_at_utc
        else created + timedelta(seconds=args.ttl_seconds)
    )
    envelope = PaperCycleEnvelope.create(
        created_at_utc=format_utc(created),
        not_before_utc=format_utc(not_before),
        expires_at_utc=format_utc(expires),
        account=account,
        market_snapshot=market,
        strategy_snapshot=strategy,
        quotes=quotes,
        reference_prices=prices,
        critical_sources=tuple(args.critical_source),
        onchain_sources=tuple(args.onchain_source),
        modelled_cost=args.modelled_cost,
        modelled_slippage_bps=args.modelled_slippage_bps,
        source_hash_match=args.source_hash_match,
        data_stale=args.data_stale,
    )
    destination = enqueue_envelope(paths, envelope)
    _json(
        {
            "status": "queued",
            "request_id": envelope.request_id,
            "strategy_id": envelope.strategy_id,
            "path": str(destination),
            "expected_account_hash": envelope.expected_account_hash,
            "exchange_submission_available": False,
        }
    )
    return 0


def command_run_once(args: argparse.Namespace) -> int:
    result = run_scheduler_once(
        SchedulerPaths.under(args.runtime_root),
        max_items=args.max_items,
        lock_timeout_seconds=args.lock_timeout_seconds,
    )
    _json(asdict(result))
    return 2 if result.rejected or result.halted else 0


def command_daemon(args: argparse.Namespace) -> int:
    return serve_scheduler(
        SchedulerPaths.under(args.runtime_root),
        poll_seconds=args.poll_seconds,
        max_items_per_pass=args.max_items_per_pass,
        lock_timeout_seconds=args.lock_timeout_seconds,
    )


def command_status(args: argparse.Namespace) -> int:
    _json(scheduler_status(SchedulerPaths.under(args.runtime_root)))
    return 0


def command_verify(args: argparse.Namespace) -> int:
    _json(verify_scheduler(SchedulerPaths.under(args.runtime_root)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fin-paper-scheduler",
        description="Process sealed FIN paper-cycle requests without exchange submission.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    enqueue = subparsers.add_parser("enqueue")
    enqueue.add_argument("--runtime-root", type=Path, default=Path("runtime"))
    enqueue.add_argument("--market-snapshot", type=Path, required=True)
    enqueue.add_argument("--strategy-snapshot", type=Path, required=True)
    enqueue.add_argument("--account-state", type=Path)
    enqueue.add_argument("--quotes", type=Path, required=True)
    enqueue.add_argument("--reference-prices", type=Path, required=True)
    enqueue.add_argument("--critical-source", action="append", required=True)
    enqueue.add_argument("--onchain-source", action="append", default=[])
    enqueue.add_argument("--modelled-cost", default="0")
    enqueue.add_argument("--modelled-slippage-bps", default="0")
    enqueue.add_argument(
        "--source-hash-match",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    enqueue.add_argument("--data-stale", action="store_true")
    enqueue.add_argument("--created-at-utc")
    enqueue.add_argument("--not-before-utc")
    enqueue.add_argument("--expires-at-utc")
    enqueue.add_argument("--ttl-seconds", type=int, default=86_400)
    enqueue.set_defaults(function=command_enqueue)

    run_once = subparsers.add_parser("run-once")
    run_once.add_argument("--runtime-root", type=Path, default=Path("runtime"))
    run_once.add_argument("--max-items", type=int, default=10)
    run_once.add_argument("--lock-timeout-seconds", type=float, default=30.0)
    run_once.set_defaults(function=command_run_once)

    daemon = subparsers.add_parser("daemon")
    daemon.add_argument("--runtime-root", type=Path, default=Path("runtime"))
    daemon.add_argument("--poll-seconds", type=float, default=5.0)
    daemon.add_argument("--max-items-per-pass", type=int, default=10)
    daemon.add_argument("--lock-timeout-seconds", type=float, default=30.0)
    daemon.set_defaults(function=command_daemon)

    status = subparsers.add_parser("status")
    status.add_argument("--runtime-root", type=Path, default=Path("runtime"))
    status.set_defaults(function=command_status)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--runtime-root", type=Path, default=Path("runtime"))
    verify.set_defaults(function=command_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "ttl_seconds", 1) <= 0:
        raise SystemExit("--ttl-seconds must be positive")
    return int(args.function(args))


if __name__ == "__main__":
    raise SystemExit(main())
