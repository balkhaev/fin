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

from finruntime.strategies import dyn_paper

from .atlas_v517_backtest import run_atlas_v517_replay
from .backtests import backtest_report
from .factor_backtests import run_consensus_backtest, run_funding_backtest

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
FactorRunner = Callable[[str, date, date], dict[str, Any]]


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


def _atlas_v517_report(*, started: datetime, run_id: str) -> dict[str, Any]:
    replay = run_atlas_v517_replay()
    completed = datetime.now(UTC)
    metrics = replay["metrics"]
    episodes = replay["episodes"]
    threshold_passed = metrics["cagr_percent"] >= CAGR_THRESHOLD_PERCENT
    stress = replay["stress_metrics"]
    return {
        "schema_version": 1,
        "strategy_id": "atlas-nx",
        "strategy_identity": "v517_v524_v75_tristate_guard",
        "paper_strategy_identity": "atlas_nx_r1",
        "strategy_name": "Atlas V517 · исторический replay",
        "report_kind": "on_demand_historical_replay",
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
            "start": replay["dates"][0].isoformat(),
            "end": replay["dates"][-1].isoformat(),
            "label": "Полный закреплённый период V517/V524 · 2021—2026 H1",
            "trade_inclusion": (
                "CAGR рассчитан на полном исследовательском периоде; "
                "таблица ниже ограничена последними двумя годами потока"
            ),
        },
        "requested_window": {
            "requested_years": REQUESTED_YEARS,
            "start": replay["requested_start"].isoformat(),
            "end": replay["requested_end"].isoformat(),
            "label": "Последние 2 года доступного V517-потока",
        },
        "evidence": {
            "status": "verified",
            "status_label": "Пересчитано из закреплённого потока",
            "cagr_threshold_percent": CAGR_THRESHOLD_PERCENT,
            "cagr_threshold_passed": threshold_passed,
            "headline": "Atlas V517 подтверждает 50%+ CAGR на полном периоде",
            "summary": (
                "При нажатии сервер заново применяет tri-state leverage overlay к "
                "checksum-закреплённому V75 account stream. Полный период даёт "
                f"{metrics['cagr_percent']:.3f}% CAGR; последние два года потока — "
                f"{replay['requested_window_metrics']['cagr_percent']:.3f}%. "
                "Это исторический V517, а не результат текущего Atlas NX R1 paper-счёта."
            ),
            "parameters_informed_by_known_history": True,
            "program_level_holdout_pristine": False,
            "account_level_only": True,
        },
        "metrics": metrics,
        "requested_window_metrics": replay["requested_window_metrics"],
        "stress_metrics": stress,
        "trade_table_kind": "account_leverage_episodes",
        "trade_count": len(episodes),
        "trades": episodes,
        "blockers": [],
        "limitations": [
            (
                "50,55% CAGR относится к полному периоду 2021—2026 H1; на последних "
                "двух годах того же потока CAGR равен "
                f"{replay['requested_window_metrics']['cagr_percent']:.2f}%."
            ),
            (
                "Параметры V517 выбирались с учётом известной истории: program-level "
                "holdout не pristine, результат не является независимым прогнозом."
            ),
            (
                "Исходник является account-level V75 equity stream. Поэтому таблица "
                "показывает эпизоды изменения целевого плеча, а не сделки по отдельным монетам."
            ),
            (
                "Текущий paper runtime Atlas NX R1 — отдельная reconstructed identity; "
                "он не наследует метрики и forward-state V517/V75."
            ),
            "Бектест не меняет paper-счёт и не отправляет биржевые ордера.",
        ],
        "provenance": {
            "source_repository": "balkhaev/fin",
            "source_commit": "0fd3c5deed2d97be44bbad8acf4afd4105bd2010",
            "strategy_commit": "663cd5f19ed381cd616bf783faf5a30c5df8baaf",
            "strategy_identity": "v517_v524_v75_tristate_guard",
            "paper_strategy_identity": "atlas_nx_r1",
            "engine_module": "atlas_v517_backtest",
            "market_data_source": "pinned V75 account-level equity stream",
            "market_data_as_of": replay["dates"][-1].isoformat(),
            "input_sha256": replay["input_sha256"],
            "policy": {
                "high_leverage": 2.075,
                "base_leverage": 0.97,
                "low_leverage": 0.60,
                "rebalance_days": 10,
                "no_trade_band": 0.04,
                "transfer_cost_bps": 10.0,
                "financing_annual": 0.08,
            },
            "position_level_margin_replay_complete": False,
            "is_current_paper_account": False,
        },
        "historical_reference": None,
    }


def _run_factor_strategy(strategy_id: str, start: date, end: date) -> dict[str, Any]:
    if strategy_id == "consensus-wif-dot":
        return run_consensus_backtest(start, end)
    if strategy_id == "funding-neutral":
        return run_funding_backtest(start, end)
    raise KeyError(strategy_id)


def _factor_report(
    strategy_id: str,
    replay: dict[str, Any],
    *,
    start: date,
    end: date,
    started: datetime,
    run_id: str,
) -> dict[str, Any]:
    completed = datetime.now(UTC)
    metrics = replay["metrics"]
    trades = replay["trades"]
    threshold_passed = metrics["cagr_percent"] >= CAGR_THRESHOLD_PERCENT
    details = {
        "consensus-wif-dot": {
            "identity": "consensus-wif-dot-v1",
            "name": "Consensus WIF + DOT",
            "repository": "balkhaev/trader",
            "source": "Binance USD-M official klines, premium, funding and OI archives",
            "headline": (
                "Текущий Consensus-контракт пересчитан на реальных factor-рядах"
            ),
            "summary": (
                "Сервер заново построил WIF OI-flush и DOT post-funding сигналы только "
                "из уже известных на тот момент данных, применил текущий risk accelerator, "
                "20 bps round-turn cost, stops, targets и time exits."
            ),
            "limitations": [
                "WIF open interest взят из официальных 5-минутных Binance metrics; premium и объём синхронизированы с закрытыми 15m свечами.",
                "Если stop и target попали в одну 15m свечу, replay консервативно считает stop первым.",
                "Сигналы редкие; sticky hard-stop после 15% drawdown запрещает последующие входы так же, как текущий paper-контракт.",
            ],
        },
        "funding-neutral": {
            "identity": "funding-neutral",
            "name": "Funding Neutral",
            "repository": "balkhaev/fin",
            "source": "Binance USD-M and Bybit public funding/mark-price APIs",
            "headline": "Funding-spread core пересчитан на Binance + Bybit",
            "summary": (
                "Сервер причинно сопоставил опубликованные funding rates пяти активов, "
                "нормализовал интервалы, применил trailing-median confirmation, basis, "
                "комиссии, slippage и safety buffers, затем проверил фактический 24h P&L."
            ),
            "limitations": [
                "Исторические full-depth order books и точный venue OI за весь период недоступны; replay проверяет funding/basis core, а эти два live-фильтра помечены как неприменённые.",
                "Каждая пара использует текущий фиксированный notional $1 000 и максимум одну одновременную позицию.",
                "Нулевое число сделок означает, что строгий expected-net порог не был пройден, а не отсутствие рыночных данных.",
            ],
        },
    }
    detail = details[strategy_id]
    return {
        "schema_version": 1,
        "strategy_id": strategy_id,
        "strategy_identity": detail["identity"],
        "strategy_name": detail["name"],
        "report_kind": "on_demand_factor_backtest",
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
            "start": start.isoformat(),
            "end": end.isoformat(),
            "label": "Последние 2 закрытых года UTC",
            "trade_inclusion": "Сигнал и вход находятся внутри двухлетнего окна",
        },
        "evidence": {
            "status": "computed",
            "status_label": "Рассчитано сейчас",
            "cagr_threshold_percent": CAGR_THRESHOLD_PERCENT,
            "cagr_threshold_passed": threshold_passed,
            "headline": detail["headline"],
            "summary": detail["summary"],
        },
        "metrics": metrics,
        "trade_count": len(trades),
        "trades": trades,
        "blockers": [],
        "limitations": [
            *detail["limitations"],
            "Результат не меняет paper-счёт, не отправляет ордера и не гарантирует будущую доходность.",
        ],
        "diagnostics": replay["diagnostics"],
        "provenance": {
            "source_repository": detail["repository"],
            "strategy_identity": detail["identity"],
            "engine_module": "factor_backtests",
            "market_data_source": detail["source"],
            "market_data_as_of": end.isoformat(),
            "market_data_requests": replay["market_data_requests"],
            "market_data_bytes": replay["market_data_bytes"],
            "input_sha256": replay["input_sha256"],
            "is_current_paper_account": False,
        },
        "historical_reference": None,
    }


def run_backtest(
    strategy_id: str,
    *,
    now: datetime | None = None,
    history_loader: HistoryLoader = load_binance_daily_histories,
    factor_runner: FactorRunner = _run_factor_strategy,
) -> dict[str, Any]:
    """Run a fresh two-year replay without mutating a paper account."""

    if strategy_id not in {
        "funding-neutral",
        "consensus-wif-dot",
        "dyn-iv113",
        "dyn-iv113-risk50",
        "dyn-iv113-band2",
        "atlas-nx",
        "atlas-v517-reference",
    }:
        raise KeyError(strategy_id)
    started = datetime.now(UTC)
    window_anchor = (now or started).astimezone(UTC)
    run_id = str(uuid4())
    window_end = window_anchor.date() - timedelta(days=1)
    window_start = _two_years_before(window_end)
    if strategy_id in {"funding-neutral", "consensus-wif-dot"}:
        replay = factor_runner(strategy_id, window_start, window_end)
        return _factor_report(
            strategy_id,
            replay,
            start=window_start,
            end=window_end,
            started=started,
            run_id=run_id,
        )
    if strategy_id == "atlas-nx":
        completed = datetime.now(UTC)
        report = backtest_report("atlas-nx")
        report["report_kind"] = "on_demand_unavailable"
        report["execution"] = {
            "status": "not_available",
            "run_id": run_id,
            "trigger": "user_click",
            "started_at_utc": _utc_iso(started),
            "completed_at_utc": _utc_iso(completed),
            "duration_seconds": round((completed - started).total_seconds(), 3),
        }
        return report
    if strategy_id == "atlas-v517-reference":
        return _atlas_v517_report(
            started=started,
            run_id=run_id,
        )

    dyn_profile_name = {
        "dyn-iv113": "baseline",
        "dyn-iv113-risk50": "risk50",
        "dyn-iv113-band2": "band2",
    }[strategy_id]
    profile = dyn_paper.get_profile(dyn_profile_name)
    history_start = window_start - timedelta(days=WARMUP_DAYS)
    strategy_module = dyn_paper
    engine_module = "dyn_paper"
    strategy_identity = profile.strategy_id
    strategy_name = profile.label
    execution_cost = dyn_paper.EXECUTION_COST
    reset_date = (window_start - timedelta(days=1)).isoformat()

    histories, failures, request_count = history_loader(
        strategy_module.MARKET_SYMBOLS, history_start, window_end
    )
    if len(histories) < strategy_module.MINIMUM_ASSETS:
        raise ValueError(
            f"Only {len(histories)} of {len(strategy_module.MARKET_SYMBOLS)} "
            "market histories were usable"
        )
    engine = strategy_module.build_profile_engine(histories, failures, profile)
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
        "strategy_name": strategy_name,
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
