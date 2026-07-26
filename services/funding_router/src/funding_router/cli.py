from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .config import ConfigError, Settings, load_settings
from .execution import LiveAuthorizationError
from .logging import configure_logging
from .paper import PaperTrader
from .service import run_router
from .store import SQLiteStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="funding-router",
        description="Guarded cross-exchange perpetual funding dislocation router",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.toml"),
        help="TOML configuration path (default: config.toml)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("validate", help="validate configuration without network calls")
    scan = subparsers.add_parser("scan", help="scan public market data")
    scan.add_argument("--once", action="store_true", help="run one scan and exit")
    paper = subparsers.add_parser("paper", help="run persistent paper trading")
    paper.add_argument("--once", action="store_true", help="run one paper cycle and exit")
    live = subparsers.add_parser("live", help="run guarded live execution")
    live.add_argument(
        "--confirm-live",
        action="store_true",
        help="second live-trading confirmation; an environment phrase is also required",
    )
    status = subparsers.add_parser("status", help="print persisted router state")
    status.add_argument("--events", type=int, default=20, help="number of recent events")
    return parser


def _settings_summary(settings: Settings) -> dict:
    return {
        "config": str(settings.source_path),
        "database": str(settings.service.database_path),
        "poll_seconds": settings.service.poll_seconds,
        "capital_usdt": settings.risk.capital_usdt,
        "notional_usdt": settings.risk.notional_usdt,
        "live_enabled": settings.live.enabled,
        "live_confirmation_env": settings.live.confirmation_env,
        "enabled_exchanges": [
            {
                "id": item.id,
                "exchange_class": item.exchange_class,
                "markets": list(item.markets),
                "sandbox": item.sandbox,
                "credentials_present": sorted(item.credentials().keys()),
            }
            for item in settings.enabled_exchanges
        ],
    }


def _status(settings: Settings, event_limit: int) -> int:
    with SQLiteStore(settings.service.database_path) as store:
        paper = PaperTrader(settings, store)
        payload = {
            "store": store.status_summary(),
            "active_positions": [item.to_dict() for item in store.load_active_positions()],
            "paper": paper.summary(),
            "events": store.events(limit=max(0, event_limit)),
        }
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config)
        configure_logging(settings.service.log_level)
        if args.command == "validate":
            print(json.dumps(_settings_summary(settings), indent=2, sort_keys=True))
            return 0
        if args.command == "status":
            return _status(settings, args.events)
        if args.command == "scan":
            return asyncio.run(run_router(settings, "scan", once=args.once))
        if args.command == "paper":
            return asyncio.run(run_router(settings, "paper", once=args.once))
        if args.command == "live":
            return asyncio.run(
                run_router(
                    settings,
                    "live",
                    once=False,
                    cli_confirmed=bool(args.confirm_live),
                )
            )
        parser.error(f"unsupported command: {args.command}")
    except (ConfigError, LiveAuthorizationError, ValueError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"fatal: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 1
