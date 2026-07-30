"""Paper-only Atlas NX reconstruction from committed FIN component specifications.

This is deliberately a new strategy identity.  The exact V75 target producer is
not present in repository history, so its historical results and forward clock
are never inherited by this implementation.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .dyn_paper import _fetch_asset_history, _utc_now, _write_atomic

STRATEGY_ID = "atlas_nx_r1"
PREDECESSOR_STRATEGY_ID = "v75_atlas_nx"
SNAPSHOT_DATE = "2026-07-29"
HISTORY_LIMIT = 600
ASSETS = ("ADA", "BCH", "BNB", "BTC", "DOGE", "EOS", "ETH", "LTC", "XRP")
MARKET_SYMBOLS = tuple(f"{asset}USDT" for asset in ASSETS)
MINIMUM_ASSETS = 7
EXECUTION_COST = 0.004
FINANCING_ANNUAL = 0.06
RETURN_CAP = 0.30
NO_TRADE_BAND = 0.10
HIGH_WATER_THRESHOLDS = (1.75, 2.50)
DEFENSIVE_WEIGHTS = (0.0, 0.10, 0.20)
MAXIMUM_ACCELERATOR = (0.35, 0.30, 0.25)
GROSS_CAPS = (1.10, 1.05, 1.00)
ONCHAIN_MAX_AGE_HOURS = 48.0
EPSILON = 1e-10


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def _zero_row(length: int) -> list[float]:
    return [0.0] * length


def _gross(row: list[float]) -> float:
    return sum(abs(value) for value in row)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _sample_standard_deviation(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    average = sum(values) / len(values)
    variance = sum((value - average) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


def _ema(values: list[float | None], span: int) -> list[float | None]:
    alpha = 2 / (span + 1)
    previous: float | None = None
    output: list[float | None] = []
    observations = 0
    for value in values:
        if value is not None:
            observations += 1
            previous = (
                value if previous is None else alpha * value + (1 - alpha) * previous
            )
        output.append(previous if observations >= span else None)
    return output


def _annualized_volatility(
    returns: list[float | None], index: int, window: int, minimum: int
) -> float | None:
    sample = [
        value
        for value in returns[max(0, index - window + 1) : index + 1]
        if value is not None and math.isfinite(value)
    ]
    deviation = _sample_standard_deviation(sample) if len(sample) >= minimum else None
    return deviation * math.sqrt(365) if deviation is not None else None


def _normalized_inverse_volatility(
    indexes: list[int], volatility: list[float | None], length: int
) -> list[float]:
    output = _zero_row(length)
    raw = [
        1 / _clamp(volatility[index] or 0.0, 0.15, 3.0)
        if volatility[index] is not None
        else 0.0
        for index in indexes
    ]
    total = sum(raw)
    if total <= 0:
        return output
    for index, value in zip(indexes, raw, strict=True):
        output[index] = value / total
    return output


def ratchet_stage(high_water: float, initial_nav: float, prior_stage: int) -> int:
    """Return the irreversible frozen high-water stage."""
    if initial_nav <= 0:
        raise ValueError("initial_nav must be positive")
    stage = max(0, min(2, int(prior_stage)))
    multiple = high_water / initial_nav
    if multiple >= HIGH_WATER_THRESHOLDS[1]:
        return 2
    if multiple >= HIGH_WATER_THRESHOLDS[0]:
        return max(stage, 1)
    return stage


def onchain_accelerator_scale(
    observed_at: datetime | None,
    now: datetime,
    score: float,
    stage: int,
) -> float:
    """Fail closed when the on-chain publication is absent or older than 48h."""
    if observed_at is None:
        return 0.0
    observed = (
        observed_at
        if observed_at.tzinfo is not None
        else observed_at.replace(tzinfo=UTC)
    )
    current = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    age_hours = (
        current.astimezone(UTC) - observed.astimezone(UTC)
    ).total_seconds() / 3600
    if age_hours < 0 or age_hours > ONCHAIN_MAX_AGE_HOURS:
        return 0.0
    return _clamp(score, 0.0, 1.0) * MAXIMUM_ACCELERATOR[max(0, min(2, stage))]


def _scheduled(weights: list[list[float]], dates: list[str]) -> list[list[float]]:
    if not weights:
        return []
    current = _zero_row(len(weights[0]))
    output: list[list[float]] = []
    for date_text, desired in zip(dates, weights, strict=True):
        exit_required = _gross(current) > EPSILON and _gross(desired) <= EPSILON
        rebalance_day = date.fromisoformat(date_text).weekday() == 0
        material_change = (
            sum(abs(value - current[index]) for index, value in enumerate(desired))
            >= NO_TRADE_BAND
        )
        if exit_required or (rebalance_day and material_change):
            current = list(desired)
        output.append(list(current))
    return output


def _volatility_multiplier(volatility: float | None) -> float:
    if volatility is None or not math.isfinite(volatility):
        return 0.0
    if volatility <= 0.25:
        return 1.0
    if volatility <= 0.35:
        return 0.75
    return 0.50


def _apply_risk(base: list[float], *, stage: int, vol_multiplier: float) -> list[float]:
    scaled = [value * vol_multiplier for value in base]
    gross = _gross(scaled)
    cap = GROSS_CAPS[stage]
    if gross > cap:
        scaled = [value * cap / gross for value in scaled]
    return scaled


def load_asset_histories() -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    histories: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(_fetch_asset_history, symbol): symbol
            for symbol in MARKET_SYMBOLS
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                histories.append(future.result())
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                failures.append({"symbol": symbol, "reason": str(error)})
    histories.sort(key=lambda item: ASSETS.index(item["asset"]))
    failures.sort(key=lambda item: MARKET_SYMBOLS.index(item["symbol"]))
    return histories, failures


def build_engine(
    histories: list[dict[str, Any]], failed_symbols: list[dict[str, str]]
) -> dict[str, Any]:
    """Build causal V27/V4 reconstruction targets from closed daily candles."""
    history_by_asset = {history["asset"]: history for history in histories}
    if not histories:
        raise ValueError("Atlas received no usable daily histories")
    latest_market_date = max(
        date.fromisoformat(max(history["bars"])) for history in histories
    )
    inactive_assets = [
        asset
        for asset in ASSETS
        if asset in history_by_asset
        and (
            latest_market_date
            - date.fromisoformat(max(history_by_asset[asset]["bars"]))
        ).days
        > 2
    ]
    assets = [
        asset
        for asset in ASSETS
        if asset in history_by_asset and asset not in inactive_assets
    ]
    if len(assets) < MINIMUM_ASSETS:
        raise ValueError(
            f"Only {len(assets)} Atlas assets returned usable daily history; "
            f"at least {MINIMUM_ASSETS} are required"
        )
    common_dates: set[str] | None = None
    for asset in assets:
        dates = set(history_by_asset[asset]["bars"])
        common_dates = dates if common_dates is None else common_dates & dates
    ordered_dates = sorted(common_dates or ())
    if len(ordered_dates) < 260:
        raise ValueError("Atlas requires at least 260 common closed daily candles")

    closes = [
        [float(history_by_asset[asset]["bars"][date_text]["close"]) for asset in assets]
        for date_text in ordered_dates
    ]
    highs = [
        [float(history_by_asset[asset]["bars"][date_text]["high"]) for asset in assets]
        for date_text in ordered_dates
    ]
    lows = [
        [float(history_by_asset[asset]["bars"][date_text]["low"]) for asset in assets]
        for date_text in ordered_dates
    ]
    returns = [_zero_row(len(assets))]
    for index in range(1, len(ordered_dates)):
        returns.append(
            [
                _clamp(
                    closes[index][column] / closes[index - 1][column] - 1,
                    -RETURN_CAP,
                    RETURN_CAP,
                )
                for column in range(len(assets))
            ]
        )

    returns_by_asset = [
        [row[column] for row in returns] for column in range(len(assets))
    ]
    close_by_asset = [[row[column] for row in closes] for column in range(len(assets))]
    ema100 = [_ema(values, 100) for values in close_by_asset]
    volatility: list[list[float | None]] = []
    for date_index in range(len(ordered_dates)):
        volatility.append(
            [
                _annualized_volatility(
                    [float(value) for value in returns_by_asset[column]],
                    date_index - 1,
                    60,
                    30,
                )
                if date_index > 0
                else None
                for column in range(len(assets))
            ]
        )

    defensive_assets = [
        assets.index(asset) for asset in ("BTC", "ETH") if asset in assets
    ]
    donchian_active = {column: False for column in defensive_assets}
    core_rows: list[list[float]] = []
    defensive_rows: list[list[float]] = []
    for date_index in range(len(ordered_dates)):
        scores: list[tuple[int, float]] = []
        for column in range(len(assets)):
            momenta: list[float] = []
            for lookback in (63, 126, 252):
                if date_index >= lookback:
                    momenta.append(
                        closes[date_index][column]
                        / closes[date_index - lookback][column]
                        - 1
                    )
            positive = [value for value in momenta if value > 0]
            if len(positive) >= 2 and volatility[date_index][column] is not None:
                scores.append((column, sum(positive) / len(positive)))
        selected = [
            column
            for column, _score in sorted(scores, key=lambda item: (-item[1], item[0]))[
                :3
            ]
        ]
        core_rows.append(
            _normalized_inverse_volatility(
                selected, volatility[date_index], len(assets)
            )
        )

        families: list[list[float]] = []
        breadth_indexes: list[int] = []
        dual_indexes: list[int] = []
        donchian_indexes: list[int] = []
        for column in defensive_assets:
            momentum63 = (
                closes[date_index][column] / closes[date_index - 63][column] - 1
                if date_index >= 63
                else 0.0
            )
            momentum126 = (
                closes[date_index][column] / closes[date_index - 126][column] - 1
                if date_index >= 126
                else 0.0
            )
            momentum252 = (
                closes[date_index][column] / closes[date_index - 252][column] - 1
                if date_index >= 252
                else 0.0
            )
            above_ema = ema100[column][date_index] is not None and closes[date_index][
                column
            ] > float(ema100[column][date_index])
            if (
                sum(value > 0 for value in (momentum63, momentum126, momentum252)) >= 2
                and above_ema
            ):
                breadth_indexes.append(column)
            if momentum63 > 0 and momentum126 > 0 and above_ema:
                dual_indexes.append(column)
            if date_index >= 90:
                entry = closes[date_index][column] > max(
                    highs[row][column] for row in range(date_index - 90, date_index)
                )
                exit_signal = closes[date_index][column] < min(
                    lows[row][column] for row in range(date_index - 45, date_index)
                )
                if exit_signal:
                    donchian_active[column] = False
                elif entry:
                    donchian_active[column] = True
            if donchian_active[column]:
                donchian_indexes.append(column)
        for indexes in (breadth_indexes, dual_indexes, donchian_indexes):
            families.append(
                _normalized_inverse_volatility(
                    indexes, volatility[date_index], len(assets)
                )
            )
        defensive_rows.append(
            [
                sum(family[column] for family in families) / len(families)
                for column in range(len(assets))
            ]
        )

    combined = [
        [
            core_rows[index][column] * (1 - DEFENSIVE_WEIGHTS[0])
            + defensive_rows[index][column] * DEFENSIVE_WEIGHTS[0]
            for column in range(len(assets))
        ]
        for index in range(len(ordered_dates))
    ]
    held = _scheduled(combined, ordered_dates)
    causal_base = [
        list(held[index - 1]) if index > 0 else _zero_row(len(assets))
        for index in range(len(ordered_dates))
    ]
    base_returns = [
        sum(
            causal_base[index - 1][column] * returns[index][column]
            for column in range(len(assets))
        )
        if index > 0
        else 0.0
        for index in range(len(ordered_dates))
    ]
    realized_volatility = [
        _annualized_volatility(
            [float(value) for value in base_returns], index - 1, 63, 32
        )
        if index > 0
        else None
        for index in range(len(ordered_dates))
    ]
    vol_multipliers = [_volatility_multiplier(value) for value in realized_volatility]
    targets = [
        _apply_risk(row, stage=0, vol_multiplier=vol_multipliers[index])
        for index, row in enumerate(causal_base)
    ]
    return {
        "assets": assets,
        "baseTarget": causal_base,
        "closes": closes,
        "dates": ordered_dates,
        "defensiveTarget": defensive_rows,
        "failedSymbols": failed_symbols,
        "inactiveSymbols": [f"{asset}USDT" for asset in inactive_assets],
        "returns": returns,
        "target": targets,
        "volatility": realized_volatility,
        "volatilityMultiplier": vol_multipliers,
    }


def _elapsed_days(previous: str, current: str) -> int:
    return max(1, (date.fromisoformat(current) - date.fromisoformat(previous)).days)


def paper_continuation(
    engine: dict[str, Any], *, reset_date: str, initial_nav_usd: float
) -> dict[str, Any]:
    indexes = [
        index for index, value in enumerate(engine["dates"]) if value >= reset_date
    ]
    if not indexes:
        raise ValueError(
            "The market window does not include the Atlas paper reset date"
        )
    nav = initial_nav_usd
    high_water = initial_nav_usd
    stage = 0
    previous_target = _zero_row(len(engine["assets"]))
    previous_date = engine["dates"][indexes[0]]
    daily: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []
    last_entry_prices: dict[str, float] = {}
    for date_index in indexes:
        current_date = engine["dates"][date_index]
        stage = ratchet_stage(high_water, initial_nav_usd, stage)
        defensive_weight = DEFENSIVE_WEIGHTS[stage]
        base = [
            engine["baseTarget"][date_index][column] * (1 - defensive_weight)
            + engine["defensiveTarget"][date_index - 1][column] * defensive_weight
            if date_index > 0
            else 0.0
            for column in range(len(engine["assets"]))
        ]
        target = _apply_risk(
            base,
            stage=stage,
            vol_multiplier=engine["volatilityMultiplier"][date_index],
        )
        gross_return = sum(
            previous_target[column] * engine["returns"][date_index][column]
            for column in range(len(previous_target))
        )
        turnover = sum(
            abs(target[column] - previous_target[column])
            for column in range(len(previous_target))
        )
        financing = (
            max(0.0, _gross(previous_target) - 1)
            * FINANCING_ANNUAL
            * _elapsed_days(previous_date, current_date)
            / 365
        )
        net_return = gross_return - turnover * EXECUTION_COST - financing
        nav *= 1 + net_return
        if not math.isfinite(nav) or nav <= 0:
            raise ValueError("Atlas forward paper account exhausted its capital")
        high_water = max(high_water, nav)
        for column, new_weight in enumerate(target):
            old_weight = previous_target[column]
            if abs(new_weight - old_weight) <= EPSILON:
                continue
            asset = engine["assets"][column]
            price = engine["closes"][date_index][column]
            if abs(new_weight) <= EPSILON:
                last_entry_prices.pop(asset, None)
            elif abs(old_weight) <= EPSILON or math.copysign(
                1, old_weight
            ) != math.copysign(1, new_weight):
                last_entry_prices[asset] = price
            executions.append(
                {
                    "asset": asset,
                    "date": current_date,
                    "deltaWeight": new_weight - old_weight,
                    "newWeight": new_weight,
                    "oldWeight": old_weight,
                    "price": price,
                    "side": "BUY" if new_weight - old_weight > 0 else "SELL",
                }
            )
        daily.append(
            {
                "date": current_date,
                "grossExposure": _gross(previous_target),
                "navUsd": nav,
                "return": net_return,
                "tradeCost": turnover * EXECUTION_COST,
            }
        )
        previous_target = target
        previous_date = current_date
    return {
        "daily": daily,
        "executions": executions,
        "highWater": high_water,
        "lastEntryPrices": last_entry_prices,
        "nav": nav,
        "ratchetStage": stage,
        "target": previous_target,
    }


def _mark_to_live(
    engine: dict[str, Any],
    histories: list[dict[str, Any]],
    continuation: dict[str, Any],
) -> dict[str, Any]:
    history_by_asset = {history["asset"]: history for history in histories}
    latest_index = len(engine["dates"]) - 1
    closed_nav = float(continuation["nav"])
    cash = closed_nav * (1 - sum(continuation["target"]))
    nav = cash
    positions: list[dict[str, Any]] = []
    for column, weight in enumerate(continuation["target"]):
        if abs(weight) <= EPSILON:
            continue
        asset = engine["assets"][column]
        closed_price = engine["closes"][latest_index][column]
        live_price = float(history_by_asset[asset]["liveCandle"]["close"])
        quantity = closed_nav * weight / closed_price
        notional = quantity * live_price
        nav += notional
        entry_price = continuation["lastEntryPrices"].get(asset, closed_price)
        unrealized = quantity * (live_price - entry_price)
        positions.append(
            {
                "asset": asset,
                "averageEntryPrice": entry_price,
                "notionalUsd": notional,
                "price": live_price,
                "quantity": quantity,
                "unrealizedPnlPercent": live_price / entry_price - 1,
                "unrealizedPnlUsd": unrealized,
                "weight": 0.0,
            }
        )
    positions.sort(key=lambda item: -abs(item["notionalUsd"]))
    if not math.isfinite(nav) or nav <= 0:
        raise ValueError("Atlas live-marked paper NAV is invalid")
    for position in positions:
        position["weight"] = position["notionalUsd"] / nav
    return {"cashWeight": cash / nav, "navUsd": nav, "positions": positions}


def compute_forward_state(
    histories: list[dict[str, Any]],
    failed_symbols: list[dict[str, str]],
    *,
    reset_date: str = SNAPSHOT_DATE,
    initial_nav_usd: float = 10_000.0,
) -> dict[str, Any]:
    generated_at = _utc_now()
    engine = build_engine(histories, failed_symbols)
    continuation = paper_continuation(
        engine, reset_date=reset_date, initial_nav_usd=initial_nav_usd
    )
    live = _mark_to_live(engine, histories, continuation)
    latest_index = len(engine["dates"]) - 1
    stage = continuation["ratchetStage"]
    candles: list[dict[str, Any]] = []
    for history in histories:
        items = [
            {
                "timestamp_ms": candle["openTime"],
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
            }
            for _date_text, candle in sorted(history["bars"].items())[-119:]
        ]
        live_candle = history["liveCandle"]
        if not live_candle["closed"]:
            items.append(
                {
                    "timestamp_ms": live_candle["openTime"],
                    "open": live_candle["open"],
                    "high": live_candle["high"],
                    "low": live_candle["low"],
                    "close": live_candle["close"],
                }
            )
        candles.append(
            {
                "asset": history["asset"],
                "exchange_id": "binance",
                "timeframe": "1d",
                "items": items,
            }
        )
    return {
        "schema_version": 1,
        "strategyId": STRATEGY_ID,
        "predecessorStrategyId": PREDECESSOR_STRATEGY_ID,
        "identityKind": "reconstruction",
        "forwardClockReset": True,
        "historicalMetricsInherited": False,
        "status": "ready",
        "asOf": engine["dates"][latest_index],
        "generatedAt": generated_at,
        "marketDataAt": generated_at,
        "dataAssetCount": len(engine["assets"]),
        "failedSymbols": failed_symbols,
        "inactiveSymbols": engine["inactiveSymbols"],
        "candles": candles,
        "positions": live["positions"],
        "cashWeight": live["cashWeight"],
        "targetGross": _gross(continuation["target"]),
        "grossCap": GROSS_CAPS[stage],
        "ratchetStage": stage,
        "defensiveWeight": DEFENSIVE_WEIGHTS[stage],
        "volatility": engine["volatility"][latest_index],
        "volatilityMultiplier": engine["volatilityMultiplier"][latest_index],
        "onchainAcceleratorScale": 0.0,
        "onchainStatus": "disabled_stale_or_missing",
        "paper": {
            "account": {"initialNavUsd": initial_nav_usd, "resetDate": reset_date},
            "daily": continuation["daily"],
            "executions": continuation["executions"],
            "highWaterUsd": continuation["highWater"],
            "navUsd": live["navUsd"],
            "pnlSinceResetUsd": live["navUsd"] - initial_nav_usd,
            "returnSinceReset": live["navUsd"] / initial_nav_usd - 1,
            "totalExecutions": len(continuation["executions"]),
        },
        "warnings": [
            "V67 accelerator is zero because no fresh on-chain publication snapshot is configured.",
            *[
                f"{symbol}: excluded because its latest daily candle is stale."
                for symbol in engine["inactiveSymbols"]
            ],
            *[
                f"{failure['symbol']}: {failure['reason']}"
                for failure in failed_symbols
            ],
        ],
        "exchange_submission_available": False,
        "live_ready": False,
        "real_leverage_authorized": False,
    }


def run_once(path: Path, *, reset_date: str, initial_nav_usd: float) -> dict[str, Any]:
    histories, failed_symbols = load_asset_histories()
    snapshot = compute_forward_state(
        histories,
        failed_symbols,
        reset_date=reset_date,
        initial_nav_usd=initial_nav_usd,
    )
    _write_atomic(path, snapshot)
    return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run Atlas NX R1 reconstruction on real Binance candles in paper mode"
    )
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--reset-date", default=SNAPSHOT_DATE)
    parser.add_argument("--starting-cash", type=float, default=10_000.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if args.poll_seconds < 10:
        parser.error("poll-seconds must be at least 10")
    while True:
        started = time.monotonic()
        try:
            snapshot = run_once(
                args.snapshot,
                reset_date=args.reset_date,
                initial_nav_usd=args.starting_cash,
            )
            print(
                json.dumps(
                    {
                        "event": "atlas_nx_r1_paper_snapshot",
                        "status": snapshot["status"],
                        "as_of": snapshot["asOf"],
                        "assets": snapshot["dataAssetCount"],
                        "nav_usd": round(snapshot["paper"]["navUsd"], 4),
                        "target_gross": round(snapshot["targetGross"], 6),
                        "ratchet_stage": snapshot["ratchetStage"],
                    },
                    separators=(",", ":"),
                ),
                flush=True,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            print(
                json.dumps(
                    {
                        "event": "atlas_nx_r1_paper_error",
                        "error": f"{type(error).__name__}: {error}",
                    },
                    separators=(",", ":"),
                ),
                flush=True,
            )
        if args.once:
            return 0 if args.snapshot.is_file() else 1
        time.sleep(max(0.0, args.poll_seconds - (time.monotonic() - started)))


if __name__ == "__main__":
    raise SystemExit(main())
