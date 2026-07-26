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

from config import ASSETS, BLOCK_HOURS, END, START

BINANCE_FUNDING_BASE = (
    "https://data.binance.vision/data/futures/um/monthly/fundingRate"
)
HYPERLIQUID_INFO = "https://api.hyperliquid.xyz/info"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _session() -> requests.Session:
    client = requests.Session()
    client.headers.update(
        {
            "User-Agent": "fin-research-v171/1.0 (+https://github.com/balkhaev/fin)",
            "Accept": "application/json,text/plain,application/zip,*/*",
            "Content-Type": "application/json",
        }
    )
    return client


def _request(
    client: requests.Session,
    method: str,
    url: str,
    *,
    params: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    attempts: int = 6,
) -> requests.Response:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            response = client.request(
                method,
                url,
                params=params,
                json=payload,
                timeout=90,
            )
            if response.status_code in {429, 500, 502, 503, 504}:
                time.sleep(min(20.0, 1.5 * (attempt + 1)))
                continue
            return response
        except requests.RequestException as exc:
            last = exc
            time.sleep(min(20.0, 1.5 * (attempt + 1)))
    if last is not None:
        raise last
    raise RuntimeError(f"request failed: {method} {url}")


def _timestamp_ms(value: Any) -> int:
    number = int(float(str(value)))
    if number > 10**15:
        number //= 1000
    if number < 10**11:
        number *= 1000
    return number


def _month_strings() -> list[str]:
    start = pd.Timestamp(START).to_period("M")
    end = pd.Timestamp(END).to_period("M")
    return [str(value) for value in pd.period_range(start, end, freq="M")]


def _read_csv_from_zip(payload: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise ValueError(f"expected one CSV in archive, got {names}")
        raw = archive.read(names[0])
    # Read without assuming a header.  Binance archives have changed header
    # conventions over time; normalization below removes a textual header row
    # when present and preserves the first observation when absent.
    return pd.read_csv(io.BytesIO(raw), header=None)


def _normalize_binance_funding(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["timestamp", "rate", "interval_hours"])
    frame = frame.copy()
    frame.columns = [str(value).strip().lower() for value in frame.columns]
    expected = {"calc_time", "funding_interval_hours", "last_funding_rate"}
    if not expected.issubset(frame.columns):
        if frame.shape[1] != 3:
            raise ValueError(f"unexpected Binance funding columns: {list(frame.columns)}")
        frame.columns = ["calc_time", "funding_interval_hours", "last_funding_rate"]
        first = str(frame.iloc[0, 0]).lower()
        if "calc" in first or "time" in first:
            frame = frame.iloc[1:].copy()
    result = pd.DataFrame()
    result["timestamp"] = pd.to_datetime(
        frame["calc_time"].map(_timestamp_ms), unit="ms", utc=True, errors="coerce"
    )
    result["rate"] = pd.to_numeric(frame["last_funding_rate"], errors="coerce")
    result["interval_hours"] = pd.to_numeric(
        frame["funding_interval_hours"], errors="coerce"
    )
    result = result.dropna(subset=["timestamp", "rate"]).copy()
    result["interval_hours"] = result["interval_hours"].fillna(BLOCK_HOURS)
    return result.drop_duplicates("timestamp", keep="last").sort_values("timestamp")


def download_binance_funding(
    asset: str, cache: Path
) -> tuple[pd.DataFrame, dict[str, Any]]:
    cache.mkdir(parents=True, exist_ok=True)
    symbol = f"{asset}USDT"
    target = cache / f"binance_{symbol}_funding.csv"
    meta_path = cache / f"binance_{symbol}_funding_provenance.json"
    if target.exists() and meta_path.exists():
        frame = pd.read_csv(target, parse_dates=["timestamp"])
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, format="mixed")
        return frame, json.loads(meta_path.read_text())

    client = _session()
    parts: list[pd.DataFrame] = []
    requests_meta: list[dict[str, Any]] = []
    for month in _month_strings():
        filename = f"{symbol}-fundingRate-{month}.zip"
        url = f"{BINANCE_FUNDING_BASE}/{symbol}/{filename}"
        meta: dict[str, Any] = {"month": month, "url": url}
        try:
            checksum_response = _request(client, "GET", url + ".CHECKSUM")
            archive_response = _request(client, "GET", url)
            meta["checksum_status"] = checksum_response.status_code
            meta["http_status"] = archive_response.status_code
            meta["bytes"] = len(archive_response.content)
            if checksum_response.status_code != 200 or archive_response.status_code != 200:
                meta["valid"] = False
            else:
                expected = checksum_response.text.strip().split()[0].lower()
                actual = sha256_bytes(archive_response.content)
                meta["sha256"] = actual
                meta["checksum_valid"] = expected == actual
                frame = _normalize_binance_funding(
                    _read_csv_from_zip(archive_response.content)
                )
                meta["rows"] = len(frame)
                meta["valid"] = bool(meta["checksum_valid"] and not frame.empty)
                if meta["valid"]:
                    parts.append(frame)
        except Exception as exc:  # noqa: BLE001
            meta["valid"] = False
            meta["error"] = f"{type(exc).__name__}: {exc}"
        requests_meta.append(meta)
        time.sleep(0.04)

    if parts:
        frame = pd.concat(parts, ignore_index=True)
        frame = frame.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
    else:
        frame = pd.DataFrame(columns=["timestamp", "rate", "interval_hours"])
    start = pd.Timestamp(START, tz="UTC")
    end = pd.Timestamp(END, tz="UTC")
    if not frame.empty:
        frame = frame[(frame.timestamp >= start) & (frame.timestamp <= end)].copy()
    provenance = {
        "venue": "binance_usdm",
        "asset": asset,
        "symbol": symbol,
        "dataset": "checksum_verified_monthly_fundingRate",
        "requests": requests_meta,
        "rows": len(frame),
        "timestamp_min": frame.timestamp.min().isoformat() if not frame.empty else None,
        "timestamp_max": frame.timestamp.max().isoformat() if not frame.empty else None,
    }
    frame.to_csv(target, index=False)
    meta_path.write_text(json.dumps(provenance, indent=2) + "\n")
    return frame, provenance


def download_hyperliquid_funding(
    asset: str, cache: Path
) -> tuple[pd.DataFrame, dict[str, Any]]:
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"hyperliquid_{asset}_funding.csv"
    meta_path = cache / f"hyperliquid_{asset}_funding_provenance.json"
    if target.exists() and meta_path.exists():
        frame = pd.read_csv(target, parse_dates=["timestamp"])
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, format="mixed")
        return frame, json.loads(meta_path.read_text())

    client = _session()
    start_ms = int(pd.Timestamp(START, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(END, tz="UTC").timestamp() * 1000)
    cursor = start_ms
    rows: list[dict[str, Any]] = []
    requests_meta: list[dict[str, Any]] = []
    seen: set[int] = set()
    for page in range(250):
        payload = {
            "type": "fundingHistory",
            "coin": asset,
            "startTime": cursor,
            "endTime": end_ms,
        }
        response = _request(client, "POST", HYPERLIQUID_INFO, payload=payload)
        meta: dict[str, Any] = {
            "page": page,
            "start_time": cursor,
            "end_time": end_ms,
            "status": response.status_code,
            "bytes": len(response.content),
            "sha256": sha256_bytes(response.content),
        }
        requests_meta.append(meta)
        if response.status_code != 200:
            meta["body_prefix"] = response.text[:300]
            break
        body = response.json()
        if not isinstance(body, list) or not body:
            meta["rows"] = 0
            break
        fresh_times: list[int] = []
        for item in body:
            if not isinstance(item, dict):
                continue
            try:
                timestamp = int(item["time"])
                rate = float(item["fundingRate"])
                premium = float(item["premium"]) if item.get("premium") is not None else None
            except (KeyError, TypeError, ValueError):
                continue
            if timestamp in seen or timestamp < start_ms or timestamp > end_ms:
                continue
            seen.add(timestamp)
            fresh_times.append(timestamp)
            rows.append(
                {
                    "timestamp": pd.to_datetime(timestamp, unit="ms", utc=True),
                    "rate": rate,
                    "premium": premium,
                }
            )
        meta["rows"] = len(fresh_times)
        if not fresh_times:
            break
        next_cursor = max(fresh_times) + 1
        if next_cursor <= cursor:
            meta["pagination_stalled"] = True
            break
        cursor = next_cursor
        if cursor > end_ms or len(body) < 500:
            break
        # fundingHistory is heavily weighted; stay below the documented IP budget.
        time.sleep(2.7)

    frame = pd.DataFrame(rows, columns=["timestamp", "rate", "premium"])
    if not frame.empty:
        frame = frame.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
    provenance = {
        "venue": "hyperliquid",
        "asset": asset,
        "dataset": "public_fundingHistory",
        "requests": requests_meta,
        "rows": len(frame),
        "timestamp_min": frame.timestamp.min().isoformat() if not frame.empty else None,
        "timestamp_max": frame.timestamp.max().isoformat() if not frame.empty else None,
    }
    frame.to_csv(target, index=False)
    meta_path.write_text(json.dumps(provenance, indent=2) + "\n")
    return frame, provenance


def download_hyperliquid_candles(
    asset: str, cache: Path
) -> tuple[pd.DataFrame, dict[str, Any]]:
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"hyperliquid_{asset}_8h.csv"
    meta_path = cache / f"hyperliquid_{asset}_8h_provenance.json"
    if target.exists() and meta_path.exists():
        frame = pd.read_csv(target, parse_dates=["timestamp"])
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, format="mixed")
        return frame, json.loads(meta_path.read_text())

    start_ms = int(pd.Timestamp(START, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(END, tz="UTC").timestamp() * 1000)
    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": asset,
            "interval": "8h",
            "startTime": start_ms,
            "endTime": end_ms,
        },
    }
    client = _session()
    response = _request(client, "POST", HYPERLIQUID_INFO, payload=payload)
    if response.status_code != 200:
        raise RuntimeError(
            f"Hyperliquid candleSnapshot {asset}: {response.status_code} {response.text[:300]}"
        )
    body = response.json()
    rows: list[dict[str, Any]] = []
    if isinstance(body, list):
        for item in body:
            if not isinstance(item, dict):
                continue
            try:
                timestamp = pd.to_datetime(int(item["t"]), unit="ms", utc=True)
                open_price = float(item["o"])
                close_price = float(item["c"])
            except (KeyError, TypeError, ValueError):
                continue
            if open_price > 0 and close_price > 0:
                rows.append(
                    {
                        "timestamp": timestamp,
                        "open_hyperliquid": open_price,
                        "close_hyperliquid": close_price,
                    }
                )
    frame = pd.DataFrame(
        rows, columns=["timestamp", "open_hyperliquid", "close_hyperliquid"]
    )
    if not frame.empty:
        frame = frame.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
    provenance = {
        "venue": "hyperliquid",
        "asset": asset,
        "dataset": "public_candleSnapshot_8h",
        "request": payload,
        "status": response.status_code,
        "bytes": len(response.content),
        "sha256": sha256_bytes(response.content),
        "rows": len(frame),
        "timestamp_min": frame.timestamp.min().isoformat() if not frame.empty else None,
        "timestamp_max": frame.timestamp.max().isoformat() if not frame.empty else None,
    }
    frame.to_csv(target, index=False)
    meta_path.write_text(json.dumps(provenance, indent=2) + "\n")
    return frame, provenance


def load_binance_prices(asset: str, v165_processed: Path) -> pd.DataFrame:
    path = v165_processed / f"{asset}_hourly.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    frame = pd.read_csv(path, usecols=["timestamp", "open_binance", "close_binance"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, format="mixed")
    frame = frame.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
    frame = frame[
        frame.timestamp.dt.minute.eq(0)
        & frame.timestamp.dt.second.eq(0)
        & frame.timestamp.dt.hour.mod(BLOCK_HOURS).eq(0)
    ].copy()
    return frame


def _funding_blocks(
    frame: pd.DataFrame, venue: str
) -> pd.DataFrame:
    output_columns = [
        "timestamp",
        f"funding_{venue}",
        f"funding_count_{venue}",
        f"funding_hours_{venue}",
        f"funding_max_jitter_seconds_{venue}",
    ]
    if frame.empty:
        return pd.DataFrame(columns=output_columns)

    values = frame.copy()
    values["timestamp"] = pd.to_datetime(
        values["timestamp"], utc=True, errors="coerce", format="mixed"
    )
    values["rate"] = pd.to_numeric(values["rate"], errors="coerce")
    values = values.dropna(subset=["timestamp", "rate"]).copy()
    if values.empty:
        return pd.DataFrame(columns=output_columns)

    # Exchange archives stamp scheduled payments a few milliseconds late (and
    # early Hyperliquid records can be tens of minutes late).  A raw floor
    # therefore splits economically identical 08:00 payments between adjacent
    # blocks.  Snap only observations within a strict half-hour tolerance.
    values["scheduled_timestamp"] = values["timestamp"].dt.round("h")
    jitter = (values["timestamp"] - values["scheduled_timestamp"]).abs()
    if jitter.gt(pd.Timedelta(minutes=30)).any():
        bad = values.loc[
            jitter.gt(pd.Timedelta(minutes=30)),
            ["timestamp", "scheduled_timestamp"],
        ].head(5)
        raise ValueError(f"funding timestamp outside schedule tolerance: {bad}")
    values["jitter_seconds"] = jitter.dt.total_seconds()
    values = values.drop_duplicates("scheduled_timestamp", keep="last")
    values = values.sort_values("scheduled_timestamp").reset_index(drop=True)

    if "interval_hours" in values.columns:
        interval = pd.to_numeric(values["interval_hours"], errors="coerce")
    else:
        # Hyperliquid changed from 8-hour to hourly payments in the retained
        # history.  Infer the local schedule from the nearest valid neighbour;
        # using the minimum neighbour gap prevents a missing event from being
        # mistaken for a longer, fully covered interval.
        scheduled = values["scheduled_timestamp"]
        previous = scheduled.diff().dt.total_seconds().div(3600.0)
        following = scheduled.shift(-1).sub(scheduled).dt.total_seconds().div(3600.0)
        neighbours = pd.concat([previous, following], axis=1)
        neighbours = neighbours.where(neighbours.gt(0))
        interval = neighbours.min(axis=1, skipna=True)

    values["coverage_hours"] = (
        interval.fillna(float(BLOCK_HOURS))
        .clip(lower=1.0, upper=float(BLOCK_HOURS))
        .round(6)
    )

    # A payment stamped at T settles the interval ending at T.  After schedule
    # normalization, subtracting one nanosecond assigns it to [T-8h, T).
    values["block"] = (
        values["scheduled_timestamp"] - pd.Timedelta(nanoseconds=1)
    ).dt.floor(f"{BLOCK_HOURS}h")
    grouped = values.groupby("block", as_index=False).agg(
        rate=("rate", "sum"),
        count=("rate", "count"),
        coverage_hours=("coverage_hours", "sum"),
        max_jitter_seconds=("jitter_seconds", "max"),
    )
    return grouped.rename(
        columns={
            "block": "timestamp",
            "rate": f"funding_{venue}",
            "count": f"funding_count_{venue}",
            "coverage_hours": f"funding_hours_{venue}",
            "max_jitter_seconds": f"funding_max_jitter_seconds_{venue}",
        }
    )[output_columns]

def build_asset_frame(
    asset: str,
    binance_funding: pd.DataFrame,
    hyperliquid_funding: pd.DataFrame,
    binance_prices: pd.DataFrame,
    hyperliquid_prices: pd.DataFrame,
) -> pd.DataFrame:
    start = pd.Timestamp(START, tz="UTC").floor(f"{BLOCK_HOURS}h")
    end = pd.Timestamp(END, tz="UTC").floor(f"{BLOCK_HOURS}h")
    index = pd.DataFrame(
        {"timestamp": pd.date_range(start, end, freq=f"{BLOCK_HOURS}h", tz="UTC")}
    )
    frame = index.merge(binance_prices, on="timestamp", how="left")
    frame = frame.merge(hyperliquid_prices, on="timestamp", how="left")
    frame = frame.merge(_funding_blocks(binance_funding, "binance"), on="timestamp", how="left")
    frame = frame.merge(
        _funding_blocks(hyperliquid_funding, "hyperliquid"),
        on="timestamp",
        how="left",
    )
    frame["asset"] = asset
    required_hours = float(BLOCK_HOURS) - 1e-6
    frame["funding_complete"] = (
        frame.funding_hours_binance.fillna(0).ge(required_hours)
        & frame.funding_hours_hyperliquid.fillna(0).ge(required_hours)
    )
    frame["price_complete"] = frame[
        ["open_binance", "open_hyperliquid"]
    ].notna().all(axis=1)
    # Log relative venue basis, available at the decision/entry boundary.
    ratio = frame.open_hyperliquid / frame.open_binance
    frame["basis_bps"] = np.where(
        ratio.gt(0), np.log(ratio) * 10_000.0, np.nan
    )
    frame["funding_spread"] = frame.funding_binance - frame.funding_hyperliquid
    return frame


def load_all(
    root: Path,
    cache: Path,
    v165_processed: Path,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    normalized = root / "inputs" / "normalized"
    normalized.mkdir(parents=True, exist_ok=True)
    markets: dict[str, pd.DataFrame] = {}
    provenance: dict[str, Any] = {"assets": {}}
    for asset in ASSETS:
        print(f"loading {asset}", flush=True)
        bin_funding, bin_funding_meta = download_binance_funding(asset, cache / "binance")
        hl_funding, hl_funding_meta = download_hyperliquid_funding(
            asset, cache / "hyperliquid"
        )
        hl_prices, hl_price_meta = download_hyperliquid_candles(
            asset, cache / "hyperliquid"
        )
        bin_prices = load_binance_prices(asset, v165_processed)
        frame = build_asset_frame(
            asset,
            bin_funding,
            hl_funding,
            bin_prices,
            hl_prices,
        )
        frame.to_csv(normalized / f"{asset}_8h.csv", index=False)
        markets[asset] = frame
        usable = frame.price_complete & frame.funding_complete
        provenance["assets"][asset] = {
            "binance_funding": bin_funding_meta,
            "hyperliquid_funding": hl_funding_meta,
            "hyperliquid_prices": hl_price_meta,
            "binance_price_source": str(v165_processed / f"{asset}_hourly.csv"),
            "rows": len(frame),
            "usable_rows": int(usable.sum()),
            "price_coverage": float(frame.price_complete.mean()),
            "funding_coverage": float(frame.funding_complete.mean()),
            "usable_timestamp_min": (
                frame.loc[usable, "timestamp"].min().isoformat() if usable.any() else None
            ),
            "usable_timestamp_max": (
                frame.loc[usable, "timestamp"].max().isoformat() if usable.any() else None
            ),
        }
    (root / "inputs" / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n"
    )
    return markets, provenance
