"""Normalize FIN, trader and fin2 paper runtimes for one simple UI."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

DEFAULT_FIN2_FORWARD_URL = "https://fin2.balkhaev.com/api/strategy/forward"
UPSTREAM_CACHE_SECONDS = 10.0
UPSTREAM_TIMEOUT_SECONDS = 4.0


def _number(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _percent(pnl: float, starting_balance: float) -> float:
    return pnl / starting_balance * 100 if starting_balance > 0 else 0.0


def _fetch_json(url: str, timeout: float) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "FIN-Strategy-Hub/1.0"})
    with urlopen(request, timeout=timeout) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise TypeError(f"upstream did not return a JSON object: {url}")
    return value


class UpstreamSnapshotCache:
    def __init__(
        self,
        fetcher: Callable[[str, float], dict[str, Any]] = _fetch_json,
        *,
        ttl_seconds: float = UPSTREAM_CACHE_SECONDS,
        timeout_seconds: float = UPSTREAM_TIMEOUT_SECONDS,
    ) -> None:
        self.fetcher = fetcher
        self.ttl_seconds = ttl_seconds
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._items: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, url: str) -> tuple[dict[str, Any] | None, str | None, bool]:
        with self._lock:
            cached = self._items.get(url)
            if cached and time.monotonic() - cached[0] < self.ttl_seconds:
                return dict(cached[1]), None, False
            try:
                value = self.fetcher(url, self.timeout_seconds)
            except (OSError, TypeError, ValueError) as error:
                if cached:
                    return dict(cached[1]), f"{type(error).__name__}: {error}", True
                return None, f"{type(error).__name__}: {error}", True
            self._items[url] = (time.monotonic(), dict(value))
            return value, None, False


def read_consensus_snapshot(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "schema_version": 1,
            "strategy_id": "consensus-wif-dot-v1",
            "mode": "paper",
            "health": "starting",
            "paper": {
                "starting_balance_usdt": 10_000.0,
                "equity_usdt": 10_000.0,
                "realized_pnl_usdt": 0.0,
                "unrealized_pnl_usdt": 0.0,
                "closed_positions": 0,
                "positions": [],
                "equity_history": [],
            },
            "candles": [],
            "signals": [],
            "errors": ["WIF/DOT paper worker is starting"],
        }
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("unsupported WIF/DOT paper snapshot")
    return value


def _funding_strategy(paper_snapshot: dict[str, Any]) -> dict[str, Any]:
    account = paper_snapshot.get("paper")
    account = account if isinstance(account, dict) else {}
    position = account.get("open_position")
    starting = _number(account.get("starting_balance_usdt"))
    equity = _number(account.get("equity_usdt"), starting)
    pnl = equity - starting
    open_count = 1 if isinstance(position, dict) else 0
    return {
        "id": "funding-neutral",
        "repository": "fin",
        "name": "Funding Neutral",
        "description": "Нейтральный funding-спред между Binance и Bybit.",
        "mode": "paper",
        "status": "running"
        if paper_snapshot.get("health") == "healthy"
        else "degraded",
        "status_label": "Позиция открыта" if open_count else "Сканирует спреды",
        "equity_usdt": equity,
        "starting_balance_usdt": starting,
        "pnl_usdt": pnl,
        "return_percent": _percent(pnl, starting),
        "open_positions": open_count,
        "closed_positions": int(_number(account.get("closed_positions"))),
        "market": "Binance ↔ Bybit perpetuals",
        "timeframe": "5 sec · 1m candles",
        "updated_at_ms": paper_snapshot.get("updated_at_ms"),
        "positions": [position] if isinstance(position, dict) else [],
        "signals": paper_snapshot.get("scan", {}).get("candidates", []),
        "detail": {
            "markets": len(paper_snapshot.get("markets") or []),
            "entry_threshold_bps": paper_snapshot.get("risk", {}).get(
                "min_current_spread_bps_8h"
            ),
            "rejections": len(paper_snapshot.get("scan", {}).get("rejections", [])),
        },
    }


def _atlas_strategy(runtime: dict[str, Any]) -> dict[str, Any]:
    strategies = runtime.get("strategies")
    rows = strategies if isinstance(strategies, list) else []
    source = next(
        (item for item in rows if item.get("strategy_id") == "v75_atlas_nx"), {}
    )
    account = source.get("account") if isinstance(source, dict) else {}
    account = account if isinstance(account, dict) else {}
    starting = 10_000.0
    equity = _number(account.get("equity"), starting)
    pnl = equity - starting
    observations = int(_number(source.get("observation_count")))
    return {
        "id": "atlas-nx",
        "repository": "fin",
        "name": "Atlas NX",
        "description": "Портфельный FIN runtime с проверяемым paper-ledger.",
        "mode": "paper",
        "status": source.get("health", "starting"),
        "status_label": "Наблюдает" if observations else "Ждёт первый цикл",
        "equity_usdt": equity,
        "starting_balance_usdt": starting,
        "pnl_usdt": pnl,
        "return_percent": _percent(pnl, starting),
        "open_positions": int(_number(account.get("spot_position_count")))
        + int(_number(account.get("perp_position_count"))),
        "closed_positions": int(_number(source.get("committed_cycles"))),
        "market": "Multi-asset portfolio",
        "timeframe": "scheduler",
        "updated_at_ms": None,
        "positions": [],
        "signals": [],
        "detail": {
            "observations": observations,
            "committed_cycles": int(_number(source.get("committed_cycles"))),
            "scheduler": runtime.get("scheduler", {}).get("state"),
        },
    }


def _consensus_strategy(snapshot: dict[str, Any]) -> dict[str, Any]:
    account = snapshot.get("paper")
    account = account if isinstance(account, dict) else {}
    starting = _number(account.get("starting_balance_usdt"), 10_000.0)
    equity = _number(account.get("equity_usdt"), starting)
    pnl = equity - starting
    positions = account.get("positions")
    positions = positions if isinstance(positions, list) else []
    signals = snapshot.get("signals")
    signals = signals if isinstance(signals, list) else []
    return {
        "id": "consensus-wif-dot",
        "repository": "trader",
        "name": "Consensus WIF + DOT",
        "description": "WIF OI-flush и DOT negative-funding rebound.",
        "mode": "paper",
        "status": "running"
        if snapshot.get("health") == "healthy"
        else snapshot.get("health", "starting"),
        "status_label": "Позиция открыта" if positions else "Ищет подтверждение",
        "equity_usdt": equity,
        "starting_balance_usdt": starting,
        "pnl_usdt": pnl,
        "return_percent": _percent(pnl, starting),
        "open_positions": len(positions),
        "closed_positions": int(_number(account.get("closed_positions"))),
        "market": "Binance USD-M · WIF / DOT",
        "timeframe": "60 sec · 15m signals",
        "updated_at_ms": snapshot.get("market_data_at_ms"),
        "positions": positions,
        "signals": signals,
        "candles": snapshot.get("candles") or [],
        "detail": {
            "risk_mode": "base",
            "max_gross": MAX_GROSS_LEVERAGE,
            "errors": snapshot.get("errors") or [],
            **(snapshot.get("diagnostics") or {}),
        },
    }


def _dyn_strategy(
    snapshot: dict[str, Any] | None, error: str | None, stale: bool
) -> dict[str, Any]:
    source = snapshot or {}
    paper = source.get("paper") if isinstance(source, dict) else {}
    paper = paper if isinstance(paper, dict) else {}
    account = paper.get("account")
    account = account if isinstance(account, dict) else {}
    starting = _number(account.get("initialNavUsd"), 100_000.0)
    equity = _number(paper.get("navUsd"), starting)
    pnl = equity - starting
    positions = source.get("positions")
    positions = positions if isinstance(positions, list) else []
    status = "degraded" if error or stale else str(source.get("status", "starting"))
    if status == "ready":
        status = "running"
    return {
        "id": "dyn-iv113",
        "repository": "fin2",
        "name": "DYN-IV113",
        "description": "Dynamic FLOW + Absolute Momentum на закрытых свечах Binance.",
        "mode": "paper",
        "status": status,
        "status_label": "В рынке" if positions else "Режим CASH",
        "equity_usdt": equity,
        "starting_balance_usdt": starting,
        "pnl_usdt": pnl,
        "return_percent": _percent(pnl, starting),
        "open_positions": len(positions),
        "closed_positions": int(_number(paper.get("totalExecutions"))),
        "market": "Binance spot · 17 assets",
        "timeframe": "daily close · live marks",
        "updated_at_ms": None,
        "updated_at": source.get("marketDataAt") or source.get("generatedAt"),
        "positions": positions,
        "signals": [],
        "detail": {
            "target_gross": source.get("targetGross"),
            "cash_weight": source.get("cashWeight"),
            "btc_consensus_score": source.get("btcConsensusScore"),
            "eligible_assets": source.get("eligibleAssets") or [],
            "upstream_error": error,
            "upstream_stale": stale,
        },
    }


MAX_GROSS_LEVERAGE = 3.0


class StrategyHub:
    def __init__(
        self,
        *,
        fin2_url: str | None = None,
        cache: UpstreamSnapshotCache | None = None,
    ) -> None:
        self.fin2_url = fin2_url or os.environ.get(
            "FIN2_FORWARD_URL", DEFAULT_FIN2_FORWARD_URL
        )
        self.cache = cache or UpstreamSnapshotCache()

    def snapshot(
        self,
        *,
        funding: dict[str, Any],
        runtime: dict[str, Any],
        consensus: dict[str, Any],
    ) -> dict[str, Any]:
        dyn, dyn_error, dyn_stale = self.cache.get(self.fin2_url)
        strategies = [
            _funding_strategy(funding),
            _consensus_strategy(consensus),
            _dyn_strategy(dyn, dyn_error, dyn_stale),
            _atlas_strategy(runtime),
        ]
        paper_strategies = [item for item in strategies if item["mode"] == "paper"]
        equity = sum(_number(item.get("equity_usdt")) for item in paper_strategies)
        starting = sum(
            _number(item.get("starting_balance_usdt")) for item in paper_strategies
        )
        return {
            "schema_version": 1,
            "mode": "paper",
            "generated_at_ms": int(time.time() * 1000),
            "exchange_submission_available": False,
            "summary": {
                "strategy_count": len(strategies),
                "running_count": sum(
                    item["status"] in {"running", "healthy"} for item in strategies
                ),
                "paper_equity_usdt": equity,
                "paper_starting_balance_usdt": starting,
                "paper_pnl_usdt": equity - starting,
                "open_positions": sum(
                    int(item["open_positions"]) for item in paper_strategies
                ),
            },
            "strategies": strategies,
        }
