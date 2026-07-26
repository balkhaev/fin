from __future__ import annotations

import hashlib
import io
import json
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

from config import (
    END,
    MIN_FULL_MONTHS,
    NORMALIZED_FREQUENCY,
    PRICE_MOVE_LOOKBACK_MINUTES,
    REPLENISHMENT_LOOKBACK_MINUTES,
    REQUIRED_MONTH_COVERAGE,
    ROLLING_MIN_PERIODS,
    ROLLING_WINDOW_MINUTES,
    START,
    SYMBOLS,
)

BASE = "https://data.binance.vision/data/futures/cm"
THREAD_LOCAL = threading.local()
DEPTH_PERCENTAGES = (-5, -1, 1, 5)
MARK_COLUMNS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "base_volume",
    "trade_count",
    "taker_buy_volume",
    "taker_buy_base_volume",
    "ignore",
)


@dataclass(slots=True)
class ArchiveFile:
    dataset: str
    symbol: str
    period: str
    path: Path
    url: str
    sha256: str
    rows: int | None = None


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def session() -> requests.Session:
    value = getattr(THREAD_LOCAL, "client", None)
    if value is None:
        value = requests.Session()
        value.headers.update(
            {
                "User-Agent": "fin-research-v198/1.0 (+https://github.com/balkhaev/fin)",
                "Accept": "application/zip,text/plain,*/*",
            }
        )
        THREAD_LOCAL.client = value
    return value


def request(url: str) -> requests.Response:
    last: Exception | None = None
    for attempt in range(6):
        try:
            response = session().get(url, timeout=180)
            if response.status_code in {429, 500, 502, 503, 504}:
                time.sleep(min(15.0, 1.0 + 1.5 * attempt))
                continue
            return response
        except requests.RequestException as exc:
            last = exc
            time.sleep(min(15.0, 1.0 + 1.5 * attempt))
    if last is not None:
        raise last
    raise RuntimeError(f"request failed: {url}")


def month_strings() -> list[str]:
    start = pd.Timestamp(START).to_period("M")
    end = pd.Timestamp(END).to_period("M")
    return [str(value) for value in pd.period_range(start, end, freq="M")]


def month_dates(month: str) -> list[str]:
    period = pd.Period(month, freq="M")
    return [
        value.strftime("%Y-%m-%d")
        for value in pd.date_range(period.start_time, period.end_time, freq="1D")
    ]


def monthly_url(dataset: str, symbol: str, month: str) -> str:
    if dataset == "bookDepth":
        filename = f"{symbol}-bookDepth-{month}.zip"
        return f"{BASE}/monthly/bookDepth/{symbol}/{filename}"
    filename = f"{symbol}-1m-{month}.zip"
    return f"{BASE}/monthly/markPriceKlines/{symbol}/1m/{filename}"


def daily_url(dataset: str, symbol: str, day: str) -> str:
    if dataset == "bookDepth":
        filename = f"{symbol}-bookDepth-{day}.zip"
        return f"{BASE}/daily/bookDepth/{symbol}/{filename}"
    filename = f"{symbol}-1m-{day}.zip"
    return f"{BASE}/daily/markPriceKlines/{symbol}/1m/{filename}"


def cache_target(cache: Path, dataset: str, symbol: str, period: str) -> Path:
    return cache / dataset / symbol / f"{period}.zip"


def download_one(
    cache: Path,
    dataset: str,
    symbol: str,
    period: str,
    url: str,
) -> ArchiveFile | None:
    target = cache_target(cache, dataset, symbol, period)
    meta_path = target.with_suffix(".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and meta_path.exists():
        meta = json.loads(meta_path.read_text())
        actual = sha256_file(target)
        if actual == meta.get("sha256"):
            return ArchiveFile(dataset, symbol, period, target, url, actual)
        target.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)

    checksum_response = request(url + ".CHECKSUM")
    archive_response = request(url)
    if checksum_response.status_code == 404 or archive_response.status_code == 404:
        return None
    if checksum_response.status_code != 200 or archive_response.status_code != 200:
        raise RuntimeError(
            f"download failed {dataset} {symbol} {period}: "
            f"checksum={checksum_response.status_code} archive={archive_response.status_code}"
        )
    expected = checksum_response.text.strip().split()[0].lower()
    actual = sha256_bytes(archive_response.content)
    if expected != actual:
        raise ValueError(f"checksum mismatch for {url}: {expected} != {actual}")
    target.write_bytes(archive_response.content)
    meta_path.write_text(
        json.dumps(
            {
                "dataset": dataset,
                "symbol": symbol,
                "period": period,
                "url": url,
                "bytes": len(archive_response.content),
                "sha256": actual,
            },
            indent=2,
        )
        + "\n"
    )
    return ArchiveFile(dataset, symbol, period, target, url, actual)


def download_month(
    cache: Path,
    dataset: str,
    symbol: str,
    month: str,
) -> list[ArchiveFile]:
    monthly = download_one(
        cache,
        dataset,
        symbol,
        month,
        monthly_url(dataset, symbol, month),
    )
    if monthly is not None:
        return [monthly]

    outputs: list[ArchiveFile] = []
    days = month_dates(month)
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(
                download_one,
                cache,
                dataset,
                symbol,
                day,
                daily_url(dataset, symbol, day),
            ): day
            for day in days
        }
        for future in as_completed(futures):
            value = future.result()
            if value is not None:
                outputs.append(value)
    return sorted(outputs, key=lambda value: value.period)


def read_member(path: Path) -> bytes:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise ValueError(f"expected one CSV in {path}, got {names}")
        return archive.read(names[0])


def read_depth(files: list[ArchiveFile]) -> tuple[pd.DataFrame, int]:
    frames: list[pd.DataFrame] = []
    raw_rows = 0
    for item in files:
        frame = pd.read_csv(io.BytesIO(read_member(item.path)))
        required = {"timestamp", "percentage", "notional"}
        if not required.issubset(frame.columns):
            raise ValueError(f"invalid bookDepth schema in {item.path}: {frame.columns.tolist()}")
        raw_rows += len(frame)
        frame = frame[["timestamp", "percentage", "notional"]].copy()
        frame["percentage"] = pd.to_numeric(frame["percentage"], errors="coerce")
        frame["notional"] = pd.to_numeric(frame["notional"], errors="coerce")
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
        frame = frame.dropna(subset=["timestamp", "percentage", "notional"])
        frame = frame[frame.percentage.isin(DEPTH_PERCENTAGES)]
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(), raw_rows
    values = pd.concat(frames, ignore_index=True)
    values = values.sort_values("timestamp")
    values["minute"] = values.timestamp.dt.floor(NORMALIZED_FREQUENCY)
    values = values.drop_duplicates(["minute", "percentage"], keep="last")
    pivot = values.pivot(index="minute", columns="percentage", values="notional")
    pivot = pivot.rename(
        columns={
            -5: "bid_wide",
            -1: "bid_near",
            1: "ask_near",
            5: "ask_wide",
        }
    )
    for column in ("bid_wide", "bid_near", "ask_near", "ask_wide"):
        if column not in pivot:
            pivot[column] = np.nan
    return pivot[["bid_wide", "bid_near", "ask_near", "ask_wide"]].sort_index(), raw_rows


def parse_mark_bytes(raw: bytes) -> pd.DataFrame:
    sample = pd.read_csv(io.BytesIO(raw), nrows=2, header=None)
    first = str(sample.iloc[0, 0]).lower()
    has_header = "open" in first or "time" in first
    frame = pd.read_csv(io.BytesIO(raw), header=0 if has_header else None)
    if not has_header:
        if frame.shape[1] < len(MARK_COLUMNS):
            raise ValueError(f"unexpected mark-price width: {frame.shape[1]}")
        frame = frame.iloc[:, : len(MARK_COLUMNS)]
        frame.columns = MARK_COLUMNS
    else:
        normalized = {
            "open_time": None,
            "open": None,
            "close": None,
        }
        for column in frame.columns:
            key = "".join(character for character in str(column).lower() if character.isalnum())
            if key in {"opentime", "open_time"}:
                normalized["open_time"] = column
            elif key == "open":
                normalized["open"] = column
            elif key == "close":
                normalized["close"] = column
        if not all(normalized.values()):
            raise ValueError(f"invalid mark-price header: {frame.columns.tolist()}")
        frame = frame.rename(columns={value: key for key, value in normalized.items()})
    output = frame[["open_time", "open", "close"]].copy()
    time_value = pd.to_numeric(output.open_time, errors="coerce")
    time_value = time_value.where(time_value < 10**15, time_value / 1000.0)
    output["timestamp"] = pd.to_datetime(time_value, unit="ms", utc=True, errors="coerce")
    output["open"] = pd.to_numeric(output.open, errors="coerce")
    output["close"] = pd.to_numeric(output.close, errors="coerce")
    output = output.dropna(subset=["timestamp", "open", "close"])
    output = output[(output.open > 0) & (output.close > 0)]
    return output.drop_duplicates("timestamp", keep="last").set_index("timestamp")[
        ["open", "close"]
    ]


def read_mark(files: list[ArchiveFile]) -> tuple[pd.DataFrame, int]:
    frames: list[pd.DataFrame] = []
    raw_rows = 0
    for item in files:
        raw = read_member(item.path)
        frame = parse_mark_bytes(raw)
        raw_rows += len(frame)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(), raw_rows
    return pd.concat(frames).sort_index().loc[lambda value: ~value.index.duplicated(keep="last")], raw_rows


def add_features(panel: pd.DataFrame) -> pd.DataFrame:
    frame = panel.copy()
    near_total = frame.bid_near + frame.ask_near
    wide_total = frame.bid_wide + frame.ask_wide
    frame["near_imbalance"] = (frame.bid_near - frame.ask_near) / near_total.where(
        near_total > 0
    )
    frame["wide_imbalance"] = (frame.bid_wide - frame.ask_wide) / wide_total.where(
        wide_total > 0
    )
    frame["pressure"] = 0.70 * frame.near_imbalance + 0.30 * frame.wide_imbalance
    frame["near_total"] = near_total
    pressure_mean = frame.pressure.rolling(
        ROLLING_WINDOW_MINUTES, min_periods=ROLLING_MIN_PERIODS
    ).mean()
    pressure_std = frame.pressure.rolling(
        ROLLING_WINDOW_MINUTES, min_periods=ROLLING_MIN_PERIODS
    ).std(ddof=1)
    frame["pressure_z"] = (frame.pressure - pressure_mean) / pressure_std.where(
        pressure_std > 1e-12
    )
    log_depth = np.log(frame.near_total.where(frame.near_total > 0))
    depth_mean = log_depth.rolling(
        ROLLING_WINDOW_MINUTES, min_periods=ROLLING_MIN_PERIODS
    ).mean()
    depth_std = log_depth.rolling(
        ROLLING_WINDOW_MINUTES, min_periods=ROLLING_MIN_PERIODS
    ).std(ddof=1)
    frame["depth_z"] = (log_depth - depth_mean) / depth_std.where(depth_std > 1e-12)
    frame["bid_replenishment"] = (
        frame.bid_near / frame.bid_near.shift(REPLENISHMENT_LOOKBACK_MINUTES) - 1.0
    ).clip(-5.0, 5.0)
    frame["ask_replenishment"] = (
        frame.ask_near / frame.ask_near.shift(REPLENISHMENT_LOOKBACK_MINUTES) - 1.0
    ).clip(-5.0, 5.0)
    frame["price_move"] = frame.close / frame.close.shift(PRICE_MOVE_LOOKBACK_MINUTES) - 1.0
    frame["quality"] = frame[
        [
            "bid_wide",
            "bid_near",
            "ask_near",
            "ask_wide",
            "open",
            "close",
        ]
    ].notna().all(axis=1)
    return frame.replace([np.inf, -np.inf], np.nan)


def period_months(start: str, end: str) -> set[str]:
    return {
        str(value)
        for value in pd.period_range(pd.Timestamp(start).to_period("M"), pd.Timestamp(end).to_period("M"), freq="M")
    }


def load_symbol(
    symbol: str,
    cache: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]]]:
    panels: list[pd.DataFrame] = []
    provenance: list[dict[str, Any]] = []
    quality: list[dict[str, Any]] = []
    for month in month_strings():
        print(f"{symbol} {month}: download", flush=True)
        depth_files = download_month(cache, "bookDepth", symbol, month)
        mark_files = download_month(cache, "markPriceKlines", symbol, month)
        depth, depth_raw_rows = read_depth(depth_files)
        mark, mark_raw_rows = read_mark(mark_files)
        joined = depth.join(mark, how="outer").sort_index()
        period = pd.Period(month, freq="M")
        expected = int((period.end_time.date() - period.start_time.date()).days + 1) * 1440
        complete = joined[
            ["bid_wide", "bid_near", "ask_near", "ask_wide", "open", "close"]
        ].notna().all(axis=1)
        coverage = float(complete.sum() / expected) if expected else 0.0
        full = coverage >= REQUIRED_MONTH_COVERAGE
        quality.append(
            {
                "symbol": symbol,
                "month": month,
                "expected_minutes": expected,
                "joined_minutes": len(joined),
                "complete_minutes": int(complete.sum()),
                "coverage": coverage,
                "full_month": full,
                "depth_raw_rows": depth_raw_rows,
                "mark_raw_rows": mark_raw_rows,
                "depth_archive_count": len(depth_files),
                "mark_archive_count": len(mark_files),
            }
        )
        for item in depth_files + mark_files:
            provenance.append(
                {
                    "dataset": item.dataset,
                    "symbol": item.symbol,
                    "period": item.period,
                    "url": item.url,
                    "sha256": item.sha256,
                    "bytes": item.path.stat().st_size,
                }
            )
        if not joined.empty:
            panels.append(joined)
    if not panels:
        return pd.DataFrame(), provenance, quality
    panel = pd.concat(panels).sort_index()
    panel = panel.loc[~panel.index.duplicated(keep="last")]
    full_index = pd.date_range(
        pd.Timestamp(START, tz="UTC"),
        pd.Timestamp(END, tz="UTC"),
        freq=NORMALIZED_FREQUENCY,
    )
    panel = panel.reindex(full_index)
    return add_features(panel), provenance, quality


def coverage_gate(quality_rows: list[dict[str, Any]]) -> dict[str, Any]:
    period_definitions = {
        "development": (START, DEVELOPMENT_END),
        "validation": (VALIDATION_START, VALIDATION_END),
        "holdout": (HOLDOUT_START, HOLDOUT_END),
        "final": (FINAL_START, END),
    }
    symbols: dict[str, Any] = {}
    passed = True
    for symbol in SYMBOLS:
        values = [row for row in quality_rows if row["symbol"] == symbol]
        periods: dict[str, Any] = {}
        for name, (start, end) in period_definitions.items():
            months = period_months(start, end)
            rows = [row for row in values if row["month"] in months]
            full_count = sum(bool(row["full_month"]) for row in rows)
            required = MIN_FULL_MONTHS[name]
            value = {
                "months": len(months),
                "full_months": full_count,
                "required_full_months": required,
                "passed": full_count >= required,
                "minimum_coverage": min([row["coverage"] for row in rows], default=0.0),
                "median_coverage": float(
                    np.median([row["coverage"] for row in rows]) if rows else 0.0
                ),
            }
            periods[name] = value
            passed = passed and value["passed"]
        symbols[symbol] = {"periods": periods}
    return {
        "candidate": "V198_DEPTH_PANEL_COVERAGE",
        "required_month_coverage": REQUIRED_MONTH_COVERAGE,
        "symbols": symbols,
        "passed": passed,
    }


def load_all(
    cache: Path,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any], pd.DataFrame, dict[str, Any]]:
    panels: dict[str, pd.DataFrame] = {}
    provenance_rows: list[dict[str, Any]] = []
    quality_rows: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        panel, provenance, quality = load_symbol(symbol, cache)
        panels[symbol] = panel
        provenance_rows.extend(provenance)
        quality_rows.extend(quality)
    gate = coverage_gate(quality_rows)
    provenance = {
        "candidate": "V198_CAUSAL_DEPTH_PANEL",
        "normalized_frequency": NORMALIZED_FREQUENCY,
        "depth_snapshot_rule": "last completed 30-second snapshot in each UTC minute",
        "price_rule": "completed mark close for features; next-minute mark open for proxy execution",
        "archives": provenance_rows,
    }
    return panels, provenance, pd.DataFrame(quality_rows), gate
