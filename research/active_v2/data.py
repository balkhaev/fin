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

import numpy as np
import pandas as pd

from config import ResearchConfig

RAW_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trades",
    "taker_buy_base",
    "taker_buy_quote",
    "ignore",
]
NUMERIC = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trades",
    "taker_buy_quote",
]


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
        current = current + pd.offsets.MonthBegin(1)


def request_bytes(url: str, retries: int = 5, timeout: int = 90) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "fin-active-research-v2/1.0", "Accept": "*/*"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
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
    return value if len(value) == 64 and all(c in "0123456789abcdef" for c in value) else None


def timestamp_unit(values: pd.Series) -> str:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        raise ValueError("archive contains no valid timestamps")
    magnitude = float(numeric.abs().median())
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
        unit=timestamp_unit(frame["open_time"]),
        utc=True,
        errors="coerce",
    )
    for column in NUMERIC:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["open_time", *NUMERIC])
    frame = frame[(frame["open_time"] >= start) & (frame["open_time"] < end)]
    frame = frame.sort_values("open_time", kind="mergesort").drop_duplicates("open_time", keep="last")
    return frame.set_index("open_time")[NUMERIC].astype(float)


def download_symbol(
    symbol: str,
    config: ResearchConfig,
    cache_dir: Path,
    refresh: bool = False,
) -> tuple[pd.DataFrame, list[DownloadRecord]]:
    frames: list[pd.DataFrame] = []
    records: list[DownloadRecord] = []
    end = utc(config.end_exclusive)
    for month in iter_months(config.start, config.end_exclusive):
        next_month = month + pd.offsets.MonthBegin(1)
        clip_start, clip_end = max(month, utc(config.start)), min(next_month, end)
        ym = month.strftime("%Y-%m")
        filename = f"{symbol}-{config.interval}-{ym}.zip"
        url = (
            "https://data.binance.vision/data/spot/monthly/klines/"
            f"{symbol}/{config.interval}/{filename}"
        )
        path = cache_dir / symbol / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        if refresh or not path.exists():
            path.write_bytes(request_bytes(url))
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        checksum_available = False
        checksum_passed: bool | None = None
        try:
            expected = parse_checksum(request_bytes(url + ".CHECKSUM", retries=2, timeout=30))
            if expected:
                checksum_available = True
                checksum_passed = expected == digest
                if not checksum_passed:
                    raise ValueError(f"checksum mismatch for {filename}")
        except RuntimeError:
            pass
        month_frame = parse_zip(payload, clip_start, clip_end)
        frames.append(month_frame)
        records.append(
            DownloadRecord(
                symbol=symbol,
                year_month=ym,
                url=url,
                bytes=len(payload),
                sha256=digest,
                checksum_available=checksum_available,
                checksum_passed=checksum_passed,
                rows=len(month_frame),
            )
        )
        print(f"{symbol} {ym}: {len(month_frame):,} bars")
    data = pd.concat(frames).sort_index()
    data = data[~data.index.duplicated(keep="last")]
    return data, records


def validate(frame: pd.DataFrame, symbol: str, minutes: int = 15) -> dict[str, object]:
    expected = pd.Timedelta(minutes=minutes)
    diffs = frame.index.to_series().diff().dropna()
    irregular = diffs[diffs != expected]
    invalid = (
        (frame["high"] < frame[["open", "close", "low"]].max(axis=1))
        | (frame["low"] > frame[["open", "close", "high"]].min(axis=1))
        | (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | (frame[["volume", "quote_volume", "trades", "taker_buy_quote"]] < 0).any(axis=1)
    )
    if invalid.any():
        raise ValueError(f"{symbol}: {int(invalid.sum())} invalid rows")
    return {
        "symbol": symbol,
        "rows": int(len(frame)),
        "start": frame.index.min().isoformat(),
        "end": frame.index.max().isoformat(),
        "duplicates": int(frame.index.duplicated().sum()),
        "irregular_intervals": int(len(irregular)),
        "largest_gap_minutes": float(diffs.max().total_seconds() / 60) if len(diffs) else 0.0,
        "null_values": int(frame.isna().sum().sum()),
    }


def aggregate(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    temp = frame.copy()
    temp.index = temp.index + pd.Timedelta(minutes=15)
    grouped = temp.resample(rule, label="right", closed="right", origin="epoch").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "quote_volume": "sum",
            "trades": "sum",
            "taker_buy_quote": "sum",
        }
    ).dropna()
    grouped.index = grouped.index - pd.Timedelta(minutes=15)
    return grouped


def build_base_features(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    prev = out["close"].shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev).abs(),
            (out["low"] - prev).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr14"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    range_ = (out["high"] - out["low"]).replace(0, np.nan)
    out["bar_location"] = ((out["close"] - out["low"]) / range_).clip(0, 1)
    out["taker_ratio"] = (out["taker_buy_quote"] / out["quote_volume"].replace(0, np.nan)).clip(0, 1)
    out["volume_baseline"] = out["quote_volume"].rolling(96 * 14, min_periods=96 * 3).median().shift(1)
    out["volume_ratio"] = out["quote_volume"] / out["volume_baseline"].replace(0, np.nan)
    for bars in (2, 4):
        ret = out["close"].pct_change(bars)
        sigma = ret.rolling(96 * 30, min_periods=96 * 10).std().shift(1)
        out[f"ret_{bars}"] = ret
        out[f"shock_z_{bars}"] = ret / sigma.replace(0, np.nan)
        out[f"low_{bars}"] = out["low"].rolling(bars, min_periods=bars).min()
    return out


def load_all(config: ResearchConfig, cache_dir: Path, refresh: bool = False):
    raw: dict[str, pd.DataFrame] = {}
    records: list[dict[str, object]] = []
    quality: list[dict[str, object]] = []
    for symbol in config.symbols:
        frame, manifest = download_symbol(symbol, config, cache_dir, refresh)
        quality.append(validate(frame, symbol))
        raw[symbol] = frame
        records.extend(asdict(item) for item in manifest)
    return raw, records, quality
