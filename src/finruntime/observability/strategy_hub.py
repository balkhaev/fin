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


def _metric(label: str, value: str) -> dict[str, str]:
    return {"label": label, "value": value}


def _number_text(value: object, digits: int = 2) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—"
    return f"{float(value):.{digits}f}"


def _best_funding_spread(scan: dict[str, Any]) -> float | None:
    values: list[float] = []
    for candidate in scan.get("candidates") or []:
        if isinstance(candidate, dict):
            value = candidate.get("current_spread_bps_8h")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(float(value))
    for rejection in scan.get("rejections") or []:
        if not isinstance(rejection, dict):
            continue
        details = rejection.get("details")
        if not isinstance(details, dict):
            continue
        value = details.get("current_spread_bps_8h")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
    return max(values) if values else None


def _failed_summary(
    context: dict[str, Any], labels: dict[str, str], *, fallback: str
) -> str:
    failed = context.get("failed")
    if not isinstance(failed, list) or not failed:
        return fallback
    descriptions = [labels.get(str(item), str(item)) for item in failed]
    visible = descriptions[:2]
    suffix = f" и ещё {len(descriptions) - 2}" if len(descriptions) > 2 else ""
    return f"{', '.join(visible)}{suffix}"


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
            "risk_state": {
                "mode": "base",
                "initial_equity_usdt": 10_000.0,
                "equity_usdt": 10_000.0,
                "high_water_equity_usdt": 10_000.0,
                "last_derisk_high_water_equity_usdt": 10_000.0,
            },
            "candles": [],
            "signals": [],
            "signal_context": {"wif": None, "dot": None},
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
    scan = paper_snapshot.get("scan")
    scan = scan if isinstance(scan, dict) else {}
    risk = paper_snapshot.get("risk")
    risk = risk if isinstance(risk, dict) else {}
    candidates = scan.get("candidates")
    candidates = candidates if isinstance(candidates, list) else []
    rejections = scan.get("rejections")
    rejections = rejections if isinstance(rejections, list) else []
    best_spread = _best_funding_spread(scan)
    current_threshold = _number(risk.get("min_current_spread_bps_8h"), 8.0)
    predicted_threshold = _number(risk.get("min_predicted_spread_bps_8h"), 5.0)
    net_threshold = _number(risk.get("min_expected_net_bps"), 10.0)
    starting = _number(account.get("starting_balance_usdt"))
    equity = _number(account.get("equity_usdt"), starting)
    pnl = equity - starting
    open_count = 1 if isinstance(position, dict) else 0
    if open_count:
        why_now = (
            "Paper-пара уже открыта: одна биржа куплена, другая продана, "
            "поэтому движение цены в основном взаимно компенсируется."
        )
        waiting_for = (
            "Ждём, пока чистая доходность по funding исчезнет, либо сработает "
            "лимит удержания; затем обе paper-ноги закроются вместе."
        )
    elif candidates:
        why_now = (
            f"Найдено подходящих пар: {len(candidates)}. Paper-вход должен "
            "появиться в ближайшем цикле сканера."
        )
        waiting_for = "Ждём одновременного открытия лонга и шорта в paper-паре."
    else:
        spread_text = (
            f"{best_spread:.2f} bps за 8ч"
            if best_spread is not None
            else "не рассчитан"
        )
        why_now = (
            f"Сделки нет: лучший текущий спред — {spread_text}, "
            f"а для входа нужно минимум {current_threshold:.2f} bps."
        )
        waiting_for = (
            f"Текущий спред ≥ {current_threshold:.2f} bps, прогноз ≥ "
            f"{predicted_threshold:.2f} bps и ожидаемый доход после расходов ≥ "
            f"{net_threshold:.2f} bps при достаточной ликвидности."
        )
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
        "signals": candidates,
        "detail": {
            "markets": len(paper_snapshot.get("markets") or []),
            "entry_threshold_bps": risk.get("min_current_spread_bps_8h"),
            "rejections": len(rejections),
        },
        "context": {
            "how_it_works": (
                "Покупает бессрочный фьючерс там, где funding ниже, и одновременно "
                "шортит тот же актив там, где funding выше."
            ),
            "why_now": why_now,
            "waiting_for": waiting_for,
            "metrics": [
                _metric(
                    "Лучший спред",
                    f"{best_spread:.2f} bps / 8ч" if best_spread is not None else "—",
                ),
                _metric("Порог входа", f"{current_threshold:.2f} bps / 8ч"),
                _metric("Кандидаты", str(len(candidates))),
            ],
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
    committed_cycles = int(_number(source.get("committed_cycles")))
    scheduler_state = runtime.get("scheduler", {}).get("state")
    source_health = str(source.get("health", "starting"))
    status = "waiting" if observations == 0 else source_health
    if status == "healthy":
        status = "running"
    return {
        "id": "atlas-nx",
        "repository": "fin",
        "name": "Atlas NX",
        "description": "Портфельный FIN runtime с проверяемым paper-ledger.",
        "mode": "paper",
        "status": status,
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
            "committed_cycles": committed_cycles,
            "scheduler": scheduler_state,
        },
        "context": {
            "how_it_works": (
                "Получает дневные веса портфеля V75 ATLAS-NX и проводит их "
                "через отдельный paper-счёт с комиссиями, funding и сверкой."
            ),
            "why_now": (
                "Циклов ещё не было: ledger не получил ни одного валидного "
                "снимка стратегии."
                if observations == 0
                else f"Обработано наблюдений: {observations}; завершено циклов: {committed_cycles}."
            ),
            "waiting_for": (
                "Ждём первый закрытый дневной снимок V75, затем план, "
                "paper-исполнение и зафиксированный результат цикла."
                if observations == 0
                else "Ждём следующие закрытые дневные веса и плановый цикл scheduler."
            ),
            "metrics": [
                _metric("Наблюдения", str(observations)),
                _metric("Циклы", str(committed_cycles)),
                _metric("Scheduler", str(scheduler_state or "—")),
            ],
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
    risk_state = snapshot.get("risk_state")
    risk_state = risk_state if isinstance(risk_state, dict) else {}
    signal_context = snapshot.get("signal_context")
    signal_context = signal_context if isinstance(signal_context, dict) else {}
    wif_context = signal_context.get("wif")
    wif_context = wif_context if isinstance(wif_context, dict) else {}
    dot_context = signal_context.get("dot")
    dot_context = dot_context if isinstance(dot_context, dict) else {}
    wif_reason = _failed_summary(
        wif_context,
        {
            "weekday": "сегодня не сигнальный день UTC",
            "move_45m_atr": "падение слабее 2 ATR",
            "volume_z": "нет всплеска объёма",
            "lower_wick": "нижняя тень короче 50%",
            "close_location": "закрытие недостаточно высоко",
            "taker_imbalance": "продавцы слишком агрессивны",
            "oi_z": "открытый интерес ещё не сброшен",
            "strength": "общая сила ниже 3.5",
        },
        fallback="полный WIF-сигнал не сформирован",
    )
    dot_reason = _failed_summary(
        dot_context,
        {
            "weekday": "сегодня DOT-модуль выключен",
            "window": "сейчас не окно 15–30 минут после funding",
            "funding": "funding недостаточно отрицательный",
        },
        fallback="полный DOT-сигнал не сформирован",
    )
    if positions:
        assets = ", ".join(
            str(item.get("asset") or item.get("symbol")) for item in positions
        )
        why_now = f"Открыты paper-позиции: {assets}. Риск-контроль продолжает следить за стопом и временем удержания."
    elif signals:
        assets = ", ".join(
            str(item.get("asset") or item.get("symbol")) for item in signals
        )
        why_now = f"Сигнал подтверждён для {assets}; paper-счёт обрабатывает вход."
    else:
        why_now = f"Сделки нет. WIF: {wif_reason}. DOT: {dot_reason}."
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
            "risk_mode": risk_state.get("mode", "base"),
            "high_water_equity_usdt": risk_state.get("high_water_equity_usdt"),
            "max_gross": MAX_GROSS_LEVERAGE,
            "errors": snapshot.get("errors") or [],
            **(snapshot.get("diagnostics") or {}),
        },
        "context": {
            "how_it_works": (
                "Покупает WIF после резкого сброса цены и открытого интереса либо "
                "DOT после аномально отрицательного funding."
            ),
            "why_now": why_now,
            "waiting_for": (
                "WIF: падение ≥ 2 ATR со всплеском объёма, сбросом OI и сильным "
                "отскоком свечи. DOT: funding ≤ −2.25/−2.50 bps в окне 15–30 минут."
            ),
            "metrics": [
                _metric(
                    "WIF 45м", f"{_number_text(wif_context.get('move_45m_atr'))} ATR"
                ),
                _metric("WIF OI z", _number_text(wif_context.get("oi_z"))),
                _metric(
                    "DOT funding",
                    f"{_number_text(dot_context.get('funding_rate_bps'))} bps",
                ),
                _metric("Риск-режим", str(risk_state.get("mode", "base"))),
            ],
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
    starting = _number(account.get("initialNavUsd"), 10_000.0)
    equity = _number(paper.get("navUsd"), starting)
    pnl = equity - starting
    positions = source.get("positions")
    positions = positions if isinstance(positions, list) else []
    eligible_assets = source.get("eligibleAssets")
    eligible_assets = eligible_assets if isinstance(eligible_assets, list) else []
    btc_filters = source.get("btcFilters")
    btc_filters = btc_filters if isinstance(btc_filters, list) else []
    btc_score = int(_number(source.get("btcConsensusScore")))
    target_gross = _number(source.get("targetGross"))
    cash_weight = _number(source.get("cashWeight"), 1.0)
    status = "degraded" if error or stale else str(source.get("status", "starting"))
    if status == "ready":
        status = "running"
    if positions:
        why_now = (
            f"В портфеле {len(positions)} paper-позиций; целевая gross-экспозиция "
            f"{target_gross:.2f}×."
        )
        waiting_for = (
            "Ждём следующей недельной ребалансировки либо отключения BTC-фильтра."
        )
    elif btc_score == 0:
        why_now = (
            "Стратегия в кэше: ни один из трёх трендовых BTC-фильтров сейчас "
            "не разрешает рыночный риск."
        )
        waiting_for = (
            "Ждём хотя бы один положительный BTC-фильтр: BTC выше EMA100 с "
            "EMA20 выше EMA100, BTC выше SMA150 или EMA50 выше EMA200."
        )
    elif not eligible_assets:
        why_now = "Стратегия в кэше: после фильтров ликвидности не осталось доступных активов."
        waiting_for = "Ждём активы с историей ≥ 180 дней и достаточным объёмом."
    else:
        why_now = (
            "BTC-фильтр разрешает риск, но итоговый вес FLOW/momentum пока равен нулю."
        )
        waiting_for = "Ждём положительный FLOW или absolute momentum на ребалансировке."
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
            "target_gross": target_gross,
            "cash_weight": cash_weight,
            "btc_consensus_score": btc_score,
            "btc_filters": btc_filters,
            "eligible_assets": eligible_assets,
            "upstream_error": error,
            "upstream_stale": stale,
        },
        "context": {
            "how_it_works": (
                "Ранжирует ликвидные монеты по FLOW и положительному momentum, "
                "а трендовые BTC-фильтры решают, держать портфель или уйти в кэш."
            ),
            "why_now": why_now,
            "waiting_for": waiting_for,
            "metrics": [
                _metric("BTC режим", f"{btc_score}/3"),
                _metric("Доступно активов", str(len(eligible_assets))),
                _metric("Target gross", f"{target_gross:.2f}×"),
                _metric("Cash", f"{cash_weight * 100:.0f}%"),
            ],
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
                    item["status"] == "running" for item in strategies
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
