from __future__ import annotations

import asyncio
import json
import logging
import signal
from collections.abc import Mapping
from pathlib import Path

from .config import Settings
from .execution import LiveExecutor, authorize_live
from .gateways import CCXTGateway, ExchangeGateway
from .logging import log_event
from .models import MarketSnapshot, PositionState, PositionStatus, ScanResult, now_ms
from .paper import PaperTrader
from .scanner import FundingScanner
from .store import SQLiteStore

LOGGER = logging.getLogger("funding_router")
PAPER_HISTORY_KEY = "paper.equity_history"
MAX_PAPER_HISTORY_POINTS = 720


def build_gateways(settings: Settings) -> dict[str, ExchangeGateway]:
    return {
        exchange.id: CCXTGateway(exchange, settings.service)
        for exchange in settings.enabled_exchanges
    }


def _print_scan(result: ScanResult, limit: int = 10) -> None:
    payload = result.to_dict()
    payload["candidates"] = payload["candidates"][:limit]
    payload["rejections"] = payload["rejections"][:limit]
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _market_payload(snapshot: MarketSnapshot) -> dict[str, object]:
    quote = snapshot.quote
    order_book = snapshot.order_book
    return {
        "exchange_id": quote.exchange_id,
        "symbol": quote.symbol,
        "asset": quote.asset,
        "mark_price": quote.mark_price,
        "index_price": quote.index_price,
        "best_bid": order_book.best_bid,
        "best_ask": order_book.best_ask,
        "book_spread_bps": (order_book.best_ask / order_book.best_bid - 1.0) * 10_000.0,
        "funding_rate": quote.funding_rate,
        "predicted_funding_rate": quote.predicted_funding_rate,
        "funding_bps_8h": quote.current_rate_per_hour * 8.0 * 10_000.0,
        "predicted_funding_bps_8h": quote.predicted_rate_per_hour * 8.0 * 10_000.0,
        "funding_interval_hours": quote.interval_hours,
        "next_funding_ms": quote.funding_timestamp_ms,
        "open_interest_usdt": quote.open_interest_usdt,
        "observed_at_ms": quote.observed_at_ms,
    }


def _equity_history(
    store: SQLiteStore,
    paper_summary: dict[str, object],
    timestamp_ms: int,
) -> list[dict[str, float | int]]:
    stored = store.get_state(PAPER_HISTORY_KEY) or {}
    raw_items = stored.get("items", [])
    items = list(raw_items) if isinstance(raw_items, list) else []
    items.append(
        {
            "timestamp_ms": timestamp_ms,
            "equity_usdt": float(paper_summary["equity_usdt"]),
            "realized_pnl_usdt": float(paper_summary["realized_pnl_usdt"]),
            "unrealized_pnl_usdt": float(paper_summary["unrealized_pnl_usdt"]),
        }
    )
    items = items[-MAX_PAPER_HISTORY_POINTS:]
    store.set_state(PAPER_HISTORY_KEY, {"items": items})
    return items


def _paper_snapshot(
    settings: Settings,
    scanner: FundingScanner,
    store: SQLiteStore,
    result: ScanResult,
    paper_summary: dict[str, object],
) -> dict[str, object]:
    markets = sorted(
        (_market_payload(snapshot) for snapshot in scanner.last_snapshots.values()),
        key=lambda item: (str(item["asset"]), str(item["exchange_id"])),
    )
    market_map = {
        (snapshot.exchange_id, snapshot.symbol): snapshot
        for snapshot in scanner.last_snapshots.values()
    }
    candles = []
    for (exchange_id, symbol), items in sorted(scanner.last_candles.items()):
        market = market_map.get((exchange_id, symbol))
        candles.append(
            {
                "exchange_id": exchange_id,
                "symbol": symbol,
                "asset": market.asset if market is not None else symbol.split("/")[0],
                "timeframe": settings.service.candle_timeframe,
                "items": items,
            }
        )
    history = _equity_history(store, paper_summary, result.observed_at_ms)
    return {
        "schema_version": 1,
        "mode": "paper",
        "updated_at_ms": result.observed_at_ms,
        "paper": {**paper_summary, "equity_history": history},
        "scan": result.to_dict(),
        "markets": markets,
        "candles": candles,
        "candle_errors": list(scanner.last_candle_errors),
        "events": store.events(limit=30),
        "risk": {
            "capital_usdt": settings.risk.capital_usdt,
            "notional_usdt": settings.risk.notional_usdt,
            "min_current_spread_bps_8h": settings.risk.min_current_spread_bps_8h,
            "min_predicted_spread_bps_8h": settings.risk.min_predicted_spread_bps_8h,
            "min_expected_net_bps": settings.risk.min_expected_net_bps,
            "max_hold_hours": settings.risk.max_hold_hours,
        },
    }


def _write_snapshot(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def _position_close_reason(
    position: PositionState,
    scanner: FundingScanner,
    settings: Settings,
) -> str | None:
    age_hours = (now_ms() - position.opened_at_ms) / 3_600_000.0
    if age_hours >= settings.risk.max_hold_hours:
        return "max_hold_hours"
    long = scanner.last_snapshots.get(
        (position.long_leg.exchange_id, position.long_leg.symbol)
    )
    short = scanner.last_snapshots.get(
        (position.short_leg.exchange_id, position.short_leg.symbol)
    )
    if long is None or short is None:
        return None
    current_bps_8h = (
        (short.quote.current_rate_per_hour - long.quote.current_rate_per_hour)
        * 8.0
        * 10_000.0
    )
    predicted_bps_8h = (
        (short.quote.predicted_rate_per_hour - long.quote.predicted_rate_per_hour)
        * 8.0
        * 10_000.0
    )
    if predicted_bps_8h <= 0:
        return "predicted_funding_reversal"
    if current_bps_8h <= settings.risk.exit_expected_net_bps:
        return "funding_spread_collapsed"
    return None


async def _recover_live_position(
    executor: LiveExecutor,
    store: SQLiteStore,
) -> PositionState | None:
    active = store.load_active_positions()
    if len(active) > 1:
        raise RuntimeError(
            "more than one persisted active position; manual intervention required"
        )
    if not active:
        return None
    position = active[0]
    if position.status == PositionStatus.OPEN:
        try:
            await executor.reconcile_position(position)
            store.append_event(
                "startup_reconciled", position.to_dict(), position.position_id
            )
            return position
        except Exception as exc:
            store.append_event(
                "startup_reconcile_failed",
                {"error": f"{type(exc).__name__}: {exc}"},
                position.position_id,
            )
    # OPENING/CLOSING/ERROR or a mismatched OPEN position: flatten before scanning.
    try:
        await executor.flatten_symbols(
            position.long_leg.exchange_id,
            position.long_leg.symbol,
            position.short_leg.exchange_id,
            position.short_leg.symbol,
            position.position_id,
        )
        position.status = PositionStatus.CLOSED
        position.updated_at_ms = now_ms()
        position.metadata["close_reason"] = "startup_recovery_flatten"
        store.save_position(position)
        store.append_event(
            "startup_flattened", position.to_dict(), position.position_id
        )
        return None
    except Exception as exc:
        position.status = PositionStatus.ERROR
        position.updated_at_ms = now_ms()
        position.error = f"{type(exc).__name__}: {exc}"
        store.save_position(position)
        raise


async def run_router(
    settings: Settings,
    mode: str,
    *,
    once: bool = False,
    cli_confirmed: bool = False,
    gateways: Mapping[str, ExchangeGateway] | None = None,
) -> int:
    if mode not in {"scan", "paper", "live"}:
        raise ValueError(f"unknown mode: {mode}")
    if mode == "live":
        authorize_live(settings, cli_confirmed)
        if once:
            raise ValueError(
                "live mode cannot run with --once; an open position needs supervision"
            )

    gateway_map = dict(gateways) if gateways is not None else build_gateways(settings)
    scanner = FundingScanner(settings, gateway_map)
    store = SQLiteStore(settings.service.database_path)
    paper = PaperTrader(settings, store) if mode == "paper" else None
    executor = LiveExecutor(settings, gateway_map, store) if mode == "live" else None
    live_position: PositionState | None = None
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    missing_active_cycles = 0
    try:
        await scanner.initialize()
        if executor is not None:
            live_position = await _recover_live_position(executor, store)
        while not stop.is_set():
            result = await scanner.scan_once()
            if mode == "scan":
                _print_scan(result)
            elif mode == "paper":
                assert paper is not None
                if paper.position is not None:
                    paper.accrue(scanner.last_snapshots, result.observed_at_ms)
                    should_close, reason = paper.should_close(
                        scanner.last_snapshots, result.observed_at_ms
                    )
                    if should_close:
                        pnl = paper.close(reason, result.observed_at_ms)
                        log_event(
                            LOGGER,
                            logging.INFO,
                            "paper_position_closed",
                            reason=reason,
                            pnl_usdt=pnl,
                        )
                if paper.position is None and result.candidates:
                    opened = paper.open(result.candidates[0], result.observed_at_ms)
                    log_event(
                        LOGGER,
                        logging.INFO,
                        "paper_position_opened",
                        position_id=opened.position_id,
                        candidate_id=result.candidates[0].candidate_id,
                    )
                paper_summary = paper.summary()
                snapshot = _paper_snapshot(
                    settings,
                    scanner,
                    store,
                    result,
                    paper_summary,
                )
                store.set_state("paper.latest", snapshot)
                _write_snapshot(settings.service.snapshot_path, snapshot)
                print(
                    json.dumps(
                        {"scan": result.to_dict(), "paper": paper_summary},
                        indent=2,
                        sort_keys=True,
                    )
                )
            else:
                assert executor is not None
                if live_position is not None:
                    long_key = (
                        live_position.long_leg.exchange_id,
                        live_position.long_leg.symbol,
                    )
                    short_key = (
                        live_position.short_leg.exchange_id,
                        live_position.short_leg.symbol,
                    )
                    if (
                        long_key not in scanner.last_snapshots
                        or short_key not in scanner.last_snapshots
                    ):
                        missing_active_cycles += 1
                    else:
                        missing_active_cycles = 0
                    await executor.reconcile_position(live_position)
                    reason = _position_close_reason(live_position, scanner, settings)
                    if missing_active_cycles >= 3:
                        reason = "active_market_data_unavailable"
                    if reason is not None:
                        live_position = await executor.close_position(
                            live_position, reason
                        )
                        live_position = None
                if live_position is None and result.candidates:
                    live_position = await executor.open_candidate(result.candidates[0])
                log_event(
                    LOGGER,
                    logging.INFO,
                    "live_cycle",
                    candidate_count=len(result.candidates),
                    errors=list(result.errors),
                    position_id=live_position.position_id if live_position else None,
                )

            if once:
                break
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=settings.service.poll_seconds
                )
            except TimeoutError:
                pass
        return 0
    finally:
        if (
            mode == "live"
            and executor is not None
            and live_position is not None
            and settings.live.close_on_shutdown
        ):
            try:
                await executor.close_position(live_position, "service_shutdown")
            except Exception:
                LOGGER.exception("failed to close live position during shutdown")
        await scanner.close()
        store.close()
