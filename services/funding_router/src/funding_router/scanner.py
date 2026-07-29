from __future__ import annotations

import asyncio
import itertools
from collections import defaultdict
from collections.abc import Mapping

from .analytics import evaluate_pair
from .config import Settings
from .gateways import ExchangeGateway
from .models import MarketSnapshot, Rejection, ScanResult, now_ms


class FundingScanner:
    def __init__(self, settings: Settings, gateways: Mapping[str, ExchangeGateway]):
        self.settings = settings
        self.gateways = dict(gateways)
        self.last_snapshots: dict[tuple[str, str], MarketSnapshot] = {}
        self.last_candles: dict[tuple[str, str], list[dict[str, float | int]]] = {}
        self.last_candle_errors: list[str] = []
        missing = {item.id for item in settings.enabled_exchanges} - set(gateways)
        if missing:
            raise ValueError(f"missing gateways: {sorted(missing)}")

    async def initialize(self) -> None:
        await asyncio.gather(
            *(gateway.initialize() for gateway in self.gateways.values())
        )

    async def close(self) -> None:
        await asyncio.gather(
            *(gateway.close() for gateway in self.gateways.values()),
            return_exceptions=True,
        )

    async def scan_once(self) -> ScanResult:
        jobs: list[tuple[str, str, asyncio.Task[MarketSnapshot]]] = []
        candle_jobs: list[
            tuple[str, str, asyncio.Task[list[dict[str, float | int]]]]
        ] = []
        for exchange in self.settings.enabled_exchanges:
            gateway = self.gateways[exchange.id]
            for symbol in exchange.markets:
                jobs.append(
                    (
                        exchange.id,
                        symbol,
                        asyncio.create_task(gateway.fetch_snapshot(symbol)),
                    )
                )
                fetch_candles = getattr(gateway, "fetch_candles", None)
                if callable(fetch_candles):
                    candle_jobs.append(
                        (
                            exchange.id,
                            symbol,
                            asyncio.create_task(
                                fetch_candles(
                                    symbol,
                                    self.settings.service.candle_timeframe,
                                    self.settings.service.candle_limit,
                                )
                            ),
                        )
                    )

        snapshots: list[MarketSnapshot] = []
        errors: list[str] = []
        for exchange_id, symbol, task in jobs:
            try:
                snapshots.append(await task)
            except Exception as exc:
                errors.append(f"{exchange_id} {symbol}: {type(exc).__name__}: {exc}")

        self.last_snapshots = {
            (snapshot.exchange_id, snapshot.symbol): snapshot for snapshot in snapshots
        }

        candles: dict[tuple[str, str], list[dict[str, float | int]]] = {}
        candle_errors: list[str] = []
        for exchange_id, symbol, task in candle_jobs:
            try:
                rows = await task
                if rows:
                    candles[(exchange_id, symbol)] = rows
            except Exception as exc:
                candle_errors.append(
                    f"{exchange_id} {symbol}: {type(exc).__name__}: {exc}"
                )
        self.last_candles = candles
        self.last_candle_errors = candle_errors

        grouped: dict[str, list[MarketSnapshot]] = defaultdict(list)
        for snapshot in snapshots:
            grouped[snapshot.asset].append(snapshot)

        exchange_map = self.settings.exchange_map()
        candidates = []
        rejections: list[Rejection] = []
        for asset, rows in grouped.items():
            for first, second in itertools.combinations(rows, 2):
                if first.exchange_id == second.exchange_id:
                    continue
                if (
                    first.quote.current_rate_per_hour
                    <= second.quote.current_rate_per_hour
                ):
                    long, short = first, second
                else:
                    long, short = second, first
                evaluation = evaluate_pair(
                    long, short, self.settings.risk, exchange_map
                )
                if evaluation.candidate is not None:
                    candidates.append(evaluation.candidate)
                elif evaluation.rejection is not None:
                    rejections.append(evaluation.rejection)

        candidates.sort(key=lambda item: item.expected_net_bps, reverse=True)
        return ScanResult(
            observed_at_ms=now_ms(),
            candidates=tuple(candidates),
            rejections=tuple(rejections),
            errors=tuple(errors),
        )
