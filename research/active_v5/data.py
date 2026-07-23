from __future__ import annotations

import hashlib
import io
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from config import ResearchConfig

KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
]
KLINE_NUMERIC = [
    "open", "high", "low", "close", "volume", "quote_volume", "trades",
    "taker_buy_base", "taker_buy_quote",
]


@dataclass(frozen=True)
class DownloadRecord:
    market: str
    data_type: str
    symbol: str
    interval: str
    year_month: str
    url: str
    bytes: int
    sha256: str
    checksum_available: bool
    checksum_passed: bool | None
    rows: int


def utc(value: str) -> pd.Timestamp:
    return pd.Timestamp(value, tz="UTC")


def iter_months(start: str, end_exclusive: str) -> Iterable[pd.Timestamp]:
    current = utc(start).to_period("M").to_timestamp().tz_localize("UTC")
    end = utc(end_exclusive)
    while current < end:
        yield current
        current += pd.offsets.MonthBegin(1)


def request_bytes(url: str, retries: int = 6, timeout: int = 120) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "fin-active-v4/1.0"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                break
            time.sleep(min(30.0, 1.5 * (2**attempt)))
    raise RuntimeError(f"download failed: {url}: {last}")


def parse_checksum(payload: bytes) -> str | None:
    try:
        value = payload.decode("utf-8").strip().split()[0].lower()
    except Exception:
        return None
    if len(value) == 64 and all(char in "0123456789abcdef" for char in value):
        return value
    return None


def timestamp_unit(series: pd.Series) -> str:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return "ms"
    magnitude = float(values.abs().median())
    if magnitude >= 1e17:
        return "ns"
    if magnitude >= 1e14:
        return "us"
    if magnitude >= 1e11:
        return "ms"
    return "s"


def unzip_csv(payload: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = sorted(name for name in archive.namelist() if name.lower().endswith(".csv"))
        if not names:
            raise ValueError("archive contains no CSV")
        return archive.read(names[0])


def parse_klines(payload: bytes, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    raw = unzip_csv(payload)
    frame = pd.read_csv(io.BytesIO(raw), header=None, names=KLINE_COLUMNS, low_memory=False)
    frame["open_time"] = pd.to_datetime(
        pd.to_numeric(frame["open_time"], errors="coerce"),
        unit=timestamp_unit(frame["open_time"]),
        utc=True,
        errors="coerce",
    )
    for column in KLINE_NUMERIC:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["open_time", *KLINE_NUMERIC])
    frame = frame[(frame.open_time >= start) & (frame.open_time < end)]
    frame = frame.sort_values("open_time", kind="mergesort").drop_duplicates("open_time", keep="last")
    return frame.set_index("open_time")[KLINE_NUMERIC].astype(float)


def parse_funding(payload: bytes, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    raw = unzip_csv(payload)
    first_line = raw.splitlines()[0].decode("utf-8", errors="ignore").lower() if raw else ""
    has_header = any(token in first_line for token in ("calc_time", "funding", "rate"))
    frame = pd.read_csv(io.BytesIO(raw), header=0 if has_header else None, low_memory=False)
    if frame.empty:
        return pd.Series(dtype=float, name="funding_rate")

    lower = {str(column).strip().lower(): column for column in frame.columns}
    time_column = next((lower[name] for name in ("calc_time", "funding_time", "time") if name in lower), None)
    rate_column = next(
        (lower[name] for name in ("last_funding_rate", "funding_rate", "rate") if name in lower),
        None,
    )

    numeric = frame.apply(pd.to_numeric, errors="coerce")
    if time_column is None:
        candidates = []
        for column in frame.columns:
            values = numeric[column].dropna()
            if len(values) and float(values.abs().median()) >= 1e11:
                candidates.append(column)
        if not candidates:
            raise ValueError("funding archive has no timestamp-like column")
        time_column = candidates[0]
    if rate_column is None:
        candidates = []
        for column in frame.columns:
            if column == time_column:
                continue
            values = numeric[column].dropna()
            if len(values) and float(values.abs().quantile(0.95)) < 1.0:
                candidates.append(column)
        if not candidates:
            raise ValueError("funding archive has no rate-like column")
        rate_column = candidates[-1]

    timestamp = pd.to_datetime(
        pd.to_numeric(frame[time_column], errors="coerce"),
        unit=timestamp_unit(frame[time_column]),
        utc=True,
        errors="coerce",
    )
    rate = pd.to_numeric(frame[rate_column], errors="coerce")
    result = pd.Series(rate.to_numpy(float), index=timestamp, name="funding_rate").dropna()
    result = result[(result.index >= start) & (result.index < end)]
    result = result.groupby(level=0).sum().sort_index()
    return result.astype(float)


def archive_url(market: str, data_type: str, symbol: str, interval: str, year_month: str) -> str:
    if market == "spot" and data_type == "klines":
        filename = f"{symbol}-{interval}-{year_month}.zip"
        return f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/{interval}/{filename}"
    if market == "futures_um" and data_type == "klines":
        filename = f"{symbol}-{interval}-{year_month}.zip"
        return f"https://data.binance.vision/data/futures/um/monthly/klines/{symbol}/{interval}/{filename}"
    if market == "futures_um" and data_type == "fundingRate":
        filename = f"{symbol}-fundingRate-{year_month}.zip"
        return f"https://data.binance.vision/data/futures/um/monthly/fundingRate/{symbol}/{filename}"
    raise ValueError(f"unsupported archive: {market}/{data_type}/{interval}")


def cached_payload(url: str, cache_path: Path, refresh: bool) -> tuple[bytes, str, bool, bool | None]:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if refresh or not cache_path.exists():
        cache_path.write_bytes(request_bytes(url))
    payload = cache_path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    checksum_available = False
    checksum_passed: bool | None = None
    try:
        expected = parse_checksum(request_bytes(url + ".CHECKSUM", retries=2, timeout=30))
        if expected:
            checksum_available = True
            checksum_passed = expected == digest
            if not checksum_passed:
                raise ValueError(f"checksum mismatch: {url}")
    except RuntimeError:
        pass
    return payload, digest, checksum_available, checksum_passed


def download_one(
    market: str,
    data_type: str,
    symbol: str,
    interval: str,
    month: pd.Timestamp,
    config: ResearchConfig,
    cache: Path,
    refresh: bool,
) -> tuple[str, str, str, pd.DataFrame | pd.Series, dict[str, object]]:
    next_month = month + pd.offsets.MonthBegin(1)
    clip_start = max(month, utc(config.start))
    clip_end = min(next_month, utc(config.end_exclusive))
    ym = month.strftime("%Y-%m")
    url = archive_url(market, data_type, symbol, interval, ym)
    filename = url.rsplit("/", 1)[-1]
    cache_path = cache / market / data_type / symbol / interval / filename
    payload, digest, available, passed = cached_payload(url, cache_path, refresh)
    if data_type == "fundingRate":
        parsed: pd.DataFrame | pd.Series = parse_funding(payload, clip_start, clip_end)
    else:
        parsed = parse_klines(payload, clip_start, clip_end)
    record = DownloadRecord(
        market=market,
        data_type=data_type,
        symbol=symbol,
        interval=interval,
        year_month=ym,
        url=url,
        bytes=len(payload),
        sha256=digest,
        checksum_available=available,
        checksum_passed=passed,
        rows=len(parsed),
    )
    return market, data_type, symbol, parsed, asdict(record)


def validate_klines(frame: pd.DataFrame, symbol: str, interval: str, market: str) -> dict[str, object]:
    expected = pd.Timedelta(interval)
    diffs = frame.index.to_series().diff().dropna()
    invalid = (
        (frame.high < frame[["open", "close", "low"]].max(axis=1))
        | (frame.low > frame[["open", "close", "high"]].min(axis=1))
        | (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | (frame[["volume", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote"]] < 0).any(axis=1)
    )
    if invalid.any():
        raise ValueError(f"{market}/{symbol}: invalid OHLCV rows={int(invalid.sum())}")
    return {
        "market": market,
        "data_type": "klines",
        "symbol": symbol,
        "interval": interval,
        "rows": int(len(frame)),
        "start": frame.index.min().isoformat(),
        "end": frame.index.max().isoformat(),
        "duplicates": int(frame.index.duplicated().sum()),
        "irregular_intervals": int((diffs != expected).sum()),
        "largest_gap_hours": float(diffs.max().total_seconds() / 3600.0) if len(diffs) else 0.0,
        "null_values": int(frame.isna().sum().sum()),
    }


def validate_funding(series: pd.Series, symbol: str) -> dict[str, object]:
    diffs = series.index.to_series().diff().dropna()
    return {
        "market": "futures_um",
        "data_type": "fundingRate",
        "symbol": symbol,
        "interval": "variable",
        "rows": int(len(series)),
        "start": series.index.min().isoformat() if len(series) else None,
        "end": series.index.max().isoformat() if len(series) else None,
        "duplicates": int(series.index.duplicated().sum()),
        "irregular_intervals": int((~diffs.isin([pd.Timedelta(hours=4), pd.Timedelta(hours=8)])).sum()) if len(diffs) else 0,
        "largest_gap_hours": float(diffs.max().total_seconds() / 3600.0) if len(diffs) else 0.0,
        "null_values": int(series.isna().sum()),
        "mean_rate": float(series.mean()) if len(series) else np.nan,
        "max_abs_rate": float(series.abs().max()) if len(series) else np.nan,
    }


def load_all(config: ResearchConfig, cache: Path, refresh: bool = False):
    tasks: list[tuple[str, str, str, str, pd.Timestamp]] = []
    for month in iter_months(config.start, config.end_exclusive):
        for symbol in config.symbols:
            tasks.extend(
                [
                    ("spot", "klines", symbol, config.spot_interval, month),
                    ("futures_um", "klines", symbol, config.perp_interval, month),
                    ("futures_um", "fundingRate", symbol, "none", month),
                ]
            )

    grouped: dict[tuple[str, str, str], list[pd.DataFrame | pd.Series]] = {}
    records: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(download_one, market, dtype, symbol, interval, month, config, cache, refresh)
            for market, dtype, symbol, interval, month in tasks
        ]
        for number, future in enumerate(as_completed(futures), start=1):
            market, dtype, symbol, parsed, record = future.result()
            grouped.setdefault((market, dtype, symbol), []).append(parsed)
            records.append(record)
            if number % 50 == 0 or number == len(futures):
                print(f"archives processed: {number}/{len(futures)}")

    spot: dict[str, pd.DataFrame] = {}
    perp: dict[str, pd.DataFrame] = {}
    funding: dict[str, pd.Series] = {}
    quality: list[dict[str, object]] = []
    for symbol in config.symbols:
        spot_frame = pd.concat(grouped[("spot", "klines", symbol)]).sort_index()
        spot_frame = spot_frame[~spot_frame.index.duplicated(keep="last")]
        perp_frame = pd.concat(grouped[("futures_um", "klines", symbol)]).sort_index()
        perp_frame = perp_frame[~perp_frame.index.duplicated(keep="last")]
        funding_series = pd.concat(grouped[("futures_um", "fundingRate", symbol)]).sort_index()
        funding_series = funding_series.groupby(level=0).sum()
        spot[symbol] = spot_frame
        perp[symbol] = perp_frame
        funding[symbol] = funding_series
        quality.append(validate_klines(spot_frame, symbol, config.spot_interval, "spot"))
        quality.append(validate_klines(perp_frame, symbol, config.perp_interval, "futures_um"))
        quality.append(validate_funding(funding_series, symbol))
        print(
            f"{symbol}: spot={len(spot_frame):,}, perp={len(perp_frame):,}, "
            f"funding={len(funding_series):,}"
        )
    records.sort(key=lambda row: (row["market"], row["data_type"], row["symbol"], row["year_month"]))
    return spot, perp, funding, records, quality
