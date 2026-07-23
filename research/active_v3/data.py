from __future__ import annotations

import hashlib
import io
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from config import ResearchConfig

RAW_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
]
NUMERIC = ["open", "high", "low", "close", "volume", "quote_volume", "trades", "taker_buy_quote"]


@dataclass(frozen=True)
class DownloadRecord:
    symbol: str
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


def request_bytes(url: str, retries: int = 5, timeout: int = 90) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "fin-active-v3/1.0"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                break
            time.sleep(min(30, 2**attempt))
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
    magnitude = float(values.abs().median())
    if magnitude >= 1e17:
        return "ns"
    if magnitude >= 1e14:
        return "us"
    if magnitude >= 1e11:
        return "ms"
    return "s"


def parse_zip(payload: bytes, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = sorted(name for name in archive.namelist() if name.lower().endswith(".csv"))
        if not names:
            raise ValueError("archive contains no CSV")
        raw = archive.read(names[0])
    frame = pd.read_csv(io.BytesIO(raw), header=None, names=RAW_COLUMNS, low_memory=False)
    frame["open_time"] = pd.to_datetime(
        pd.to_numeric(frame["open_time"], errors="coerce"),
        unit=timestamp_unit(frame["open_time"]), utc=True, errors="coerce",
    )
    for column in NUMERIC:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["open_time", *NUMERIC])
    frame = frame[(frame.open_time >= start) & (frame.open_time < end)]
    frame = frame.sort_values("open_time", kind="mergesort").drop_duplicates("open_time", keep="last")
    return frame.set_index("open_time")[NUMERIC].astype(float)


def download_symbol(symbol: str, config: ResearchConfig, cache: Path, refresh: bool = False):
    frames: list[pd.DataFrame] = []
    records: list[DownloadRecord] = []
    end = utc(config.end_exclusive)
    for month in iter_months(config.start, config.end_exclusive):
        next_month = month + pd.offsets.MonthBegin(1)
        clip_start, clip_end = max(month, utc(config.start)), min(next_month, end)
        ym = month.strftime("%Y-%m")
        filename = f"{symbol}-{config.source_interval}-{ym}.zip"
        url = (
            "https://data.binance.vision/data/spot/monthly/klines/"
            f"{symbol}/{config.source_interval}/{filename}"
        )
        path = cache / symbol / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        if refresh or not path.exists():
            path.write_bytes(request_bytes(url))
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        available = False
        passed: bool | None = None
        try:
            expected = parse_checksum(request_bytes(url + ".CHECKSUM", retries=2, timeout=30))
            if expected:
                available = True
                passed = expected == digest
                if not passed:
                    raise ValueError(f"checksum mismatch: {filename}")
        except RuntimeError:
            pass
        parsed = parse_zip(payload, clip_start, clip_end)
        frames.append(parsed)
        records.append(DownloadRecord(symbol, ym, url, len(payload), digest, available, passed, len(parsed)))
    frame = pd.concat(frames).sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    return frame, [asdict(item) for item in records]


def validate_15m(frame: pd.DataFrame, symbol: str) -> dict[str, object]:
    expected = pd.Timedelta(minutes=15)
    diffs = frame.index.to_series().diff().dropna()
    invalid = (
        (frame.high < frame[["open", "close", "low"]].max(axis=1))
        | (frame.low > frame[["open", "close", "high"]].min(axis=1))
        | (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | (frame[["volume", "quote_volume", "trades", "taker_buy_quote"]] < 0).any(axis=1)
    )
    if invalid.any():
        raise ValueError(f"{symbol}: invalid OHLCV rows={int(invalid.sum())}")
    return {
        "symbol": symbol,
        "rows": int(len(frame)),
        "start": frame.index.min().isoformat(),
        "end": frame.index.max().isoformat(),
        "duplicates": int(frame.index.duplicated().sum()),
        "irregular_intervals": int((diffs != expected).sum()),
        "largest_gap_minutes": float(diffs.max().total_seconds() / 60),
        "null_values": int(frame.isna().sum().sum()),
    }


def to_daily(frame: pd.DataFrame) -> pd.DataFrame:
    daily = frame.resample("1D", label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
        "volume": "sum", "quote_volume": "sum", "trades": "sum", "taker_buy_quote": "sum",
    }).dropna()
    return daily


def load_all(config: ResearchConfig, cache: Path, refresh: bool = False):
    daily: dict[str, pd.DataFrame] = {}
    records: list[dict[str, object]] = []
    quality: list[dict[str, object]] = []
    for symbol in config.symbols:
        frame, manifest = download_symbol(symbol, config, cache, refresh)
        quality.append(validate_15m(frame, symbol))
        daily[symbol] = to_daily(frame)
        records.extend(manifest)
        print(f"{symbol}: {len(frame):,} 15m rows -> {len(daily[symbol]):,} daily bars")
    return daily, records, quality
