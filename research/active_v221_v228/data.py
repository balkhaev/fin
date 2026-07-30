from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

from config import ASSETS, BAR_MINUTES, END, START, SYMBOLS

BASE = "https://data.binance.vision/data/futures/um/monthly/klines"
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


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _session() -> requests.Session:
    client = requests.Session()
    client.headers.update(
        {
            "User-Agent": "fin-research-v221/1.0 (+https://github.com/balkhaev/fin)",
            "Accept": "application/json,text/plain,application/zip,*/*",
        }
    )
    return client


def _get(client: requests.Session, url: str) -> requests.Response:
    last: Exception | None = None
    for attempt in range(6):
        try:
            response = client.get(url, timeout=90)
            if response.status_code in {429, 500, 502, 503, 504}:
                time.sleep(min(15.0, 1.5 * (attempt + 1)))
                continue
            return response
        except requests.RequestException as exc:
            last = exc
            time.sleep(min(15.0, 1.5 * (attempt + 1)))
    if last is not None:
        raise last
    raise RuntimeError(f"request failed: {url}")


def month_strings() -> list[str]:
    start = pd.Timestamp(START).to_period("M")
    end = pd.Timestamp(END).to_period("M")
    return [str(value) for value in pd.period_range(start, end, freq="M")]


def _parse_timestamp(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    finite = values.dropna()
    if finite.empty:
        return pd.to_datetime(values, unit="ms", utc=True, errors="coerce")
    unit = "us" if float(finite.median()) > 1e14 else "ms"
    return pd.to_datetime(values, unit=unit, utc=True, errors="coerce")


def parse_kline_zip(payload: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise ValueError(f"expected one CSV, got {names}")
        raw = archive.read(names[0])
    frame = pd.read_csv(io.BytesIO(raw), header=None, low_memory=False)
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "timestamp",
                "open",
                "close",
                "quote_volume",
                "taker_buy_quote",
            ]
        )
    if frame.shape[1] < 11:
        raise ValueError(f"unexpected kline width: {frame.shape[1]}")
    frame = frame.iloc[:, :12].copy()
    frame.columns = KLINE_COLUMNS[: frame.shape[1]]
    first = str(frame.iloc[0]["open_time"]).strip().lower()
    if not first.replace(".", "", 1).isdigit():
        frame = frame.iloc[1:].copy()
    result = pd.DataFrame()
    result["timestamp"] = _parse_timestamp(frame["open_time"])
    for column in ("open", "close", "quote_volume", "taker_buy_quote"):
        result[column] = pd.to_numeric(frame[column], errors="coerce")
    result = result.dropna(subset=["timestamp", "open", "close"]).copy()
    result = result[(result.open > 0) & (result.close > 0)]
    result = result.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
    return result.reset_index(drop=True)


def download_market(
    asset: str,
    cache: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    symbol = SYMBOLS[asset]
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"{symbol}_{BAR_MINUTES}m.csv"
    metadata = cache / f"{symbol}_{BAR_MINUTES}m_provenance.json"
    if target.exists() and metadata.exists():
        frame = pd.read_csv(target, parse_dates=["timestamp"])
        frame["timestamp"] = pd.to_datetime(
            frame["timestamp"], utc=True, format="mixed", errors="coerce"
        )
        return frame, json.loads(metadata.read_text())

    client = _session()
    interval = f"{BAR_MINUTES}m"
    parts: list[pd.DataFrame] = []
    requests_meta: list[dict[str, Any]] = []
    for month in month_strings():
        filename = f"{symbol}-{interval}-{month}.zip"
        url = f"{BASE}/{symbol}/{interval}/{filename}"
        meta: dict[str, Any] = {"month": month, "url": url}
        try:
            checksum_response = _get(client, url + ".CHECKSUM")
            archive_response = _get(client, url)
            meta["checksum_status"] = checksum_response.status_code
            meta["http_status"] = archive_response.status_code
            meta["bytes"] = len(archive_response.content)
            if checksum_response.status_code != 200 or archive_response.status_code != 200:
                meta["valid"] = False
                meta["reason"] = "missing_archive_or_checksum"
            else:
                expected = checksum_response.text.strip().split()[0].lower()
                actual = sha256_bytes(archive_response.content)
                meta["sha256"] = actual
                meta["checksum_valid"] = expected == actual
                frame = parse_kline_zip(archive_response.content)
                meta["rows"] = len(frame)
                meta["valid"] = bool(meta["checksum_valid"] and not frame.empty)
                meta["reason"] = "ok" if meta["valid"] else "checksum_or_parse_failure"
                if meta["valid"]:
                    parts.append(frame)
        except Exception as exc:  # noqa: BLE001
            meta["valid"] = False
            meta["reason"] = f"{type(exc).__name__}: {exc}"
        requests_meta.append(meta)
        time.sleep(0.025)

    if parts:
        frame = pd.concat(parts, ignore_index=True)
        frame = frame.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
    else:
        frame = pd.DataFrame(
            columns=[
                "timestamp",
                "open",
                "close",
                "quote_volume",
                "taker_buy_quote",
            ]
        )
    start = pd.Timestamp(START, tz="UTC")
    end = pd.Timestamp(END, tz="UTC")
    if not frame.empty:
        frame = frame[(frame.timestamp >= start) & (frame.timestamp <= end)].copy()
    provenance = {
        "market": "binance_usdm",
        "asset": asset,
        "symbol": symbol,
        "interval": interval,
        "dataset": "checksum_verified_monthly_klines",
        "requests": requests_meta,
        "valid_months": int(sum(bool(item.get("valid")) for item in requests_meta)),
        "attempted_months": len(requests_meta),
        "rows": len(frame),
        "timestamp_min": frame.timestamp.min().isoformat() if not frame.empty else None,
        "timestamp_max": frame.timestamp.max().isoformat() if not frame.empty else None,
    }
    frame.to_csv(target, index=False)
    metadata.write_text(json.dumps(provenance, indent=2) + "\n")
    return frame.reset_index(drop=True), provenance


def build_panel(raw: dict[str, pd.DataFrame]) -> pd.DataFrame:
    start = pd.Timestamp(START, tz="UTC").floor(f"{BAR_MINUTES}min")
    end = pd.Timestamp(END, tz="UTC").floor(f"{BAR_MINUTES}min")
    panel = pd.DataFrame(
        {"timestamp": pd.date_range(start, end, freq=f"{BAR_MINUTES}min", tz="UTC")}
    )
    for asset in ASSETS:
        frame = raw[asset].copy().rename(
            columns={
                "open": f"open_{asset.lower()}",
                "close": f"close_{asset.lower()}",
                "quote_volume": f"quote_volume_{asset.lower()}",
                "taker_buy_quote": f"taker_buy_quote_{asset.lower()}",
            }
        )
        panel = panel.merge(frame, on="timestamp", how="left")
        quote = pd.to_numeric(panel[f"quote_volume_{asset.lower()}"], errors="coerce")
        buy = pd.to_numeric(panel[f"taker_buy_quote_{asset.lower()}"], errors="coerce")
        panel[f"flow_{asset.lower()}"] = np.where(
            quote > 0,
            2.0 * buy / quote - 1.0,
            np.nan,
        )
    required = []
    for asset in ASSETS:
        suffix = asset.lower()
        required.extend([f"open_{suffix}", f"close_{suffix}", f"flow_{suffix}"])
    panel["complete"] = panel[required].notna().all(axis=1)
    return panel


def load_all(cache: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    raw: dict[str, pd.DataFrame] = {}
    provenance: dict[str, Any] = {"assets": {}}
    for asset in ASSETS:
        print(f"loading {asset} USD-M 5m", flush=True)
        frame, meta = download_market(asset, cache)
        raw[asset] = frame
        provenance["assets"][asset] = meta
    panel = build_panel(raw)
    complete = panel.complete.astype(bool)
    provenance["panel"] = {
        "rows": len(panel),
        "complete_rows": int(complete.sum()),
        "coverage": float(complete.mean()),
        "complete_timestamp_min": (
            panel.loc[complete, "timestamp"].min().isoformat() if complete.any() else None
        ),
        "complete_timestamp_max": (
            panel.loc[complete, "timestamp"].max().isoformat() if complete.any() else None
        ),
    }
    return panel, provenance
