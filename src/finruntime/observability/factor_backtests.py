"""Causal public-market replays for factor-driven paper strategies."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import statistics
import time
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from finruntime.strategies import consensus_paper

INITIAL_NAV_USD = 10_000.0
BINANCE_ARCHIVE_ROOT = "https://data.binance.vision/data/futures/um"
BINANCE_FUTURES_API = "https://fapi.binance.com"
BYBIT_API = "https://api.bybit.com"
FIFTEEN_MINUTES_MS = 15 * 60 * 1000
TROPICAL_YEAR_DAYS = 365.2425
MAX_DOWNLOAD_WORKERS = 16
ONE_HOUR_MS = 60 * 60 * 1000
FUNDING_MAX_HOLD_HOURS = 72
FUNDING_EXIT_SPREAD_BPS_8H = 1.0


@dataclass(frozen=True, slots=True)
class ArchiveSpec:
    group: str
    url: str


@dataclass(slots=True)
class DownloadAudit:
    request_count: int = 0
    byte_count: int = 0
    payload_sha256: str = ""


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _next_month(value: date) -> date:
    return (value.replace(day=28) + timedelta(days=4)).replace(day=1)


def _month_end(value: date) -> date:
    return _next_month(value) - timedelta(days=1)


def _archive_name(kind: str, symbol: str, interval: str | None, token: str) -> str:
    if kind == "fundingRate":
        return f"{symbol}-fundingRate-{token}"
    if kind == "metrics":
        return f"{symbol}-metrics-{token}"
    if interval is None:
        raise ValueError(f"archive interval is required for {kind}")
    return f"{symbol}-{interval}-{token}"


def _archive_specs(
    group: str,
    kind: str,
    symbol: str,
    interval: str | None,
    start: date,
    end: date,
) -> list[ArchiveSpec]:
    specs: list[ArchiveSpec] = []
    cursor = _month_start(start)
    current_month = _month_start(datetime.now(UTC).date())
    while cursor <= end:
        covered_start = max(start, cursor)
        covered_end = min(end, _month_end(cursor))
        if cursor >= current_month and kind != "metrics":
            cursor = _next_month(cursor)
            continue
        use_daily = kind == "metrics"
        if use_daily:
            observed = covered_start
            while observed <= covered_end:
                token = observed.isoformat()
                name = _archive_name(kind, symbol, interval, token)
                if kind in {"metrics", "fundingRate"}:
                    path = f"daily/{kind}/{symbol}/{name}.zip"
                else:
                    path = f"daily/{kind}/{symbol}/{interval}/{name}.zip"
                specs.append(ArchiveSpec(group, f"{BINANCE_ARCHIVE_ROOT}/{path}"))
                observed += timedelta(days=1)
        else:
            token = cursor.strftime("%Y-%m")
            name = _archive_name(kind, symbol, interval, token)
            if kind == "fundingRate":
                path = f"monthly/{kind}/{symbol}/{name}.zip"
            else:
                path = f"monthly/{kind}/{symbol}/{interval}/{name}.zip"
            specs.append(ArchiveSpec(group, f"{BINANCE_ARCHIVE_ROOT}/{path}"))
        cursor = _next_month(cursor)
    return specs


def _fetch_bytes(url: str, timeout_seconds: float = 30.0) -> bytes:
    request = Request(
        url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "finruntime-backtest/1.0",
        },
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return response.read()
        except (HTTPError, OSError) as error:
            last_error = error
            if isinstance(error, HTTPError) and error.code == 404:
                raise
            if attempt < 2:
                time.sleep(0.25 * (attempt + 1))
    raise RuntimeError(f"market archive request failed: {url}: {last_error}")


def _download_archives(
    specs: list[ArchiveSpec], *, allow_missing: bool = False
) -> tuple[dict[str, list[dict[str, str]]], DownloadAudit]:
    if not specs:
        return {}, DownloadAudit(payload_sha256=hashlib.sha256(b"").hexdigest())
    rows_by_group: dict[str, list[dict[str, str]]] = defaultdict(list)
    payloads: list[tuple[str, bytes]] = []
    missing: list[str] = []
    with ThreadPoolExecutor(
        max_workers=min(MAX_DOWNLOAD_WORKERS, len(specs))
    ) as executor:
        futures = {executor.submit(_fetch_bytes, spec.url): spec for spec in specs}
        for future in as_completed(futures):
            spec = futures[future]
            try:
                payloads.append((spec.url, future.result()))
            except HTTPError as error:
                if error.code == 404 and allow_missing:
                    missing.append(spec.url)
                    continue
                raise RuntimeError(
                    f"required market archive is missing: {spec.url}"
                ) from error
    digest = hashlib.sha256()
    total_bytes = 0
    for url, payload in sorted(payloads):
        digest.update(url.encode("utf-8"))
        digest.update(hashlib.sha256(payload).digest())
        total_bytes += len(payload)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
            if len(names) != 1:
                raise ValueError(f"unexpected archive contents: {url}")
            text = archive.read(names[0]).decode("utf-8")
        group = next(spec.group for spec in specs if spec.url == url)
        rows_by_group[group].extend(csv.DictReader(io.StringIO(text)))
    audit = DownloadAudit(
        request_count=len(payloads) + len(missing),
        byte_count=total_bytes,
        payload_sha256=digest.hexdigest(),
    )
    return dict(rows_by_group), audit


def _kline_rows(rows: list[dict[str, str]]) -> list[dict[str, float | int]]:
    unique: dict[int, dict[str, float | int]] = {}
    for row in rows:
        timestamp = int(row["open_time"])
        unique[timestamp] = {
            "timestamp_ms": timestamp,
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "quote_volume": float(row["quote_volume"]),
            "taker_buy_quote": float(row["taker_buy_quote_volume"]),
            "close_time_ms": int(row["close_time"]),
        }
    return [unique[key] for key in sorted(unique)]


def _z_score(values: list[float]) -> float:
    if len(values) < 20 or not all(math.isfinite(value) for value in values):
        return math.nan
    sample = values[:-1]
    deviation = statistics.stdev(sample)
    return (values[-1] - statistics.fmean(sample)) / deviation if deviation > 0 else 0.0


def _atr14(rows: list[dict[str, float | int]], index: int) -> float:
    sample = rows[max(0, index - 79) : index + 1]
    if len(sample) < 15:
        return math.nan
    ranges: list[float] = []
    for previous, current in pairwise(sample):
        previous_close = float(previous["close"])
        high = float(current["high"])
        low = float(current["low"])
        ranges.append(
            max(high - low, abs(high - previous_close), abs(low - previous_close))
        )
    value = statistics.fmean(ranges[:14])
    for current_range in ranges[14:]:
        value = (value * 13 + current_range) / 14
    return value


def _preliminary_wif_signals(
    rows: list[dict[str, float | int]], premium_rows: list[dict[str, float | int]]
) -> list[dict[str, Any]]:
    premium = {int(row["timestamp_ms"]): float(row["close"]) for row in premium_rows}
    candidates: list[dict[str, Any]] = []
    for index in range(672, len(rows)):
        current = rows[index]
        observed = datetime.fromtimestamp(int(current["close_time_ms"]) / 1000, UTC)
        if observed.weekday() not in {1, 4, 6}:
            continue
        candle_range = float(current["high"]) - float(current["low"])
        if candle_range <= 0:
            continue
        lower_wick_ratio = (
            min(float(current["open"]), float(current["close"])) - float(current["low"])
        ) / candle_range
        close_location = (
            float(current["close"]) - float(current["low"])
        ) / candle_range
        quote_volume = float(current["quote_volume"])
        taker_imbalance = (
            2 * float(current["taker_buy_quote"]) / quote_volume - 1
            if quote_volume > 0
            else 0.0
        )
        if lower_wick_ratio < 0.5 or close_location < 0.6 or taker_imbalance < -0.10:
            continue
        atr = _atr14(rows, index)
        move = (
            (float(current["close"]) - float(rows[index - 3]["close"])) / atr
            if math.isfinite(atr) and atr > 0
            else math.nan
        )
        if not math.isfinite(move) or move > -2:
            continue
        sample = rows[index - 672 : index + 1]
        volume_z = _z_score(
            [math.log1p(max(0.0, float(item["quote_volume"]))) for item in sample]
        )
        premium_z = _z_score(
            [premium.get(int(item["timestamp_ms"]), math.nan) for item in sample]
        )
        if not math.isfinite(volume_z) or volume_z < 1 or not math.isfinite(premium_z):
            continue
        entry_index = index + 1
        if entry_index >= len(rows):
            continue
        candidates.append(
            {
                "module": "wif_oi_flush",
                "symbol": "WIFUSDT",
                "asset": "WIF",
                "signal_time_ms": int(current["close_time_ms"]),
                "entry_time_ms": int(rows[entry_index]["timestamp_ms"]),
                "entry_price": float(rows[entry_index]["open"]),
                "atr": atr,
                "move_45m_atr": move,
                "volume_z": volume_z,
                "premium_z": premium_z,
                "taker_imbalance": taker_imbalance,
                "lower_wick_ratio": lower_wick_ratio,
                "close_location": close_location,
                "stop_atr": 1.25,
                "target_r": 5.0,
                "max_hold_minutes": 60,
            }
        )
    return candidates


def _metrics_points(rows: list[dict[str, str]]) -> list[tuple[int, float]]:
    unique: dict[int, float] = {}
    for row in rows:
        observed = datetime.fromisoformat(row["create_time"]).replace(tzinfo=UTC)
        value = float(row["sum_open_interest"])
        if value > 0 and math.isfinite(value):
            unique[int(observed.timestamp() * 1000)] = value
    return [(key, unique[key]) for key in sorted(unique)]


def _oi_change_z(points: list[tuple[int, float]], cutoff_ms: int) -> float:
    available = [point for point in points if point[0] <= cutoff_ms]
    changes = [
        available[index][1] / available[index - 9][1] - 1
        for index in range(9, len(available))
    ]
    return _z_score(changes[-1000:])


def _dot_signals(
    funding_rows: list[dict[str, str]], dot_rows: list[dict[str, float | int]]
) -> list[dict[str, Any]]:
    row_indexes = {
        int(row["timestamp_ms"]): index for index, row in enumerate(dot_rows)
    }
    signals: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in funding_rows:
        funding_time_ms = int(row["calc_time"])
        if funding_time_ms in seen:
            continue
        seen.add(funding_time_ms)
        weekday = datetime.fromtimestamp(funding_time_ms / 1000, UTC).weekday()
        threshold = {0: -2.25, 1: -2.25, 4: -2.5, 5: -2.5, 6: -2.5}.get(weekday)
        funding_bps = float(row["last_funding_rate"]) * 10_000
        signal_index = row_indexes.get(funding_time_ms)
        entry_index = row_indexes.get(funding_time_ms + FIFTEEN_MINUTES_MS)
        if (
            threshold is None
            or funding_bps > threshold
            or signal_index is None
            or entry_index is None
        ):
            continue
        atr = _atr14(dot_rows, signal_index)
        if not math.isfinite(atr) or atr <= 0:
            continue
        entry = dot_rows[entry_index]
        signals.append(
            {
                "module": "dot_negative_funding",
                "symbol": "DOTUSDT",
                "asset": "DOT",
                "signal_time_ms": funding_time_ms,
                "entry_time_ms": int(entry["timestamp_ms"]),
                "entry_price": float(entry["open"]),
                "atr": atr,
                "funding_rate_bps": funding_bps,
                "threshold_bps": threshold,
                "stop_atr": 6.0,
                "target_r": 2.0,
                "max_hold_minutes": 480,
            }
        )
    return signals


def _portfolio_metrics(
    daily: list[dict[str, Any]], start: date, end: date, scope: str, label: str
) -> dict[str, Any]:
    nav_by_date = {
        date.fromisoformat(row["date"]): float(row["navUsd"]) for row in daily
    }
    returns: list[float] = []
    previous = INITIAL_NAV_USD
    observed = start
    normalized: list[dict[str, Any]] = []
    last_nav = INITIAL_NAV_USD
    while observed <= end:
        nav = nav_by_date.get(observed, last_nav)
        returns.append(nav / previous - 1)
        normalized.append({"date": observed.isoformat(), "navUsd": nav})
        previous = nav
        last_nav = nav
        observed += timedelta(days=1)
    years = max((end - start).days / TROPICAL_YEAR_DAYS, 1 / TROPICAL_YEAR_DAYS)
    multiple = last_nav / INITIAL_NAV_USD
    deviation = statistics.stdev(returns) if len(returns) > 1 else 0.0
    downside = [min(value, 0.0) for value in returns]
    downside_deviation = statistics.stdev(downside) if len(downside) > 1 else 0.0
    high_water = INITIAL_NAV_USD
    maximum_drawdown = 0.0
    for row in normalized:
        high_water = max(high_water, row["navUsd"])
        maximum_drawdown = min(maximum_drawdown, row["navUsd"] / high_water - 1)
    return {
        "scope": scope,
        "scope_label": label,
        "cagr_percent": (multiple ** (1 / years) - 1) * 100,
        "total_return_percent": (multiple - 1) * 100,
        "sharpe": statistics.fmean(returns) / deviation * math.sqrt(365)
        if deviation > 0
        else None,
        "sortino": (
            statistics.fmean(returns) / downside_deviation * math.sqrt(365)
            if downside_deviation > 0
            else None
        ),
        "max_drawdown_percent": maximum_drawdown * 100,
        "years": years,
        "starting_nav_usd": INITIAL_NAV_USD,
        "ending_nav_usd": last_nav,
        "daily_observations": len(normalized),
    }


def _mark_equity(
    realized: float,
    positions: list[dict[str, Any]],
    bars: dict[str, dict[str, float | int]],
) -> float:
    unrealized = 0.0
    for position in positions:
        bar = bars.get(str(position["symbol"]))
        mark = float(bar["open"]) if bar else float(position["last_price"])
        position["last_price"] = mark
        unrealized += float(position["quantity"]) * (
            mark - float(position["entry_price"])
        )
        unrealized -= float(position["cost_usd"])
    return INITIAL_NAV_USD + realized + unrealized


def _simulate_consensus(
    signals: list[dict[str, Any]],
    wif_rows: list[dict[str, float | int]],
    dot_rows: list[dict[str, float | int]],
    start: date,
    end: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    signals_by_time: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for signal in signals:
        signals_by_time[int(signal["entry_time_ms"])].append(signal)
    rows_by_symbol = {
        "WIFUSDT": {int(row["timestamp_ms"]): row for row in wif_rows},
        "DOTUSDT": {int(row["timestamp_ms"]): row for row in dot_rows},
    }
    timestamps = sorted(set(rows_by_symbol["WIFUSDT"]) | set(rows_by_symbol["DOTUSDT"]))
    start_ms = int(datetime.combine(start, datetime.min.time(), UTC).timestamp() * 1000)
    end_ms = int(
        datetime.combine(end + timedelta(days=1), datetime.min.time(), UTC).timestamp()
        * 1000
    )
    positions: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    daily: dict[str, float] = {}
    realized = 0.0
    risk_state: dict[str, Any] = consensus_paper.create_initial_risk_state()
    last_bars: dict[str, dict[str, float | int]] = {}

    for timestamp in timestamps:
        if timestamp < start_ms or timestamp >= end_ms:
            continue
        bars = {
            symbol: rows[timestamp]
            for symbol, rows in rows_by_symbol.items()
            if timestamp in rows
        }
        last_bars.update(bars)
        opening_equity = _mark_equity(realized, positions, bars)
        risk_state = consensus_paper.transition_risk_state(risk_state, opening_equity)
        gross = sum(float(position["notional_usdt"]) for position in positions)
        for signal in signals_by_time.get(timestamp, []):
            if (
                risk_state["mode"] == "stopped"
                or len(positions) >= consensus_paper.MAX_POSITIONS
            ):
                continue
            if any(position["symbol"] == signal["symbol"] for position in positions):
                continue
            entry = float(signal["entry_price"])
            stop_distance = float(signal["atr"]) * float(signal["stop_atr"])
            risk_distance = stop_distance / entry + consensus_paper.ROUND_TURN_COST_RATE
            if signal["module"] == "wif_oi_flush":
                risk_percent = (
                    consensus_paper.BOOST_WIF_RISK_PERCENT
                    if risk_state["mode"] == "boost"
                    else consensus_paper.BASE_WIF_RISK_PERCENT
                )
            else:
                risk_percent = (
                    consensus_paper.BOOST_DOT_RISK_PERCENT
                    if risk_state["mode"] == "boost"
                    else consensus_paper.BASE_DOT_RISK_PERCENT
                )
            requested = opening_equity * risk_percent / 100 / risk_distance
            available = max(
                0.0, opening_equity * consensus_paper.MAX_GROSS_LEVERAGE - gross
            )
            notional = min(requested, available)
            if notional <= 0:
                continue
            cost = notional * consensus_paper.ROUND_TURN_COST_RATE
            position = {
                **signal,
                "position_id": f"{signal['module']}:{timestamp}",
                "quantity": notional / entry,
                "notional_usdt": notional,
                "cost_usd": cost,
                "stop_price": entry - stop_distance,
                "target_price": entry + stop_distance * float(signal["target_r"]),
                "exit_at_ms": timestamp + int(signal["max_hold_minutes"]) * 60_000,
                "risk_mode": risk_state["mode"],
                "last_price": entry,
            }
            positions.append(position)
            gross += notional

        remaining: list[dict[str, Any]] = []
        for position in positions:
            bar = bars.get(str(position["symbol"]))
            if bar is None:
                remaining.append(position)
                continue
            entry = float(position["entry_price"])
            stop = float(position["stop_price"])
            target = float(position["target_price"])
            exit_price: float | None = None
            reason: str | None = None
            if timestamp >= int(position["exit_at_ms"]):
                exit_price = float(bar["open"])
                reason = "max_hold"
            elif float(bar["low"]) <= stop:
                exit_price = min(stop, float(bar["open"]))
                reason = "stop_loss"
            elif float(bar["high"]) >= target:
                exit_price = max(target, float(bar["open"]))
                reason = "take_profit"
            if exit_price is None:
                position["last_price"] = float(bar["close"])
                remaining.append(position)
                continue
            pnl = float(position["quantity"]) * (exit_price - entry) - float(
                position["cost_usd"]
            )
            realized += pnl
            entry_date = datetime.fromtimestamp(
                int(position["entry_time_ms"]) / 1000, UTC
            ).date()
            exit_date = datetime.fromtimestamp(timestamp / 1000, UTC).date()
            trades.append(
                {
                    "id": str(position["position_id"]),
                    "asset": str(position["asset"]),
                    "direction": "LONG",
                    "status": "closed",
                    "entry_date": entry_date.isoformat(),
                    "exit_date": exit_date.isoformat(),
                    "held_through": exit_date.isoformat(),
                    "holding_days": (exit_date - entry_date).days,
                    "entry_price": entry,
                    "exit_price": exit_price,
                    "asset_return_percent": (exit_price / entry - 1) * 100,
                    "net_pnl_usd": pnl,
                    "order_count": 2,
                    "exit_reason": reason,
                    "module": position["module"],
                    "risk_mode": position["risk_mode"],
                }
            )
        positions = remaining
        close_bars = {
            symbol: {**bar, "open": bar["close"]} for symbol, bar in last_bars.items()
        }
        equity = _mark_equity(realized, positions, close_bars)
        risk_state = consensus_paper.transition_risk_state(risk_state, equity)
        observed_date = datetime.fromtimestamp(timestamp / 1000, UTC).date().isoformat()
        daily[observed_date] = equity

    for position in positions:
        bar = last_bars[str(position["symbol"])]
        exit_price = float(bar["close"])
        pnl = float(position["quantity"]) * (
            exit_price - float(position["entry_price"])
        )
        pnl -= float(position["cost_usd"])
        realized += pnl
        entry_date = datetime.fromtimestamp(
            int(position["entry_time_ms"]) / 1000, UTC
        ).date()
        trades.append(
            {
                "id": str(position["position_id"]),
                "asset": str(position["asset"]),
                "direction": "LONG",
                "status": "closed",
                "entry_date": entry_date.isoformat(),
                "exit_date": end.isoformat(),
                "held_through": end.isoformat(),
                "holding_days": (end - entry_date).days,
                "entry_price": float(position["entry_price"]),
                "exit_price": exit_price,
                "asset_return_percent": (
                    exit_price / float(position["entry_price"]) - 1
                )
                * 100,
                "net_pnl_usd": pnl,
                "order_count": 2,
                "exit_reason": "window_end",
                "module": position["module"],
                "risk_mode": position["risk_mode"],
            }
        )
    daily[end.isoformat()] = INITIAL_NAV_USD + realized
    return (
        [{"date": key, "navUsd": daily[key]} for key in sorted(daily)],
        sorted(
            trades, key=lambda item: (item["exit_date"], item["asset"]), reverse=True
        ),
    )


def run_consensus_backtest(start: date, end: date) -> dict[str, Any]:
    """Replay current WIF/DOT rules from Binance's official public archives."""

    warmup_start = start - timedelta(days=8)
    specs: list[ArchiveSpec] = []
    specs.extend(_archive_specs("wif", "klines", "WIFUSDT", "15m", warmup_start, end))
    specs.extend(
        _archive_specs(
            "premium", "premiumIndexKlines", "WIFUSDT", "15m", warmup_start, end
        )
    )
    specs.extend(_archive_specs("dot", "klines", "DOTUSDT", "15m", warmup_start, end))
    specs.extend(
        _archive_specs("dot_funding", "fundingRate", "DOTUSDT", None, start, end)
    )
    archive_rows, archive_audit = _download_archives(specs)
    api_audits: list[DownloadAudit] = []
    current_start = max(warmup_start, _month_start(datetime.now(UTC).date()))
    if current_start <= end:
        for group, path, symbol in (
            ("wif", "/fapi/v1/klines", "WIFUSDT"),
            ("premium", "/fapi/v1/premiumIndexKlines", "WIFUSDT"),
            ("dot", "/fapi/v1/klines", "DOTUSDT"),
        ):
            api_rows, api_audit = _binance_api_klines(path, symbol, current_start, end)
            archive_rows.setdefault(group, []).extend(api_rows)
            api_audits.append(api_audit)
        funding_rows, funding_audit = _binance_api_funding(
            "DOTUSDT", current_start, end
        )
        archive_rows.setdefault("dot_funding", []).extend(funding_rows)
        api_audits.append(funding_audit)
    wif_rows = _kline_rows(archive_rows.get("wif", []))
    premium_rows = _kline_rows(archive_rows.get("premium", []))
    dot_rows = _kline_rows(archive_rows.get("dot", []))
    preliminary = _preliminary_wif_signals(wif_rows, premium_rows)

    metric_dates: set[date] = set()
    for signal in preliminary:
        signal_date = datetime.fromtimestamp(
            int(signal["signal_time_ms"]) / 1000, UTC
        ).date()
        for offset in range(6):
            metric_dates.add(signal_date - timedelta(days=offset))
    metric_specs = [
        _archive_specs("wif_metrics", "metrics", "WIFUSDT", None, value, value)[0]
        for value in sorted(metric_dates)
    ]
    metric_rows, metric_audit = _download_archives(metric_specs, allow_missing=True)
    archived_oi_points = _metrics_points(metric_rows.get("wif_metrics", []))
    recent_oi_points, recent_oi_audit = _recent_open_interest_points(end)
    oi_by_timestamp = {timestamp: value for timestamp, value in archived_oi_points}
    oi_by_timestamp.update(dict(recent_oi_points))
    oi_points = [(key, oi_by_timestamp[key]) for key in sorted(oi_by_timestamp)]
    wif_signals: list[dict[str, Any]] = []
    for signal in preliminary:
        oi_z = _oi_change_z(oi_points, int(signal["signal_time_ms"]))
        strength = (
            abs(float(signal["move_45m_atr"]))
            + max(-oi_z, 0.0) / 2
            + max(-float(signal["premium_z"]), 0.0) / 2
        )
        if math.isfinite(oi_z) and oi_z <= -1 and strength >= 3.5:
            wif_signals.append({**signal, "oi_z": oi_z, "strength": strength})
    dot_signals = _dot_signals(archive_rows.get("dot_funding", []), dot_rows)
    signals = sorted(
        [*wif_signals, *dot_signals], key=lambda item: int(item["entry_time_ms"])
    )
    daily, trades = _simulate_consensus(signals, wif_rows, dot_rows, start, end)
    metrics = _portfolio_metrics(
        daily,
        start,
        end,
        "on_demand_two_year_factor_replay",
        "Свежий causal replay WIF + DOT за 2 года",
    )
    audit_parts = [
        archive_audit.payload_sha256,
        metric_audit.payload_sha256,
        recent_oi_audit.payload_sha256,
        *(audit.payload_sha256 for audit in api_audits),
    ]
    combined_digest = hashlib.sha256(":".join(audit_parts).encode("utf-8")).hexdigest()
    return {
        "metrics": metrics,
        "trades": trades,
        "input_sha256": combined_digest,
        "market_data_requests": (
            archive_audit.request_count
            + metric_audit.request_count
            + recent_oi_audit.request_count
            + sum(audit.request_count for audit in api_audits)
        ),
        "market_data_bytes": (
            archive_audit.byte_count
            + metric_audit.byte_count
            + recent_oi_audit.byte_count
            + sum(audit.byte_count for audit in api_audits)
        ),
        "diagnostics": {
            "wif_klines": len(wif_rows),
            "wif_premium_klines": len(premium_rows),
            "wif_preliminary_signals": len(preliminary),
            "wif_signals": len(wif_signals),
            "wif_open_interest_points": len(oi_points),
            "dot_klines": len(dot_rows),
            "dot_signals": len(dot_signals),
        },
    }


def _fetch_json(url: str, params: dict[str, str], timeout_seconds: float = 30.0) -> Any:
    request = Request(
        f"{url}?{urlencode(params)}",
        headers={"Accept": "application/json", "User-Agent": "finruntime-backtest/1.0"},
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        return json.load(response)


def _binance_api_klines(
    path: str,
    symbol: str,
    start: date,
    end: date,
    *,
    interval: str = "15m",
) -> tuple[list[dict[str, str]], DownloadAudit]:
    interval_steps = {"15m": FIFTEEN_MINUTES_MS, "1h": 60 * 60 * 1000}
    step_ms = interval_steps[interval]
    cursor = int(datetime.combine(start, datetime.min.time(), UTC).timestamp() * 1000)
    end_ms = (
        int(
            datetime.combine(
                end + timedelta(days=1), datetime.min.time(), UTC
            ).timestamp()
            * 1000
        )
        - 1
    )
    rows: list[dict[str, str]] = []
    payloads: list[Any] = []
    while cursor <= end_ms:
        payload = _fetch_json(
            f"{BINANCE_FUTURES_API}{path}",
            {
                "symbol": symbol,
                "interval": interval,
                "startTime": str(cursor),
                "endTime": str(end_ms),
                "limit": "1500",
            },
        )
        if not isinstance(payload, list) or not payload:
            break
        payloads.append(payload)
        for row in payload:
            rows.append(
                {
                    "open_time": str(row[0]),
                    "open": str(row[1]),
                    "high": str(row[2]),
                    "low": str(row[3]),
                    "close": str(row[4]),
                    "volume": str(row[5]),
                    "close_time": str(row[6]),
                    "quote_volume": str(row[7]),
                    "count": str(row[8]),
                    "taker_buy_volume": str(row[9]),
                    "taker_buy_quote_volume": str(row[10]),
                    "ignore": str(row[11]),
                }
            )
        next_cursor = int(payload[-1][0]) + step_ms
        if next_cursor <= cursor:
            raise RuntimeError(f"Binance pagination did not advance for {symbol}")
        cursor = next_cursor
        if len(payload) < 1500:
            break
    canonical = json.dumps(payloads, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )
    return rows, DownloadAudit(
        request_count=len(payloads),
        byte_count=len(canonical),
        payload_sha256=hashlib.sha256(canonical).hexdigest(),
    )


def _binance_api_funding(
    symbol: str, start: date, end: date
) -> tuple[list[dict[str, str]], DownloadAudit]:
    cursor = int(datetime.combine(start, datetime.min.time(), UTC).timestamp() * 1000)
    end_ms = (
        int(
            datetime.combine(
                end + timedelta(days=1), datetime.min.time(), UTC
            ).timestamp()
            * 1000
        )
        - 1
    )
    rows: list[dict[str, str]] = []
    payloads: list[Any] = []
    while cursor <= end_ms:
        payload = _fetch_json(
            f"{BINANCE_FUTURES_API}/fapi/v1/fundingRate",
            {
                "symbol": symbol,
                "startTime": str(cursor),
                "endTime": str(end_ms),
                "limit": "1000",
            },
        )
        if not isinstance(payload, list) or not payload:
            break
        payloads.append(payload)
        for row in payload:
            rows.append(
                {
                    "calc_time": str(row["fundingTime"]),
                    "funding_interval_hours": "8",
                    "last_funding_rate": str(row["fundingRate"]),
                }
            )
        next_cursor = int(payload[-1]["fundingTime"]) + 1
        if next_cursor <= cursor:
            raise RuntimeError(
                f"Binance funding pagination did not advance for {symbol}"
            )
        cursor = next_cursor
        if len(payload) < 1000:
            break
    canonical = json.dumps(payloads, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )
    return rows, DownloadAudit(
        request_count=len(payloads),
        byte_count=len(canonical),
        payload_sha256=hashlib.sha256(canonical).hexdigest(),
    )


def _recent_open_interest_points(
    end: date,
) -> tuple[list[tuple[int, float]], DownloadAudit]:
    rows: list[dict[str, Any]] = []
    payloads: list[Any] = []
    window_end_ms = (
        int(
            datetime.combine(
                end + timedelta(days=1), datetime.min.time(), UTC
            ).timestamp()
            * 1000
        )
        - 1
    )
    end_time = window_end_ms
    for _batch in range(8):
        payload = _fetch_json(
            f"{BINANCE_FUTURES_API}/futures/data/openInterestHist",
            {
                "symbol": "WIFUSDT",
                "period": "5m",
                "limit": "500",
                "endTime": str(end_time),
            },
        )
        if not isinstance(payload, list) or not payload:
            break
        payloads.append(payload)
        rows.extend(payload)
        end_time = min(int(item["timestamp"]) for item in payload) - 1
    points = {
        int(row["timestamp"]): float(row["sumOpenInterest"])
        for row in rows
        if int(row["timestamp"]) <= window_end_ms and float(row["sumOpenInterest"]) > 0
    }
    downloaded = json.dumps(payloads, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )
    ordered_points = [(key, points[key]) for key in sorted(points)]
    canonical = json.dumps(
        ordered_points, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return (
        ordered_points,
        DownloadAudit(
            request_count=len(payloads),
            byte_count=len(downloaded),
            payload_sha256=hashlib.sha256(canonical).hexdigest(),
        ),
    )


def _bybit_funding(
    symbol: str, start: date, end: date
) -> tuple[list[tuple[int, float]], DownloadAudit]:
    start_ms = int(datetime.combine(start, datetime.min.time(), UTC).timestamp() * 1000)
    cursor = (
        int(
            datetime.combine(
                end + timedelta(days=1), datetime.min.time(), UTC
            ).timestamp()
            * 1000
        )
        - 1
    )
    unique: dict[int, float] = {}
    payloads: list[Any] = []
    while cursor >= start_ms:
        payload = _fetch_json(
            f"{BYBIT_API}/v5/market/funding/history",
            {
                "category": "linear",
                "symbol": symbol,
                "endTime": str(cursor),
                "limit": "200",
            },
        )
        if int(payload.get("retCode", -1)) != 0:
            raise RuntimeError(f"Bybit funding request failed for {symbol}: {payload}")
        rows = payload.get("result", {}).get("list", [])
        if not isinstance(rows, list) or not rows:
            break
        payloads.append(payload)
        timestamps: list[int] = []
        for row in rows:
            timestamp = int(row["fundingRateTimestamp"])
            timestamps.append(timestamp)
            if timestamp >= start_ms:
                unique[timestamp] = float(row["fundingRate"])
        next_cursor = min(timestamps) - 1
        if next_cursor >= cursor:
            raise RuntimeError(f"Bybit funding pagination did not advance for {symbol}")
        cursor = next_cursor
        if min(timestamps) < start_ms:
            break
    canonical = json.dumps(payloads, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )
    return (
        [(key, unique[key]) for key in sorted(unique)],
        DownloadAudit(
            request_count=len(payloads),
            byte_count=len(canonical),
            payload_sha256=hashlib.sha256(canonical).hexdigest(),
        ),
    )


def _bybit_mark_klines(
    symbol: str, start: date, end: date
) -> tuple[list[tuple[int, float]], DownloadAudit]:
    start_ms = int(datetime.combine(start, datetime.min.time(), UTC).timestamp() * 1000)
    cursor = (
        int(
            datetime.combine(
                end + timedelta(days=1), datetime.min.time(), UTC
            ).timestamp()
            * 1000
        )
        - 1
    )
    unique: dict[int, float] = {}
    payloads: list[Any] = []
    while cursor >= start_ms:
        payload = _fetch_json(
            f"{BYBIT_API}/v5/market/mark-price-kline",
            {
                "category": "linear",
                "symbol": symbol,
                "interval": "60",
                "end": str(cursor),
                "limit": "1000",
            },
        )
        if int(payload.get("retCode", -1)) != 0:
            raise RuntimeError(f"Bybit mark request failed for {symbol}: {payload}")
        rows = payload.get("result", {}).get("list", [])
        if not isinstance(rows, list) or not rows:
            break
        payloads.append(payload)
        timestamps: list[int] = []
        for row in rows:
            timestamp = int(row[0])
            timestamps.append(timestamp)
            if timestamp >= start_ms:
                unique[timestamp + ONE_HOUR_MS] = float(row[4])
        next_cursor = min(timestamps) - 1
        if next_cursor >= cursor:
            raise RuntimeError(f"Bybit mark pagination did not advance for {symbol}")
        cursor = next_cursor
        if min(timestamps) < start_ms:
            break
    canonical = json.dumps(payloads, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )
    return (
        [(key, unique[key]) for key in sorted(unique)],
        DownloadAudit(
            request_count=len(payloads),
            byte_count=len(canonical),
            payload_sha256=hashlib.sha256(canonical).hexdigest(),
        ),
    )


def _binance_funding_series(
    symbol: str, start: date, end: date
) -> tuple[list[tuple[int, float]], DownloadAudit]:
    rows, audit = _binance_api_funding(symbol, start, end)
    unique = {int(row["calc_time"]): float(row["last_funding_rate"]) for row in rows}
    return [(key, unique[key]) for key in sorted(unique)], audit


def _binance_mark_klines(
    symbol: str, start: date, end: date
) -> tuple[list[tuple[int, float]], DownloadAudit]:
    rows, audit = _binance_api_klines(
        "/fapi/v1/markPriceKlines", symbol, start, end, interval="1h"
    )
    unique = {int(row["open_time"]) + ONE_HOUR_MS: float(row["close"]) for row in rows}
    return [(key, unique[key]) for key in sorted(unique)], audit


def _latest_price(prices: list[tuple[int, float]], timestamp: int) -> float | None:
    left = 0
    right = len(prices)
    while left < right:
        middle = (left + right) // 2
        if prices[middle][0] <= timestamp:
            left = middle + 1
        else:
            right = middle
    return prices[left - 1][1] if left else None


def _recent_interval_hours(history: list[tuple[int, float]]) -> float:
    if len(history) < 2:
        return 8.0
    hours = (history[-1][0] - history[-2][0]) / (60 * 60 * 1000)
    return hours if 0.5 <= hours <= 24 else 8.0


def _funding_candidates(
    symbol: str,
    binance_funding: list[tuple[int, float]],
    bybit_funding: list[tuple[int, float]],
    binance_prices: list[tuple[int, float]],
    bybit_prices: list[tuple[int, float]],
) -> list[dict[str, Any]]:
    binance_by_time = dict(binance_funding)
    bybit_by_time = dict(bybit_funding)
    event_times = sorted(set(binance_by_time) | set(bybit_by_time))
    binance_history: list[tuple[int, float]] = []
    bybit_history: list[tuple[int, float]] = []
    candidates: list[dict[str, Any]] = []
    for timestamp in event_times:
        if timestamp in binance_by_time:
            binance_history.append((timestamp, binance_by_time[timestamp]))
        if timestamp in bybit_by_time:
            bybit_history.append((timestamp, bybit_by_time[timestamp]))
        if len(binance_history) < 3 or len(bybit_history) < 3:
            continue
        binance_interval = _recent_interval_hours(binance_history)
        bybit_interval = _recent_interval_hours(bybit_history)
        binance_rate = binance_history[-1][1]
        bybit_rate = bybit_history[-1][1]
        binance_hourly = binance_rate / binance_interval
        bybit_hourly = bybit_rate / bybit_interval
        if binance_hourly <= bybit_hourly:
            long_exchange, short_exchange = "binance", "bybit"
            long_rate, short_rate = binance_rate, bybit_rate
            long_interval, short_interval = binance_interval, bybit_interval
            long_prediction = statistics.median(
                value for _, value in binance_history[-3:]
            )
            short_prediction = statistics.median(
                value for _, value in bybit_history[-3:]
            )
            long_prices, short_prices = binance_prices, bybit_prices
        else:
            long_exchange, short_exchange = "bybit", "binance"
            long_rate, short_rate = bybit_rate, binance_rate
            long_interval, short_interval = bybit_interval, binance_interval
            long_prediction = statistics.median(
                value for _, value in bybit_history[-3:]
            )
            short_prediction = statistics.median(
                value for _, value in binance_history[-3:]
            )
            long_prices, short_prices = bybit_prices, binance_prices
        current_spread_bps_8h = (
            (short_rate / short_interval - long_rate / long_interval) * 8 * 10_000
        )
        predicted_spread_bps_8h = (
            (short_prediction / short_interval - long_prediction / long_interval)
            * 8
            * 10_000
        )
        if current_spread_bps_8h < 8 or predicted_spread_bps_8h < 5:
            continue
        long_events = max(1, round(24 / long_interval))
        short_events = max(1, round(24 / short_interval))
        current_gross_bps = (
            short_rate * short_events - long_rate * long_events
        ) * 10_000
        predicted_gross_bps = (
            short_prediction * short_events - long_prediction * long_events
        ) * 10_000
        long_price = _latest_price(long_prices, timestamp)
        short_price = _latest_price(short_prices, timestamp)
        if long_price is None or short_price is None:
            continue
        mark_divergence_bps = abs(long_price / short_price - 1) * 10_000
        if mark_divergence_bps > 75:
            continue
        entry_basis_bps = (long_price / short_price - 1) * 10_000
        if abs(entry_basis_bps) > 35:
            continue
        fee_bps = 18.0
        slippage_bps = 3.0
        safety_bps = 11.0
        basis_cost_bps = max(0.0, entry_basis_bps)
        expected_net_bps = (
            min(current_gross_bps, predicted_gross_bps)
            - fee_bps
            - slippage_bps
            - safety_bps
            - basis_cost_bps
        )
        if expected_net_bps < 10:
            continue
        candidates.append(
            {
                "asset": symbol.removesuffix("USDT"),
                "symbol": symbol,
                "entry_time_ms": timestamp,
                "exit_time_ms": timestamp + FUNDING_MAX_HOLD_HOURS * ONE_HOUR_MS,
                "long_exchange": long_exchange,
                "short_exchange": short_exchange,
                "long_entry_price": long_price,
                "short_entry_price": short_price,
                "expected_net_bps": expected_net_bps,
                "current_spread_bps_8h": current_spread_bps_8h,
                "predicted_spread_bps_8h": predicted_spread_bps_8h,
                "charged_cost_bps": (
                    fee_bps + slippage_bps + safety_bps + basis_cost_bps
                ),
            }
        )
    return candidates


def _funding_rates_at(
    history: list[tuple[int, float]], timestamp: int
) -> tuple[float, float, float] | None:
    available = [item for item in history if item[0] <= timestamp]
    if len(available) < 3:
        return None
    current = available[-1][1]
    predicted = statistics.median(value for _, value in available[-3:])
    return current, predicted, _recent_interval_hours(available)


def _funding_exit_reason(
    position: dict[str, Any],
    timestamp: int,
    data: dict[str, dict[str, list[tuple[int, float]]]],
) -> str | None:
    opened_at = int(position["entry_time_ms"])
    maximum_hold_ms = FUNDING_MAX_HOLD_HOURS * ONE_HOUR_MS
    if timestamp >= opened_at + maximum_hold_ms:
        return "max_hold_hours"
    symbol = str(position["symbol"])
    long_exchange = str(position["long_exchange"])
    short_exchange = str(position["short_exchange"])
    long_rates = _funding_rates_at(data[symbol][f"{long_exchange}_funding"], timestamp)
    short_rates = _funding_rates_at(
        data[symbol][f"{short_exchange}_funding"], timestamp
    )
    if long_rates is None or short_rates is None:
        return None
    long_current, long_predicted, long_interval = long_rates
    short_current, short_predicted, short_interval = short_rates
    current_spread_bps_8h = (
        (short_current / short_interval - long_current / long_interval) * 8 * 10_000
    )
    predicted_spread_bps_8h = (
        (short_predicted / short_interval - long_predicted / long_interval) * 8 * 10_000
    )
    if current_spread_bps_8h <= FUNDING_EXIT_SPREAD_BPS_8H:
        return "current_funding_spread_collapsed"
    if predicted_spread_bps_8h <= 0:
        return "predicted_funding_reversal"
    return None


def _funding_trade_pnl(
    position: dict[str, Any],
    timestamp: int,
    data: dict[str, dict[str, list[tuple[int, float]]]],
) -> tuple[float, float, float, float]:
    symbol = str(position["symbol"])
    entry_time = int(position["entry_time_ms"])
    long_exchange = str(position["long_exchange"])
    short_exchange = str(position["short_exchange"])
    long_prices = data[symbol][f"{long_exchange}_prices"]
    short_prices = data[symbol][f"{short_exchange}_prices"]
    long_exit = _latest_price(long_prices, timestamp)
    short_exit = _latest_price(short_prices, timestamp)
    if long_exit is None or short_exit is None:
        raise ValueError(f"missing funding-router mark for {symbol} at {timestamp}")
    notional = 1_000.0
    price_pnl = (
        notional
        / float(position["long_entry_price"])
        * (long_exit - float(position["long_entry_price"]))
    )
    price_pnl += (
        notional
        / float(position["short_entry_price"])
        * (float(position["short_entry_price"]) - short_exit)
    )
    long_rates = data[symbol][f"{long_exchange}_funding"]
    short_rates = data[symbol][f"{short_exchange}_funding"]
    long_paid = sum(
        rate for observed, rate in long_rates if entry_time < observed <= timestamp
    )
    short_received = sum(
        rate for observed, rate in short_rates if entry_time < observed <= timestamp
    )
    funding_pnl = notional * (short_received - long_paid)
    cost = notional * float(position["charged_cost_bps"]) / 10_000
    return price_pnl + funding_pnl - cost, long_exit, short_exit, funding_pnl


def run_funding_backtest(start: date, end: date) -> dict[str, Any]:
    """Replay the causal funding-spread core on Binance and Bybit history."""

    symbols = ("BTCUSDT", "ETHUSDT", "PENDLEUSDT", "WIFUSDT", "DOTUSDT")
    loaders = {
        "binance_funding": _binance_funding_series,
        "bybit_funding": _bybit_funding,
        "binance_prices": _binance_mark_klines,
        "bybit_prices": _bybit_mark_klines,
    }
    data: dict[str, dict[str, list[tuple[int, float]]]] = defaultdict(dict)
    audits: list[DownloadAudit] = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(loader, symbol, start - timedelta(days=2), end): (
                symbol,
                name,
            )
            for symbol in symbols
            for name, loader in loaders.items()
        }
        for future in as_completed(futures):
            symbol, name = futures[future]
            rows, audit = future.result()
            data[symbol][name] = rows
            audits.append(audit)

    candidates_by_time: dict[int, list[dict[str, Any]]] = defaultdict(list)
    start_ms = int(datetime.combine(start, datetime.min.time(), UTC).timestamp() * 1000)
    end_ms = (
        int(
            datetime.combine(
                end + timedelta(days=1), datetime.min.time(), UTC
            ).timestamp()
            * 1000
        )
        - 1
    )
    for symbol in symbols:
        for candidate in _funding_candidates(
            symbol,
            data[symbol]["binance_funding"],
            data[symbol]["bybit_funding"],
            data[symbol]["binance_prices"],
            data[symbol]["bybit_prices"],
        ):
            entry_time = int(candidate["entry_time_ms"])
            if start_ms <= entry_time <= end_ms:
                candidates_by_time[entry_time].append(candidate)

    market_event_times = {
        timestamp
        for symbol in symbols
        for exchange in ("binance", "bybit")
        for timestamp, _rate in data[symbol][f"{exchange}_funding"]
        if start_ms <= timestamp <= end_ms
    }
    expiry_times = {
        timestamp + FUNDING_MAX_HOLD_HOURS * ONE_HOUR_MS
        for timestamp in candidates_by_time
        if timestamp + FUNDING_MAX_HOLD_HOURS * ONE_HOUR_MS <= end_ms
    }
    event_times = sorted(market_event_times | set(candidates_by_time) | expiry_times)
    realized = 0.0
    position: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    daily: dict[str, float] = {}
    for timestamp in event_times:
        exit_reason = (
            _funding_exit_reason(position, timestamp, data)
            if position is not None
            else None
        )
        if position is not None and exit_reason is not None:
            exit_time = timestamp
            pnl, long_exit, short_exit, funding_pnl = _funding_trade_pnl(
                position, exit_time, data
            )
            realized += pnl
            entry_date = datetime.fromtimestamp(
                int(position["entry_time_ms"]) / 1000, UTC
            ).date()
            exit_date = datetime.fromtimestamp(exit_time / 1000, UTC).date()
            trades.append(
                {
                    "id": f"funding-{position['symbol']}-{position['entry_time_ms']}",
                    "asset": str(position["asset"]),
                    "direction": (
                        f"LONG {position['long_exchange']} / "
                        f"SHORT {position['short_exchange']}"
                    ),
                    "status": "closed",
                    "entry_date": entry_date.isoformat(),
                    "exit_date": exit_date.isoformat(),
                    "held_through": exit_date.isoformat(),
                    "holding_days": (exit_date - entry_date).days,
                    "entry_price": float(position["long_entry_price"]),
                    "exit_price": long_exit,
                    "asset_return_percent": pnl / 1_000 * 100,
                    "net_pnl_usd": pnl,
                    "order_count": 4,
                    "short_entry_price": float(position["short_entry_price"]),
                    "short_exit_price": short_exit,
                    "funding_pnl_usd": funding_pnl,
                    "expected_net_bps": float(position["expected_net_bps"]),
                    "exit_reason": exit_reason,
                }
            )
            daily[exit_date.isoformat()] = INITIAL_NAV_USD + realized
            position = None
        if position is None:
            available = candidates_by_time[timestamp]
            if available:
                position = max(
                    available, key=lambda item: float(item["expected_net_bps"])
                )
        if position is not None:
            pnl, _long, _short, _funding = _funding_trade_pnl(position, timestamp, data)
            observed_date = (
                datetime.fromtimestamp(timestamp / 1000, UTC).date().isoformat()
            )
            daily[observed_date] = INITIAL_NAV_USD + realized + pnl

    if position is not None:
        exit_time = end_ms
        pnl, long_exit, short_exit, funding_pnl = _funding_trade_pnl(
            position, exit_time, data
        )
        realized += pnl
        entry_date = datetime.fromtimestamp(
            int(position["entry_time_ms"]) / 1000, UTC
        ).date()
        exit_date = datetime.fromtimestamp(exit_time / 1000, UTC).date()
        trades.append(
            {
                "id": f"funding-{position['symbol']}-{position['entry_time_ms']}",
                "asset": str(position["asset"]),
                "direction": (
                    f"LONG {position['long_exchange']} / SHORT {position['short_exchange']}"
                ),
                "status": "closed",
                "entry_date": entry_date.isoformat(),
                "exit_date": exit_date.isoformat(),
                "held_through": exit_date.isoformat(),
                "holding_days": (exit_date - entry_date).days,
                "entry_price": float(position["long_entry_price"]),
                "exit_price": long_exit,
                "asset_return_percent": pnl / 1_000 * 100,
                "net_pnl_usd": pnl,
                "order_count": 4,
                "short_entry_price": float(position["short_entry_price"]),
                "short_exit_price": short_exit,
                "funding_pnl_usd": funding_pnl,
                "expected_net_bps": float(position["expected_net_bps"]),
                "exit_reason": "window_end",
            }
        )
    daily[end.isoformat()] = INITIAL_NAV_USD + realized
    daily_rows = [{"date": key, "navUsd": daily[key]} for key in sorted(daily)]
    metrics = _portfolio_metrics(
        daily_rows,
        start,
        end,
        "on_demand_two_year_funding_core_replay",
        "Causal funding-spread replay за 2 года",
    )
    combined_digest = hashlib.sha256(
        ":".join(sorted(audit.payload_sha256 for audit in audits)).encode("utf-8")
    ).hexdigest()
    return {
        "metrics": metrics,
        "trades": sorted(
            trades, key=lambda item: (item["exit_date"], item["asset"]), reverse=True
        ),
        "input_sha256": combined_digest,
        "market_data_requests": sum(audit.request_count for audit in audits),
        "market_data_bytes": sum(audit.byte_count for audit in audits),
        "diagnostics": {
            "symbols": list(symbols),
            "candidate_count": sum(len(items) for items in candidates_by_time.values()),
            "historical_order_book_filter_applied": False,
            "historical_open_interest_filter_applied": False,
            "fixed_notional_usd": 1_000.0,
        },
    }
