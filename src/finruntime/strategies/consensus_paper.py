"""Paper-only port of trader's Consensus WIF + DOT strategy.

The implementation deliberately uses Binance public endpoints only. It persists a
small paper ledger and never exposes an exchange-order path or reads API keys.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import signal
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

FUTURES_API = "https://fapi.binance.com"
INITIAL_EQUITY_USDT = 10_000.0
ROUND_TURN_COST_RATE = 20.0 / 10_000.0
MAX_GROSS_LEVERAGE = 3.0
MAX_POSITIONS = 2
MAX_HISTORY_POINTS = 720
MAX_EVENTS = 30
FIFTEEN_MINUTES_MS = 15 * 60 * 1000
BASE_WIF_RISK_PERCENT = 3.0
BASE_DOT_RISK_PERCENT = 5.0
BOOST_WIF_RISK_PERCENT = 7.5
BOOST_DOT_RISK_PERCENT = 10.0
BOOST_TRIGGER_PROFIT_PERCENT = 15.0
DERISK_DRAWDOWN_PERCENT = 8.0
HARD_STOP_DRAWDOWN_PERCENT = 15.0
EPSILON = 1e-9


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso(timestamp_ms: int) -> str:
    return (
        datetime.fromtimestamp(timestamp_ms / 1000, UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _fetch_json(path: str, params: dict[str, str], timeout: float) -> Any:
    url = f"{FUTURES_API}{path}?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": "FIN-Strategy-Hub/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _numeric_klines(rows: list[list[Any]], now_ms: int) -> list[dict[str, float | int]]:
    result: list[dict[str, float | int]] = []
    for row in rows:
        if len(row) < 11 or int(row[6]) >= now_ms:
            continue
        values = {
            "timestamp_ms": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "quote_volume": float(row[7]),
            "taker_buy_quote": float(row[10]),
            "close_time_ms": int(row[6]),
        }
        if all(
            math.isfinite(float(values[key]))
            for key in (
                "open",
                "high",
                "low",
                "close",
                "quote_volume",
                "taker_buy_quote",
            )
        ):
            result.append(values)
    return sorted(result, key=lambda item: int(item["timestamp_ms"]))


def _standard_deviation(values: list[float]) -> float:
    return statistics.stdev(values) if len(values) >= 2 else 0.0


def _z_score(values: list[float]) -> float:
    if len(values) < 20:
        return math.nan
    latest = values[-1]
    sample = values[:-1]
    deviation = _standard_deviation(sample)
    return (latest - statistics.mean(sample)) / deviation if deviation > 0 else 0.0


def _atr14(rows: list[dict[str, float | int]]) -> float:
    sample = rows[-80:]
    if len(sample) < 15:
        return math.nan
    ranges: list[float] = []
    for previous, current in itertools.pairwise(sample):
        previous_close = float(previous["close"])
        high = float(current["high"])
        low = float(current["low"])
        ranges.append(
            max(high - low, abs(high - previous_close), abs(low - previous_close))
        )
    value = statistics.mean(ranges[:14])
    for current_range in ranges[14:]:
        value = (value * 13 + current_range) / 14
    return value


def _open_interest_history(timeout: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    end_time = _now_ms()
    for _batch in range(4):
        batch = _fetch_json(
            "/futures/data/openInterestHist",
            {
                "symbol": "WIFUSDT",
                "period": "5m",
                "limit": "500",
                "endTime": str(end_time),
            },
            timeout,
        )
        if not isinstance(batch, list) or not batch:
            break
        rows = [*batch, *rows]
        timestamps = [int(item["timestamp"]) for item in batch]
        end_time = min(timestamps) - 1
    deduplicated = {int(item["timestamp"]): item for item in rows}
    return [deduplicated[key] for key in sorted(deduplicated)]


def _oi_change_z(rows: list[dict[str, Any]]) -> float:
    points = [(int(item["timestamp"]), float(item["sumOpenInterest"])) for item in rows]
    points = [item for item in points if item[0] > 0 and item[1] > 0]
    changes = [
        points[index][1] / points[index - 9][1] - 1 for index in range(9, len(points))
    ]
    return _z_score(changes[-1000:])


def _price(symbol: str, timeout: float) -> float:
    row = _fetch_json("/fapi/v1/ticker/price", {"symbol": symbol}, timeout)
    price = float(row["price"])
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"invalid {symbol} price")
    return price


def scan_market(*, timeout: float = 8.0, now_ms: int | None = None) -> dict[str, Any]:
    observed_at_ms = now_ms or _now_ms()
    wif_rows = _fetch_json(
        "/fapi/v1/klines",
        {"symbol": "WIFUSDT", "interval": "15m", "limit": "700"},
        timeout,
    )
    premium_rows = _fetch_json(
        "/fapi/v1/premiumIndexKlines",
        {"symbol": "WIFUSDT", "interval": "15m", "limit": "700"},
        timeout,
    )
    oi_rows = _open_interest_history(timeout)
    funding_rows = _fetch_json(
        "/fapi/v1/fundingRate", {"symbol": "DOTUSDT", "limit": "1"}, timeout
    )
    dot_rows = _fetch_json(
        "/fapi/v1/klines",
        {"symbol": "DOTUSDT", "interval": "15m", "limit": "100"},
        timeout,
    )
    wif_price = _price("WIFUSDT", timeout)
    dot_price = _price("DOTUSDT", timeout)

    wif = _numeric_klines(wif_rows, observed_at_ms)
    premium = _numeric_klines(premium_rows, observed_at_ms)
    dot = _numeric_klines(dot_rows, observed_at_ms)
    wif_input: dict[str, Any] | None = None
    if len(wif) >= 673 and len(premium) >= 673:
        latest = wif[-1]
        move_start = wif[-4]
        atr = _atr14(wif)
        volume_z = _z_score(
            [math.log1p(max(0.0, float(row["quote_volume"]))) for row in wif[-673:]]
        )
        premium_z = _z_score([float(row["close"]) for row in premium[-673:]])
        oi_z = _oi_change_z(oi_rows)
        quote_volume = float(latest["quote_volume"])
        taker_imbalance = (
            2 * float(latest["taker_buy_quote"]) / quote_volume - 1
            if quote_volume > 0
            else 0.0
        )
        move_45m_atr = (
            (float(latest["close"]) - float(move_start["close"])) / atr
            if math.isfinite(atr) and atr > 0
            else math.nan
        )
        features = [atr, volume_z, premium_z, oi_z, move_45m_atr]
        if all(math.isfinite(value) for value in features):
            wif_input = {
                "signal_closed_at_ms": int(latest["close_time_ms"]),
                "entry_price": wif_price,
                "open": float(latest["open"]),
                "high": float(latest["high"]),
                "low": float(latest["low"]),
                "close": float(latest["close"]),
                "atr": atr,
                "move_45m_atr": move_45m_atr,
                "volume_z": volume_z,
                "taker_imbalance": taker_imbalance,
                "oi_z": oi_z,
                "premium_z": premium_z,
            }

    dot_input: dict[str, Any] | None = None
    if isinstance(funding_rows, list) and funding_rows and math.isfinite(_atr14(dot)):
        funding = funding_rows[-1]
        dot_input = {
            "funding_time_ms": int(funding["fundingTime"]),
            "evaluated_at_ms": observed_at_ms,
            "funding_rate_bps": float(funding["fundingRate"]) * 10_000,
            "entry_price": dot_price,
            "atr": _atr14(dot),
        }

    return {
        "observed_at_ms": observed_at_ms,
        "prices": {"WIFUSDT": wif_price, "DOTUSDT": dot_price},
        "wif": wif_input,
        "dot": dot_input,
        "candles": [
            {
                "exchange_id": "binance",
                "symbol": "WIFUSDT",
                "asset": "WIF",
                "timeframe": "15m",
                "items": wif[-120:],
            },
            {
                "exchange_id": "binance",
                "symbol": "DOTUSDT",
                "asset": "DOT",
                "timeframe": "15m",
                "items": dot[-100:],
            },
        ],
        "diagnostics": {
            "wif_klines": len(wif),
            "wif_premium_klines": len(premium),
            "wif_open_interest_points": len(oi_rows),
            "dot_funding_time_ms": (
                int(funding_rows[-1]["fundingTime"])
                if isinstance(funding_rows, list) and funding_rows
                else None
            ),
        },
    }


def evaluate_signals(market: dict[str, Any]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    wif = market.get("wif")
    if isinstance(wif, dict):
        weekday = datetime.fromtimestamp(
            int(wif["signal_closed_at_ms"]) / 1000, UTC
        ).weekday()
        candle_range = float(wif["high"]) - float(wif["low"])
        if candle_range > 0:
            lower_wick = min(float(wif["open"]), float(wif["close"])) - float(
                wif["low"]
            )
            lower_wick_ratio = lower_wick / candle_range
            close_location = (float(wif["close"]) - float(wif["low"])) / candle_range
            raw_strength = (
                abs(float(wif["move_45m_atr"]))
                + max(-float(wif["oi_z"]), 0) / 2
                + max(-float(wif["premium_z"]), 0) / 2
            )
            passes = (
                weekday in {1, 4, 6}
                and float(wif["move_45m_atr"]) <= -2
                and float(wif["volume_z"]) >= 1
                and lower_wick_ratio >= 0.5
                and close_location >= 0.6
                and float(wif["taker_imbalance"]) >= -0.10
                and float(wif["oi_z"]) <= -1
                and raw_strength >= 3.5
            )
            if passes:
                signals.append(
                    {
                        "key": f"wif_oi_flush:{wif['signal_closed_at_ms']}",
                        "module": "wif_oi_flush",
                        "symbol": "WIFUSDT",
                        "asset": "WIF",
                        "entry_price": float(wif["entry_price"]),
                        "atr": float(wif["atr"]),
                        "stop_atr": 1.25,
                        "target_r": 5.0,
                        "max_hold_minutes": 60,
                        "strength": min(100, round(raw_strength / 8 * 100)),
                        "reason": (
                            f"45m {float(wif['move_45m_atr']):.2f} ATR; "
                            f"OI z {float(wif['oi_z']):.2f}; "
                            f"volume z {float(wif['volume_z']):.2f}"
                        ),
                    }
                )

    dot = market.get("dot")
    if isinstance(dot, dict):
        funding_time_ms = int(dot["funding_time_ms"])
        weekday = datetime.fromtimestamp(funding_time_ms / 1000, UTC).weekday()
        threshold = {0: -2.25, 1: -2.25, 4: -2.5, 5: -2.5, 6: -2.5}.get(weekday)
        delay_minutes = (int(dot["evaluated_at_ms"]) - funding_time_ms) / 60_000
        if (
            threshold is not None
            and 15 <= delay_minutes <= 30
            and float(dot["funding_rate_bps"]) <= threshold
        ):
            raw_strength = abs(float(dot["funding_rate_bps"]))
            signals.append(
                {
                    "key": f"dot_negative_funding:{funding_time_ms}",
                    "module": "dot_negative_funding",
                    "symbol": "DOTUSDT",
                    "asset": "DOT",
                    "entry_price": float(dot["entry_price"]),
                    "atr": float(dot["atr"]),
                    "stop_atr": 6.0,
                    "target_r": 2.0,
                    "max_hold_minutes": 480,
                    "strength": min(100, round(raw_strength * 2 / 8 * 100)),
                    "reason": (
                        f"funding {float(dot['funding_rate_bps']):.2f} bps; "
                        f"threshold {threshold:.2f} bps; delay {delay_minutes:.0f}m"
                    ),
                }
            )
    return signals


def create_initial_risk_state(
    initial_equity: float = INITIAL_EQUITY_USDT,
) -> dict[str, float | str]:
    if not math.isfinite(initial_equity) or initial_equity <= 0:
        raise ValueError("initial equity must be positive and finite")
    return {
        "mode": "base",
        "initial_equity_usdt": initial_equity,
        "equity_usdt": initial_equity,
        "high_water_equity_usdt": initial_equity,
        "last_derisk_high_water_equity_usdt": initial_equity,
    }


def transition_risk_state(
    state: dict[str, Any], equity: float
) -> dict[str, float | str]:
    initial = float(state.get("initial_equity_usdt", INITIAL_EQUITY_USDT))
    if not math.isfinite(initial) or initial <= 0:
        raise ValueError("risk state initial equity must be positive and finite")
    if not math.isfinite(equity) or equity <= 0:
        raise ValueError("paper equity must be positive and finite")

    mode = str(state.get("mode", "base"))
    if mode not in {"base", "boost", "stopped"}:
        raise ValueError(f"unsupported risk mode: {mode}")
    prior_high_water = float(state.get("high_water_equity_usdt", initial))
    last_derisk = float(state.get("last_derisk_high_water_equity_usdt", initial))
    high_water = max(prior_high_water, equity)
    drawdown_percent = (1 - equity / high_water) * 100
    profit_percent = (equity / initial - 1) * 100

    if mode == "stopped" or drawdown_percent + EPSILON >= HARD_STOP_DRAWDOWN_PERCENT:
        mode = "stopped"
    elif mode == "boost" and (drawdown_percent + EPSILON >= DERISK_DRAWDOWN_PERCENT):
        mode = "base"
        last_derisk = high_water
    else:
        at_high_water = abs(equity - high_water) <= EPSILON
        recovered_after_derisk = equity + EPSILON >= last_derisk
        if (
            mode == "base"
            and profit_percent + EPSILON >= BOOST_TRIGGER_PROFIT_PERCENT
            and at_high_water
            and recovered_after_derisk
        ):
            mode = "boost"

    return {
        "mode": mode,
        "initial_equity_usdt": initial,
        "equity_usdt": equity,
        "high_water_equity_usdt": high_water,
        "last_derisk_high_water_equity_usdt": last_derisk,
    }


def _risk_percent(module: str, mode: str) -> float:
    if mode == "stopped":
        return 0.0
    if module == "wif_oi_flush":
        return BOOST_WIF_RISK_PERCENT if mode == "boost" else BASE_WIF_RISK_PERCENT
    return BOOST_DOT_RISK_PERCENT if mode == "boost" else BASE_DOT_RISK_PERCENT


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "strategy_id": "consensus-wif-dot-v1",
        "name": "Consensus WIF + DOT",
        "mode": "paper",
        "health": "starting",
        "updated_at_ms": None,
        "market_data_at_ms": None,
        "paper": {
            "starting_balance_usdt": INITIAL_EQUITY_USDT,
            "realized_pnl_usdt": 0.0,
            "unrealized_pnl_usdt": 0.0,
            "equity_usdt": INITIAL_EQUITY_USDT,
            "closed_positions": 0,
            "positions": [],
            "equity_history": [],
        },
        "risk_state": create_initial_risk_state(),
        "signals": [],
        "seen_signal_keys": [],
        "candles": [],
        "diagnostics": {},
        "events": [],
        "errors": [],
        "exchange_submission_available": False,
    }


def _load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return _empty_state()
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("unsupported consensus paper snapshot")
    if not isinstance(value.get("risk_state"), dict):
        paper = value.get("paper")
        paper = paper if isinstance(paper, dict) else {}
        equity = float(paper.get("equity_usdt", INITIAL_EQUITY_USDT))
        value["risk_state"] = {
            **create_initial_risk_state(),
            "equity_usdt": equity,
            "high_water_equity_usdt": max(INITIAL_EQUITY_USDT, equity),
        }
    return value


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def _event(state: dict[str, Any], kind: str, payload: dict[str, Any]) -> None:
    events = list(state.get("events") or [])
    events.insert(0, {"at_ms": _now_ms(), "kind": kind, **payload})
    state["events"] = events[:MAX_EVENTS]


def update_paper_state(state: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    paper = dict(state.get("paper") or {})
    positions = [dict(item) for item in paper.get("positions") or []]
    realized = float(paper.get("realized_pnl_usdt", 0.0))
    closed_positions = int(paper.get("closed_positions", 0))
    now_ms = int(market["observed_at_ms"])
    prices = market["prices"]
    remaining: list[dict[str, Any]] = []
    for position in positions:
        mark = float(prices[position["symbol"]])
        gross_pnl = float(position["quantity"]) * (
            mark - float(position["entry_price"])
        )
        cost = float(position["notional_usdt"]) * ROUND_TURN_COST_RATE
        exit_reason = None
        if mark <= float(position["stop_price"]):
            exit_reason = "stop_loss"
        elif mark >= float(position["take_profit_price"]):
            exit_reason = "take_profit"
        elif now_ms >= int(position["exit_at_ms"]):
            exit_reason = "max_hold"
        if exit_reason:
            net_pnl = gross_pnl - cost
            realized += net_pnl
            closed_positions += 1
            _event(
                state,
                "paper_closed",
                {
                    "symbol": position["symbol"],
                    "reason": exit_reason,
                    "net_pnl_usdt": net_pnl,
                },
            )
        else:
            position.update(
                {
                    "mark_price": mark,
                    "unrealized_pnl_usdt": gross_pnl - cost,
                    "updated_at_ms": now_ms,
                }
            )
            remaining.append(position)
    positions = remaining

    unrealized = sum(float(item["unrealized_pnl_usdt"]) for item in positions)
    equity = INITIAL_EQUITY_USDT + realized + unrealized
    gross = sum(float(item["notional_usdt"]) for item in positions)
    seen = {str(item) for item in state.get("seen_signal_keys") or []}
    prior_risk_state = state.get("risk_state")
    prior_risk_state = (
        prior_risk_state
        if isinstance(prior_risk_state, dict)
        else create_initial_risk_state()
    )
    risk_state = transition_risk_state(prior_risk_state, equity)
    if risk_state["mode"] != prior_risk_state.get("mode"):
        _event(
            state,
            "risk_mode_changed",
            {"from": prior_risk_state.get("mode"), "to": risk_state["mode"]},
        )
    signals = evaluate_signals(market) if risk_state["mode"] != "stopped" else []
    for candidate in signals:
        if (
            candidate["key"] in seen
            or len(positions) >= MAX_POSITIONS
            or any(item["symbol"] == candidate["symbol"] for item in positions)
        ):
            continue
        stop_distance = float(candidate["atr"]) * float(candidate["stop_atr"])
        entry = float(candidate["entry_price"])
        risk_distance = stop_distance / entry + ROUND_TURN_COST_RATE
        risk_percent = _risk_percent(str(candidate["module"]), str(risk_state["mode"]))
        requested_notional = equity * risk_percent / 100 / risk_distance
        remaining_gross = max(0.0, equity * MAX_GROSS_LEVERAGE - gross)
        notional = min(requested_notional, remaining_gross)
        if notional <= 0:
            continue
        quantity = notional / entry
        position = {
            "position_id": f"paper-{candidate['key']}",
            "module": candidate["module"],
            "symbol": candidate["symbol"],
            "asset": candidate["asset"],
            "side": "long",
            "entry_price": entry,
            "mark_price": entry,
            "quantity": quantity,
            "notional_usdt": notional,
            "risk_mode": risk_state["mode"],
            "risk_percent": risk_percent,
            "stop_price": entry - stop_distance,
            "take_profit_price": entry + stop_distance * float(candidate["target_r"]),
            "opened_at_ms": now_ms,
            "updated_at_ms": now_ms,
            "exit_at_ms": now_ms + int(candidate["max_hold_minutes"]) * 60_000,
            "unrealized_pnl_usdt": -notional * ROUND_TURN_COST_RATE,
            "reason": candidate["reason"],
        }
        positions.append(position)
        gross += notional
        seen.add(str(candidate["key"]))
        _event(
            state,
            "paper_opened",
            {"symbol": candidate["symbol"], "notional_usdt": notional},
        )

    unrealized = sum(float(item["unrealized_pnl_usdt"]) for item in positions)
    equity = INITIAL_EQUITY_USDT + realized + unrealized
    history = list(paper.get("equity_history") or [])
    history.append(
        {
            "timestamp_ms": now_ms,
            "equity_usdt": equity,
            "realized_pnl_usdt": realized,
            "unrealized_pnl_usdt": unrealized,
        }
    )
    state.update(
        {
            "health": "healthy",
            "updated_at_ms": now_ms,
            "market_data_at_ms": now_ms,
            "paper": {
                "starting_balance_usdt": INITIAL_EQUITY_USDT,
                "realized_pnl_usdt": realized,
                "unrealized_pnl_usdt": unrealized,
                "equity_usdt": equity,
                "closed_positions": closed_positions,
                "positions": positions,
                "equity_history": history[-MAX_HISTORY_POINTS:],
            },
            "risk_state": risk_state,
            "signals": signals,
            "seen_signal_keys": sorted(seen)[-500:],
            "candles": market["candles"],
            "diagnostics": market["diagnostics"],
            "errors": [],
            "exchange_submission_available": False,
        }
    )
    return state


def run_once(path: Path, *, timeout: float = 8.0) -> dict[str, Any]:
    state = _load_state(path)
    try:
        state = update_paper_state(state, scan_market(timeout=timeout))
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        statistics.StatisticsError,
    ) as error:
        state["health"] = "degraded"
        state["errors"] = [f"{type(error).__name__}: {error}"]
        _event(state, "market_scan_failed", {"error": state["errors"][0]})
    _write_state(path, state)
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run WIF/DOT public-market paper trading"
    )
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--timeout-seconds", type=float, default=8.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if args.poll_seconds < 15:
        parser.error("--poll-seconds must be at least 15")

    path = Path(args.snapshot).expanduser().resolve()
    stopping = False

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        nonlocal stopping
        stopping = True

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, request_stop)
    while not stopping:
        state = run_once(path, timeout=args.timeout_seconds)
        print(
            json.dumps(
                {
                    "strategy": state["strategy_id"],
                    "health": state["health"],
                    "updated_at_ms": state.get("updated_at_ms"),
                    "positions": len(state["paper"]["positions"]),
                },
                separators=(",", ":"),
            ),
            flush=True,
        )
        if args.once:
            break
        deadline = time.monotonic() + args.poll_seconds
        while not stopping and time.monotonic() < deadline:
            time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
