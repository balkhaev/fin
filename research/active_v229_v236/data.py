from __future__ import annotations

import csv
import hashlib
import io
import json
import time
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

from config import (
    ASSETS,
    BAR_HOURS,
    END,
    PERP_SYMBOLS,
    START,
)

KLINE_BASE = "https://data.binance.vision/data/futures/um/monthly/klines"
FUNDING_BASE = "https://data.binance.vision/data/futures/um/monthly/fundingRate"
KLINE_COLUMNS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_base",
    "taker_buy_quote",
    "ignore",
)


@dataclass(slots=True)
class ContractData:
    symbol: str
    expiry: pd.Timestamp
    frame: pd.DataFrame


@dataclass(slots=True)
class AssetData:
    asset: str
    perp: pd.DataFrame
    funding: pd.DataFrame
    contracts: list[ContractData]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def session() -> requests.Session:
    client = requests.Session()
    client.headers.update(
        {
            "User-Agent": "fin-research-v230/1.0 (+https://github.com/balkhaev/fin)",
            "Accept": "application/zip,text/plain,*/*",
        }
    )
    return client


def get(client: requests.Session, url: str) -> requests.Response:
    last: Exception | None = None
    for attempt in range(6):
        try:
            response = client.get(url, timeout=90)
            if response.status_code in {429, 500, 502, 503, 504}:
                time.sleep(min(20.0, 1.5 * (attempt + 1)))
                continue
            return response
        except requests.RequestException as exc:
            last = exc
            time.sleep(min(20.0, 1.5 * (attempt + 1)))
    if last is not None:
        raise last
    raise RuntimeError(url)


def last_friday(year: int, month: int) -> date:
    if month == 12:
        cursor = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        cursor = date(year, month + 1, 1) - timedelta(days=1)
    while cursor.weekday() != 4:
        cursor -= timedelta(days=1)
    return cursor


def expiries() -> list[date]:
    end = pd.Timestamp(END).date()
    result = []
    for year in range(2021, 2027):
        for month in (3, 6, 9, 12):
            value = last_friday(year, month)
            if value <= end:
                result.append(value)
    return result


def month_strings(start: str = START, end: str = END) -> list[str]:
    return [
        str(value)
        for value in pd.period_range(
            pd.Timestamp(start).to_period("M"),
            pd.Timestamp(end).to_period("M"),
            freq="M",
        )
    ]


def contract_months(expiry: date) -> list[str]:
    start = pd.Timestamp(expiry) - pd.Timedelta(days=185)
    return [
        str(value)
        for value in pd.period_range(
            start.to_period("M"), pd.Timestamp(expiry).to_period("M"), freq="M"
        )
    ]


def _timestamp_ms(value: Any) -> int:
    number = int(float(str(value)))
    if number > 10**15:
        number //= 1000
    if number < 10**11:
        number *= 1000
    return number


def parse_kline_zip(payload: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise ValueError(f"expected one CSV, got {names}")
        raw = archive.read(names[0])
    frame = pd.read_csv(io.BytesIO(raw), header=None, low_memory=False)
    if frame.empty:
        return pd.DataFrame(columns=["timestamp", "open", "close"])
    if frame.shape[1] < 5:
        raise ValueError(f"unexpected kline width: {frame.shape[1]}")
    frame = frame.iloc[:, : min(frame.shape[1], 12)].copy()
    frame.columns = KLINE_COLUMNS[: frame.shape[1]]
    first = str(frame.iloc[0]["open_time"]).strip().lower()
    try:
        _timestamp_ms(first)
    except (ValueError, TypeError):
        frame = frame.iloc[1:].copy()
    result = pd.DataFrame()
    result["timestamp"] = pd.to_datetime(
        frame["open_time"].map(_timestamp_ms), unit="ms", utc=True, errors="coerce"
    )
    result["open"] = pd.to_numeric(frame["open"], errors="coerce")
    result["close"] = pd.to_numeric(frame["close"], errors="coerce")
    result = result.dropna(subset=["timestamp", "open", "close"])
    result = result[(result.open > 0) & (result.close > 0)]
    return result.drop_duplicates("timestamp", keep="last").sort_values("timestamp")


def parse_funding_zip(payload: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise ValueError(f"expected one CSV, got {names}")
        raw = archive.read(names[0])
    frame = pd.read_csv(io.BytesIO(raw), header=None, low_memory=False)
    if frame.empty:
        return pd.DataFrame(columns=["timestamp", "rate", "interval_hours"])
    if frame.shape[1] != 3:
        raise ValueError(f"unexpected funding width: {frame.shape[1]}")
    frame.columns = ["calc_time", "interval_hours", "rate"]
    first = str(frame.iloc[0]["calc_time"]).lower()
    if "time" in first or "calc" in first:
        frame = frame.iloc[1:].copy()
    result = pd.DataFrame()
    result["timestamp"] = pd.to_datetime(
        frame["calc_time"].map(_timestamp_ms), unit="ms", utc=True, errors="coerce"
    )
    result["interval_hours"] = pd.to_numeric(
        frame["interval_hours"], errors="coerce"
    ).fillna(8.0)
    result["rate"] = pd.to_numeric(frame["rate"], errors="coerce")
    result = result.dropna(subset=["timestamp", "rate"])
    return result.drop_duplicates("timestamp", keep="last").sort_values("timestamp")


def _download_archives(
    symbol: str,
    months: list[str],
    dataset: str,
    cache: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    cache.mkdir(parents=True, exist_ok=True)
    suffix = "funding" if dataset == "funding" else "1h"
    target = cache / f"{symbol}_{suffix}.csv"
    meta_path = cache / f"{symbol}_{suffix}_provenance.json"
    if target.exists() and meta_path.exists():
        frame = pd.read_csv(target, parse_dates=["timestamp"])
        frame["timestamp"] = pd.to_datetime(
            frame["timestamp"], utc=True, format="mixed", errors="coerce"
        )
        return frame, json.loads(meta_path.read_text())

    client = session()
    parts: list[pd.DataFrame] = []
    requests_meta: list[dict[str, Any]] = []
    for month in months:
        if dataset == "funding":
            filename = f"{symbol}-fundingRate-{month}.zip"
            url = f"{FUNDING_BASE}/{symbol}/{filename}"
        else:
            filename = f"{symbol}-1h-{month}.zip"
            url = f"{KLINE_BASE}/{symbol}/1h/{filename}"
        row: dict[str, Any] = {"month": month, "url": url}
        try:
            checksum = get(client, url + ".CHECKSUM")
            archive = get(client, url)
            row["checksum_status"] = checksum.status_code
            row["http_status"] = archive.status_code
            row["bytes"] = len(archive.content)
            if checksum.status_code != 200 or archive.status_code != 200:
                row["valid"] = False
                row["reason"] = "missing_archive_or_checksum"
            else:
                expected = checksum.text.strip().split()[0].lower()
                actual = sha256_bytes(archive.content)
                row["archive_sha256"] = actual
                row["checksum_valid"] = expected == actual
                frame = (
                    parse_funding_zip(archive.content)
                    if dataset == "funding"
                    else parse_kline_zip(archive.content)
                )
                row["rows"] = len(frame)
                row["valid"] = bool(row["checksum_valid"] and not frame.empty)
                row["reason"] = "ok" if row["valid"] else "checksum_or_parse_failure"
                if row["valid"]:
                    parts.append(frame)
        except Exception as exc:  # noqa: BLE001
            row["valid"] = False
            row["reason"] = f"{type(exc).__name__}: {exc}"
        requests_meta.append(row)
        time.sleep(0.025)

    if parts:
        frame = pd.concat(parts, ignore_index=True)
        frame = frame.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
    else:
        columns = ["timestamp", "rate", "interval_hours"] if dataset == "funding" else ["timestamp", "open", "close"]
        frame = pd.DataFrame(columns=columns)
    start = pd.Timestamp(START, tz="UTC")
    end = pd.Timestamp(END, tz="UTC") + pd.Timedelta(days=1)
    if not frame.empty:
        frame = frame[(frame.timestamp >= start) & (frame.timestamp < end)].copy()
    provenance = {
        "dataset": dataset,
        "symbol": symbol,
        "attempted_months": len(months),
        "valid_months": int(sum(bool(item.get("valid")) for item in requests_meta)),
        "rows": len(frame),
        "timestamp_min": frame.timestamp.min().isoformat() if not frame.empty else None,
        "timestamp_max": frame.timestamp.max().isoformat() if not frame.empty else None,
        "requests": requests_meta,
    }
    frame.to_csv(target, index=False)
    meta_path.write_text(json.dumps(provenance, indent=2) + "\n")
    return frame.reset_index(drop=True), provenance


def download_perp(asset: str, cache: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    return _download_archives(PERP_SYMBOLS[asset], month_strings(), "kline", cache / "perp")


def download_funding(asset: str, cache: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    return _download_archives(PERP_SYMBOLS[asset], month_strings(), "funding", cache / "funding")


def download_contract(
    asset: str, expiry: date, cache: Path
) -> tuple[ContractData, dict[str, Any]]:
    symbol = f"{asset}USDT_{expiry.strftime('%y%m%d')}"
    frame, provenance = _download_archives(
        symbol, contract_months(expiry), "kline", cache / "dated" / asset
    )
    expiry_ts = pd.Timestamp(expiry, tz="UTC") + pd.Timedelta(hours=8)
    return ContractData(symbol=symbol, expiry=expiry_ts, frame=frame), provenance


def load_all(cache: Path) -> tuple[dict[str, AssetData], dict[str, Any]]:
    markets: dict[str, AssetData] = {}
    provenance: dict[str, Any] = {"assets": {}}
    for asset in ASSETS:
        print(f"loading {asset} perpetual and funding", flush=True)
        perp, perp_meta = download_perp(asset, cache)
        funding, funding_meta = download_funding(asset, cache)
        contracts: list[ContractData] = []
        contract_meta: list[dict[str, Any]] = []
        for number, expiry in enumerate(expiries(), start=1):
            contract, meta = download_contract(asset, expiry, cache)
            contracts.append(contract)
            contract_meta.append(
                {
                    "symbol": contract.symbol,
                    "expiry": contract.expiry.isoformat(),
                    **meta,
                }
            )
            if number % 4 == 0:
                print(f"loaded {asset} contracts {number}/{len(expiries())}", flush=True)
        contracts.sort(key=lambda item: item.expiry)
        markets[asset] = AssetData(
            asset=asset,
            perp=perp,
            funding=funding,
            contracts=contracts,
        )
        provenance["assets"][asset] = {
            "perpetual": perp_meta,
            "funding": funding_meta,
            "contracts": contract_meta,
            "valid_contracts": int(sum(not item.frame.empty for item in contracts)),
        }
    return markets, provenance
