"""On-demand, paper-only replays for strategies with reproducible inputs."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime, timedelta
from datetime import time as datetime_time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from finruntime.strategies import atlas_nx_r1_paper, dyn_paper

INITIAL_NAV_USD = 10_000.0
REQUESTED_YEARS = 2
WARMUP_DAYS = 420
CAGR_THRESHOLD_PERCENT = 50.0
BINANCE_SPOT_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
BINANCE_PAGE_LIMIT = 1000
BINANCE_MAX_WORKERS = 8
MILLISECONDS_PER_DAY = 86_400_000
TROPICAL_YEAR_DAYS = 365.2425

HistoryLoader = Callable[
    [tuple[str, ...], date, date],
    tuple[list[dict[str, Any]], list[dict[str, str]], int],
]


def _utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _two_years_before(value: date) -> date:
    try:
        return value.replace(year=value.year - REQUESTED_YEARS)
    except ValueError:
        return value.replace(year=value.year - REQUESTED_YEARS, day=28)


def _day_start_milliseconds(value: date) -> int:
    observed = datetime.combine(value, datetime_time.min, UTC)
    return int(observed.timestamp() * 1000)


def _fetch_json(url: str, *, timeout_seconds: float = 20.0) -> Any:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "finruntime-paper-backtest/1.0",
        },
    )
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return json.load(response)
        except (OSError, ValueError) as error:
            last_error = error
            if attempt == 0:
                time.sleep(0.2)
    raise RuntimeError(f"Binance market-data request failed: {last_error}")


def _fetch_symbol_history(
    symbol: str, start: date, end: date
) -> tuple[dict[str, Any], int]:
    cursor = _day_start_milliseconds(start)
    end_milliseconds = _day_start_milliseconds(end + timedelta(days=1)) - 1
    rows_by_open: dict[int, list[Any]] = {}
    request_count = 0
    while cursor <= end_milliseconds:
        query = urlencode(
            {
                "symbol": symbol,
                "interval": "1d",
                "startTime": cursor,
                "endTime": end_milliseconds,
                "limit": BINANCE_PAGE_LIMIT,
            }
        )
        payload = _fetch_json(f"{BINANCE_SPOT_KLINES_URL}?{query}")
        request_count += 1
        if not isinstance(payload, list):
            raise TypeError(f"Binance returned an invalid kline payload for {symbol}")
        if not payload:
            break
        for row in payload:
            if not isinstance(row, list) or len(row) < 8:
                raise ValueError(f"Binance returned an invalid kline row for {symbol}")
            rows_by_open[int(row[0])] = row
        next_cursor = int(payload[-1][0]) + MILLISECONDS_PER_DAY
        if next_cursor <= cursor:
            raise ValueError(f"Binance pagination did not advance for {symbol}")
        cursor = next_cursor
        if len(payload) < BINANCE_PAGE_LIMIT:
            break

    bars: dict[str, dict[str, Any]] = {}
    for open_milliseconds, row in sorted(rows_by_open.items()):
        observed_date = datetime.fromtimestamp(open_milliseconds / 1000, UTC).date()
        if observed_date < start or observed_date > end:
            continue
        candle = {
            "openTime": open_milliseconds,
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "closeTime": int(row[6]),
            "quoteVolume": float(row[7]),
            "closed": True,
        }
        if not all(
            math.isfinite(candle[field])
            for field in ("open", "high", "low", "close", "quoteVolume")
        ):
            raise ValueError(f"Binance returned non-finite candles for {symbol}")
        bars[observed_date.isoformat()] = candle
    if not bars:
        raise ValueError(f"No closed daily candles were returned for {symbol}")
    return (
        {
            "asset": symbol.removesuffix("USDT"),
            "symbol": symbol,
            "bars": bars,
            "liveCandle": list(bars.values())[-1],
        },
        request_count,
    )


def load_binance_daily_histories(
    symbols: tuple[str, ...], start: date, end: date
) -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
    """Download a bounded range of closed Binance Spot daily candles."""

    histories: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    request_count = 0
    request_count_lock = threading.Lock()
    with ThreadPoolExecutor(
        max_workers=min(BINANCE_MAX_WORKERS, len(symbols))
    ) as executor:
        futures = {
            executor.submit(_fetch_symbol_history, symbol, start, end): symbol
            for symbol in symbols
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                history, symbol_request_count = future.result()
                histories.append(history)
                with request_count_lock:
                    request_count += symbol_request_count
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                failures.append({"symbol": symbol, "reason": str(error)})
    symbol_order = {symbol: index for index, symbol in enumerate(symbols)}
    histories.sort(key=lambda item: symbol_order[item["symbol"]])
    failures.sort(key=lambda item: symbol_order[item["symbol"]])
    return histories, failures, request_count


def _input_sha256(histories: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for history in sorted(histories, key=lambda item: str(item["symbol"])):
        digest.update(str(history["symbol"]).encode("utf-8"))
        for date_text, candle in sorted(history["bars"].items()):
            row = [
                date_text,
                candle["open"],
                candle["high"],
                candle["low"],
                candle["close"],
                candle["quoteVolume"],
            ]
            digest.update(
                json.dumps(row, separators=(",", ":"), allow_nan=False).encode("utf-8")
            )
    return digest.hexdigest()


def _metrics(daily: list[dict[str, Any]], *, start: date, end: date) -> dict[str, Any]:
    if not daily:
        raise ValueError("Backtest engine returned no daily account observations")
    returns = [float(item["return"]) for item in daily]
    ending_nav = float(daily[-1]["navUsd"])
    years = max((end - start).days / TROPICAL_YEAR_DAYS, 1 / TROPICAL_YEAR_DAYS)
    multiple = ending_nav / INITIAL_NAV_USD
    standard_deviation = statistics.stdev(returns) if len(returns) > 1 else 0.0
    sharpe = (
        statistics.fmean(returns) / standard_deviation * math.sqrt(365)
        if standard_deviation > 0
        else None
    )
    downside = [min(value, 0.0) for value in returns]
    downside_deviation = math.sqrt(
        statistics.fmean(value * value for value in downside)
    )
    sortino = (
        statistics.fmean(returns) / downside_deviation * math.sqrt(365)
        if downside_deviation > 0
        else None
    )
    high_water = INITIAL_NAV_USD
    maximum_drawdown = 0.0
    for item in daily:
        nav = float(item["navUsd"])
        high_water = max(high_water, nav)
        maximum_drawdown = min(maximum_drawdown, nav / high_water - 1)
    return {
        "scope": "on_demand_two_year_replay",
        "scope_label": "Свежий replay текущего движка за 2 года",
        "cagr_percent": (multiple ** (1 / years) - 1) * 100,
        "total_return_percent": (multiple - 1) * 100,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown_percent": maximum_drawdown * 100,
        "years": years,
        "starting_nav_usd": INITIAL_NAV_USD,
        "ending_nav_usd": ending_nav,
        "daily_observations": len(daily),
    }


def _execution_date(execution: dict[str, Any]) -> str:
    return str(execution.get("orderDate") or execution["date"])


def _engine_return(engine: dict[str, Any], date_index: int, asset_index: int) -> float:
    value = float(engine["returns"][date_index][asset_index])
    return max(-dyn_paper.RETURN_CAP, min(dyn_paper.RETURN_CAP, value))


def _new_episode(
    *, asset: str, direction: str, entry_date: str, entry_price: float
) -> dict[str, Any]:
    return {
        "asset": asset,
        "direction": direction,
        "entry_date": entry_date,
        "entry_price": entry_price,
        "net_pnl_usd": 0.0,
        "order_count": 0,
    }


def _close_episode(
    episode: dict[str, Any], *, exit_date: str | None, exit_price: float, end: date
) -> dict[str, Any]:
    held_through = exit_date or end.isoformat()
    direction_multiplier = 1 if episode["direction"] == "LONG" else -1
    asset_return = (
        (exit_price / float(episode["entry_price"]) - 1) * direction_multiplier * 100
    )
    return {
        "id": (
            f"run-{episode['entry_date'].replace('-', '')}-{episode['asset']}-"
            f"{episode['direction'].lower()}"
        ),
        "asset": episode["asset"],
        "direction": episode["direction"],
        "status": "closed" if exit_date else "open",
        "entry_date": episode["entry_date"],
        "exit_date": exit_date,
        "held_through": held_through,
        "holding_days": max(
            0,
            (
                date.fromisoformat(held_through)
                - date.fromisoformat(episode["entry_date"])
            ).days,
        ),
        "entry_price": float(episode["entry_price"]),
        "exit_price": exit_price,
        "asset_return_percent": asset_return,
        "net_pnl_usd": float(episode["net_pnl_usd"]),
        "order_count": int(episode["order_count"]),
    }


def _trade_episodes(
    *,
    engine: dict[str, Any],
    continuation: dict[str, Any],
    start: date,
    end: date,
    execution_cost: float,
) -> list[dict[str, Any]]:
    executions_by_date: dict[str, list[dict[str, Any]]] = {}
    for execution in continuation["executions"]:
        executions_by_date.setdefault(_execution_date(execution), []).append(execution)
    date_indexes = {value: index for index, value in enumerate(engine["dates"])}
    asset_indexes = {asset: index for index, asset in enumerate(engine["assets"])}
    weights = {asset: 0.0 for asset in engine["assets"]}
    active: dict[str, dict[str, Any]] = {}
    completed: list[dict[str, Any]] = []
    previous_nav = INITIAL_NAV_USD

    for daily in continuation["daily"]:
        current_date = str(daily["date"])
        date_index = date_indexes[current_date]
        gross_by_asset = {
            asset: previous_nav
            * weights[asset]
            * _engine_return(engine, date_index, asset_index)
            for asset, asset_index in asset_indexes.items()
        }
        gross_total = sum(gross_by_asset.values()) / previous_nav
        trade_cost_rate = float(daily.get("tradeCost", 0.0))
        financing_rate = max(
            0.0, gross_total - trade_cost_rate - float(daily["return"])
        )
        gross_exposure = sum(abs(value) for value in weights.values())
        for asset, episode in active.items():
            finance_cost = (
                previous_nav * financing_rate * abs(weights[asset]) / gross_exposure
                if gross_exposure > 0
                else 0.0
            )
            episode["net_pnl_usd"] += gross_by_asset[asset] - finance_cost

        for execution in executions_by_date.get(current_date, []):
            asset = str(execution["asset"])
            old_weight = float(execution["oldWeight"])
            new_weight = float(execution["newWeight"])
            delta_weight = float(execution["deltaWeight"])
            price = float(execution["price"])
            old_direction = 0 if old_weight == 0 else math.copysign(1, old_weight)
            new_direction = 0 if new_weight == 0 else math.copysign(1, new_weight)
            total_cost = previous_nav * abs(delta_weight) * execution_cost
            same_episode = old_direction == new_direction and old_direction != 0
            if same_episode:
                active[asset]["net_pnl_usd"] -= total_cost
                active[asset]["order_count"] += 1
            elif old_direction != 0:
                close_fraction = min(1.0, abs(old_weight) / abs(delta_weight))
                active[asset]["net_pnl_usd"] -= total_cost * close_fraction
                active[asset]["order_count"] += 1
                completed.append(
                    _close_episode(
                        active.pop(asset),
                        exit_date=current_date,
                        exit_price=price,
                        end=end,
                    )
                )
            if new_direction != 0 and not same_episode and asset not in active:
                direction = "LONG" if new_direction > 0 else "SHORT"
                episode = _new_episode(
                    asset=asset,
                    direction=direction,
                    entry_date=current_date,
                    entry_price=price,
                )
                open_cost = total_cost
                if old_direction != 0:
                    open_cost *= max(0.0, 1 - abs(old_weight) / abs(delta_weight))
                episode["net_pnl_usd"] -= open_cost
                episode["order_count"] = 1
                active[asset] = episode
            weights[asset] = new_weight
        previous_nav = float(daily["navUsd"])

    last_date_index = date_indexes[end.isoformat()]
    for asset, episode in active.items():
        price = float(engine["closes"][last_date_index][asset_indexes[asset]])
        completed.append(
            _close_episode(episode, exit_date=None, exit_price=price, end=end)
        )
    return sorted(
        completed,
        key=lambda item: (item["exit_date"] or item["held_through"], item["asset"]),
        reverse=True,
    )


def _blocked_report(
    strategy_id: str, *, started: datetime, completed: datetime, run_id: str
) -> dict[str, Any]:
    details = {
        "funding-neutral": {
            "identity": "funding-neutral",
            "name": "Funding Neutral",
            "repository": "balkhaev/fin",
            "blockers": [
                "Нет двухлетнего архива predicted funding с точными timestamps.",
                "Нет полного исторического стакана, open interest и basis для входных фильтров.",
                "OHLC-свечи не заменяют эти сигналы: приблизительный результат не рассчитывается.",
            ],
        },
        "consensus-wif-dot": {
            "identity": "consensus-wif-dot-v1",
            "name": "Consensus WIF + DOT",
            "repository": "balkhaev/trader",
            "blockers": [
                "Нет полного двухлетнего WIF open-interest и premium-index ряда.",
                "Нет синхронизированного исторического funding-ряда DOT для текущих правил.",
                "OHLC-свечи не заменяют факторные входы: приблизительный результат не рассчитывается.",
            ],
        },
    }
    detail = details[strategy_id]
    return {
        "schema_version": 1,
        "strategy_id": strategy_id,
        "strategy_identity": detail["identity"],
        "strategy_name": detail["name"],
        "report_kind": "on_demand_backtest_preflight",
        "execution": {
            "status": "blocked",
            "run_id": run_id,
            "trigger": "user_click",
            "started_at_utc": _utc_iso(started),
            "completed_at_utc": _utc_iso(completed),
            "duration_seconds": round((completed - started).total_seconds(), 3),
        },
        "window": {
            "requested_years": REQUESTED_YEARS,
            "start": None,
            "end": None,
            "label": "Последние 2 года",
            "trade_inclusion": "Недоступно без полного набора факторных входов",
        },
        "evidence": {
            "status": "blocked_missing_inputs",
            "status_label": "Нужны исторические факторы",
            "cagr_threshold_percent": CAGR_THRESHOLD_PERCENT,
            "cagr_threshold_passed": None,
            "headline": "Честный replay сейчас невозможен",
            "summary": (
                "Предварительная проверка выполнена. Сервер не подменяет отсутствующие "
                "исторические сигналы свечами и не показывает выдуманную доходность."
            ),
        },
        "metrics": None,
        "trade_count": 0,
        "trades": [],
        "blockers": detail["blockers"],
        "limitations": [
            "Paper-стратегия продолжает работать на текущих реальных данных.",
            "Для бэктеста нужен воспроизводимый timestamped архив всех входных факторов.",
        ],
        "provenance": {
            "source_repository": detail["repository"],
            "strategy_identity": detail["identity"],
            "preflight_only": True,
            "is_current_paper_account": False,
        },
        "historical_reference": None,
    }


def run_backtest(
    strategy_id: str,
    *,
    now: datetime | None = None,
    history_loader: HistoryLoader = load_binance_daily_histories,
) -> dict[str, Any]:
    """Run a fresh two-year replay without mutating a paper account."""

    if strategy_id not in {
        "funding-neutral",
        "consensus-wif-dot",
        "dyn-iv113",
        "atlas-nx",
    }:
        raise KeyError(strategy_id)
    started = (now or datetime.now(UTC)).astimezone(UTC)
    run_id = str(uuid4())
    if strategy_id in {"funding-neutral", "consensus-wif-dot"}:
        return _blocked_report(
            strategy_id,
            started=started,
            completed=datetime.now(UTC),
            run_id=run_id,
        )

    window_end = started.date() - timedelta(days=1)
    window_start = _two_years_before(window_end)
    history_start = window_start - timedelta(days=WARMUP_DAYS)
    if strategy_id == "dyn-iv113":
        strategy_module = dyn_paper
        engine_module = "dyn_paper"
        strategy_identity = dyn_paper.STRATEGY_ID
        execution_cost = dyn_paper.EXECUTION_COST
        reset_date = (window_start - timedelta(days=1)).isoformat()
    else:
        strategy_module = atlas_nx_r1_paper
        engine_module = "atlas_nx_r1_paper"
        strategy_identity = atlas_nx_r1_paper.STRATEGY_ID
        execution_cost = atlas_nx_r1_paper.EXECUTION_COST
        reset_date = window_start.isoformat()

    histories, failures, request_count = history_loader(
        strategy_module.MARKET_SYMBOLS, history_start, window_end
    )
    if len(histories) < strategy_module.MINIMUM_ASSETS:
        raise ValueError(
            f"Only {len(histories)} of {len(strategy_module.MARKET_SYMBOLS)} "
            "market histories were usable"
        )
    engine = strategy_module.build_engine(histories, failures)
    if window_end.isoformat() not in engine["dates"]:
        raise ValueError("The latest closed UTC day is missing from the market history")
    continuation = strategy_module.paper_continuation(
        engine,
        reset_date=reset_date,
        initial_nav_usd=INITIAL_NAV_USD,
    )
    daily = [
        item
        for item in continuation["daily"]
        if window_start.isoformat() <= str(item["date"]) <= window_end.isoformat()
    ]
    scoped_continuation = {**continuation, "daily": daily}
    metric_values = _metrics(daily, start=window_start, end=window_end)
    trades = _trade_episodes(
        engine=engine,
        continuation=scoped_continuation,
        start=window_start,
        end=window_end,
        execution_cost=execution_cost,
    )
    completed = datetime.now(UTC)
    threshold_passed = metric_values["cagr_percent"] >= CAGR_THRESHOLD_PERCENT
    return {
        "schema_version": 1,
        "strategy_id": strategy_id,
        "strategy_identity": strategy_identity,
        "strategy_name": strategy_identity,
        "report_kind": "on_demand_backtest",
        "execution": {
            "status": "completed",
            "run_id": run_id,
            "trigger": "user_click",
            "started_at_utc": _utc_iso(started),
            "completed_at_utc": _utc_iso(completed),
            "duration_seconds": round((completed - started).total_seconds(), 3),
        },
        "window": {
            "requested_years": REQUESTED_YEARS,
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "label": "Последние 2 закрытых года UTC",
            "trade_inclusion": "Эпизод открыт не раньше начала окна",
        },
        "evidence": {
            "status": "computed",
            "status_label": "Рассчитано сейчас",
            "cagr_threshold_percent": CAGR_THRESHOLD_PERCENT,
            "cagr_threshold_passed": threshold_passed,
            "headline": (
                "Порог 50%+ CAGR пройден"
                if threshold_passed
                else "Порог 50%+ CAGR не пройден"
            ),
            "summary": (
                "Сервер загрузил закрытые Binance Spot свечи и заново прогнал "
                "текущую реализацию стратегии с начальным капиталом $10 000."
            ),
        },
        "metrics": metric_values,
        "trade_count": len(trades),
        "trades": trades,
        "blockers": [],
        "limitations": [
            "Исполнение моделируется по дневной цене текущего движка с его комиссиями и financing.",
            "Результат не меняет текущий paper-счёт и не отправляет биржевые ордера.",
            "Историческая доходность не гарантирует будущую.",
        ],
        "provenance": {
            "source_repository": "balkhaev/fin",
            "strategy_identity": strategy_identity,
            "engine_module": engine_module,
            "market_data_source": "Binance Spot public klines",
            "market_data_as_of": window_end.isoformat(),
            "market_data_requests": request_count,
            "input_sha256": _input_sha256(histories),
            "failed_symbols": failures,
            "is_current_paper_account": False,
        },
        "historical_reference": None,
    }
