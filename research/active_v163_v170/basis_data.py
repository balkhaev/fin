from __future__ import annotations

import csv
import hashlib
import io
import json
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from basis_config import ASSETS, END, START

BINANCE_BASE = "https://data.binance.vision/data/futures/um/monthly/klines"
OKX_BASE = "https://www.okx.com/api/v5/market/history-candles"


@dataclass(slots=True)
class DownloadedMarket:
    asset: str
    frame: pd.DataFrame
    provenance: dict[str, Any]


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _session() -> requests.Session:
    client = requests.Session()
    client.headers.update(
        {
            "User-Agent": "fin-research-v165/1.0 (+https://github.com/balkhaev/fin)",
            "Accept": "application/json,text/plain,application/zip,*/*",
        }
    )
    return client


def _get(client: requests.Session, url: str, params: dict[str, str] | None = None) -> requests.Response:
    last: Exception | None = None
    for attempt in range(5):
        try:
            response = client.get(url, params=params, timeout=60)
            if response.status_code in {429, 500, 502, 503, 504}:
                time.sleep(1.0 + attempt)
                continue
            return response
        except requests.RequestException as exc:
            last = exc
            time.sleep(1.0 + attempt)
    if last:
        raise last
    raise RuntimeError(f"unable to fetch {url}")


def _timestamp_ms(value: Any) -> int:
    number = int(float(str(value)))
    if number > 10**15:
        number //= 1000
    if number < 10**11:
        number *= 1000
    return number


def _months() -> list[str]:
    return [value.strftime("%Y-%m") for value in pd.period_range(START[:7], END[:7], freq="M")]


def _parse_binance_zip(payload: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise ValueError(f"expected one CSV, got {names}")
        text = archive.read(names[0]).decode("utf-8-sig", errors="replace")
    rows = [row for row in csv.reader(io.StringIO(text)) if row]
    if not rows:
        return pd.DataFrame(columns=["timestamp", "open", "close"])
    try:
        _timestamp_ms(rows[0][0])
        data = rows
    except (ValueError, TypeError, IndexError):
        data = rows[1:]
    parsed: list[tuple[pd.Timestamp, float, float]] = []
    for row in data:
        if len(row) < 5:
            continue
        try:
            timestamp = pd.to_datetime(_timestamp_ms(row[0]), unit="ms", utc=True)
            open_price = float(row[1])
            close_price = float(row[4])
        except (ValueError, TypeError, IndexError):
            continue
        if open_price > 0 and close_price > 0:
            parsed.append((timestamp, open_price, close_price))
    return pd.DataFrame(parsed, columns=["timestamp", "open", "close"])


def download_binance(asset: str, cache: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    symbol = f"{asset}USDT"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"binance_{symbol}_1h.csv"
    provenance_path = cache / f"binance_{symbol}_provenance.json"
    if target.exists() and provenance_path.exists():
        frame = pd.read_csv(target, parse_dates=["timestamp"])
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        return frame, json.loads(provenance_path.read_text())

    client = _session()
    parts: list[pd.DataFrame] = []
    requests_meta: list[dict[str, Any]] = []
    for month in _months():
        filename = f"{symbol}-1h-{month}.zip"
        url = f"{BINANCE_BASE}/{symbol}/1h/{filename}"
        meta: dict[str, Any] = {"month": month, "url": url}
        try:
            checksum_response = _get(client, url + ".CHECKSUM")
            archive_response = _get(client, url)
            meta["checksum_status"] = checksum_response.status_code
            meta["http_status"] = archive_response.status_code
            meta["bytes"] = len(archive_response.content)
            if checksum_response.status_code != 200 or archive_response.status_code != 200:
                meta["valid"] = False
            else:
                expected = checksum_response.text.strip().split()[0].lower()
                actual = _sha256(archive_response.content)
                meta["sha256"] = actual
                meta["checksum_valid"] = expected == actual
                frame = _parse_binance_zip(archive_response.content)
                meta["rows"] = len(frame)
                meta["valid"] = bool(meta["checksum_valid"] and not frame.empty)
                if meta["valid"]:
                    parts.append(frame)
        except Exception as exc:  # noqa: BLE001
            meta["valid"] = False
            meta["error"] = f"{type(exc).__name__}: {exc}"
        requests_meta.append(meta)
        time.sleep(0.04)

    if not parts:
        frame = pd.DataFrame(columns=["timestamp", "open", "close"])
    else:
        frame = pd.concat(parts, ignore_index=True)
        frame = frame.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
        start = pd.Timestamp(START, tz="UTC")
        end = pd.Timestamp(END, tz="UTC")
        frame = frame[(frame.timestamp >= start) & (frame.timestamp <= end)].reset_index(drop=True)
    provenance = {
        "venue": "binance_usdm",
        "asset": asset,
        "symbol": symbol,
        "dataset": "checksum_verified_monthly_1h_klines",
        "requests": requests_meta,
        "rows": len(frame),
        "timestamp_min": frame.timestamp.min().isoformat() if not frame.empty else None,
        "timestamp_max": frame.timestamp.max().isoformat() if not frame.empty else None,
    }
    frame.to_csv(target, index=False)
    provenance_path.write_text(json.dumps(provenance, indent=2) + "\n")
    return frame, provenance


def download_okx(asset: str, cache: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    inst = f"{asset}-USDT-SWAP"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"okx_{asset}_1h.csv"
    provenance_path = cache / f"okx_{asset}_provenance.json"
    if target.exists() and provenance_path.exists():
        frame = pd.read_csv(target, parse_dates=["timestamp"])
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        return frame, json.loads(provenance_path.read_text())

    client = _session()
    rows: list[tuple[pd.Timestamp, float, float]] = []
    requests_meta: list[dict[str, Any]] = []
    seen: set[int] = set()
    after: int | None = None
    start_ms = int(pd.Timestamp(START, tz="UTC").timestamp() * 1000)
    for page in range(600):
        params = {"instId": inst, "bar": "1H", "limit": "100"}
        if after is not None:
            params["after"] = str(after)
        response = _get(client, OKX_BASE, params)
        meta: dict[str, Any] = {
            "page": page,
            "url": response.url,
            "status": response.status_code,
            "bytes": len(response.content),
            "sha256": _sha256(response.content),
        }
        requests_meta.append(meta)
        if response.status_code != 200:
            meta["body_prefix"] = response.text[:300]
            break
        payload = response.json()
        meta["api_code"] = payload.get("code") if isinstance(payload, dict) else None
        meta["api_message"] = payload.get("msg") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or str(payload.get("code")) != "0":
            meta["body_prefix"] = response.text[:300]
            break
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            break
        timestamps: list[int] = []
        fresh = 0
        for row in data:
            if not isinstance(row, list) or len(row) < 9:
                continue
            try:
                timestamp_ms = int(row[0])
                open_price = float(row[1])
                close_price = float(row[4])
                confirmed = str(row[8]) == "1"
            except (TypeError, ValueError, IndexError):
                continue
            timestamps.append(timestamp_ms)
            if timestamp_ms in seen or not confirmed or open_price <= 0 or close_price <= 0:
                continue
            seen.add(timestamp_ms)
            fresh += 1
            rows.append(
                (
                    pd.to_datetime(timestamp_ms, unit="ms", utc=True),
                    open_price,
                    close_price,
                )
            )
        if not timestamps or fresh == 0:
            break
        oldest = min(timestamps)
        if oldest <= start_ms:
            break
        if after is not None and oldest >= after:
            break
        after = oldest
        time.sleep(0.12)

    frame = pd.DataFrame(rows, columns=["timestamp", "open", "close"])
    if not frame.empty:
        frame = frame.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
        start = pd.Timestamp(START, tz="UTC")
        end = pd.Timestamp(END, tz="UTC")
        frame = frame[(frame.timestamp >= start) & (frame.timestamp <= end)].reset_index(drop=True)
    provenance = {
        "venue": "okx_usdt_swap",
        "asset": asset,
        "inst_id": inst,
        "dataset": "public_history_candles_1h",
        "requests": requests_meta,
        "rows": len(frame),
        "timestamp_min": frame.timestamp.min().isoformat() if not frame.empty else None,
        "timestamp_max": frame.timestamp.max().isoformat() if not frame.empty else None,
    }
    frame.to_csv(target, index=False)
    provenance_path.write_text(json.dumps(provenance, indent=2) + "\n")
    return frame, provenance


def load_all(cache: Path, output: Path) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    output.mkdir(parents=True, exist_ok=True)
    markets: dict[str, pd.DataFrame] = {}
    provenance: dict[str, Any] = {"assets": {}}
    for asset in ASSETS:
        binance, bprov = download_binance(asset, cache)
        okx, oprov = download_okx(asset, cache)
        merged = binance.merge(okx, on="timestamp", how="inner", suffixes=("_binance", "_okx"))
        merged = merged.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
        merged.to_csv(output / f"{asset}_hourly.csv", index=False)
        markets[asset] = merged
        provenance["assets"][asset] = {
            "binance": bprov,
            "okx": oprov,
            "aligned_rows": len(merged),
            "timestamp_min": merged.timestamp.min().isoformat() if not merged.empty else None,
            "timestamp_max": merged.timestamp.max().isoformat() if not merged.empty else None,
        }
    (output.parent / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    return markets, provenance
