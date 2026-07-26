from __future__ import annotations

import asyncio
import json
import logging
import signal
from typing import Mapping

from .config import Settings
from .execution import LiveExecutor, authorize_live
from .gateways import CCXTGateway, ExchangeGateway
from .logging import log_event
from .models import PositionState, PositionStatus, ScanResult, now_ms
from .paper import PaperTrader
from .scanner import FundingScanner
from .store import SQLiteStore

LOGGER = logging.getLogger("funding_router")


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
        short.quote.current_rate_per_hour - long.quote.current_rate_per_hour
    ) * 8.0 * 10_000.0
    predicted_bps_8h = (
        short.quote.predicted_rate_per_hour - long.quote.predicted_rate_per_hour
    ) * 8.0 * 10_000.0
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
        raise RuntimeError("more than one persisted active position; manual intervention required")
    if not active:
        return None
    position = active[0]
    if position.status == PositionStatus.OPEN:
        try:
            await executor.reconcile_position(position)
            store.append_event("startup_reconciled", position.to_dict(), position.position_id)
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
        store.append_event("startup_flattened", position.to_dict(), position.position_id)
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
            raise ValueError("live mode cannot run with --once; an open position needs supervision")

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
                        log_event(LOGGER, logging.INFO, "paper_position_closed", reason=reason, pnl_usdt=pnl)
                if paper.position is None and result.candidates:
                    opened = paper.open(result.candidates[0], result.observed_at_ms)
                    log_event(
                        LOGGER,
                        logging.INFO,
                        "paper_position_opened",
                        position_id=opened.position_id,
                        candidate_id=result.candidates[0].candidate_id,
                    )
                print(json.dumps({"scan": result.to_dict(), "paper": paper.summary()}, indent=2, sort_keys=True))
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
                    if long_key not in scanner.last_snapshots or short_key not in scanner.last_snapshots:
                        missing_active_cycles += 1
                    else:
                        missing_active_cycles = 0
                    await executor.reconcile_position(live_position)
                    reason = _position_close_reason(live_position, scanner, settings)
                    if missing_active_cycles >= 3:
                        reason = "active_market_data_unavailable"
                    if reason is not None:
                        live_position = await executor.close_position(live_position, reason)
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
                await asyncio.wait_for(stop.wait(), timeout=settings.service.poll_seconds)
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
