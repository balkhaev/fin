from __future__ import annotations

import hashlib
import io
import json
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import requests

from config import (
    ASSETS,
    BAR_HOURS,
    COIN_M_SYMBOLS,
    END,
    MIN_MONTH_ARCHIVE_SHARE,
    MIN_PRICE_COVERAGE,
    START,
    USD_M_SYMBOLS,
)

BASE = "https://data.binance.vision/data/futures"
THREAD_LOCAL = threading.local()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _session() -> requests.Session:
    client = getattr(THREAD_LOCAL, "client", None)
    if client is None:
        client = requests.Session()
        client.headers.update(
            {
                "User-Agent": "fin-research-v245/1.0 (+https://github.com/balkhaev/fin)",
                "Accept": "application/zip,text/plain,*/*",
            }
        )
        THREAD_LOCAL.client = client
    return client


def _get(url: str, attempts: int = 6) -> requests.Response:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            response = _session().get(url, timeout=90)
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


def _timestamp_ms(value: Any) -> int:
    number = int(float(str(value)))
    if number > 10**15:
        number //= 1000
    if number < 10**11:
        number *= 1000
    return number


def month_strings() -> list[str]:
    start = pd.Timestamp(START).to_period("M")
    end = pd.Timestamp(END).to_period("M")
    return [str(value) for value in pd.period_range(start, end, freq="M")]


def _read_single_csv(payload: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise ValueError(f"expected one CSV in archive, got {names}")
        return archive.read(names[0])


def normalize_kline(payload: bytes, venue: str) -> pd.DataFrame:
    raw = _read_single_csv(payload)
    frame = pd.read_csv(io.BytesIO(raw), header=None)
    if frame.empty:
        return pd.DataFrame(columns=["timestamp", f"open_{venue}", f"close_{venue}"])
    first = str(frame.iloc[0, 0]).strip().lower()
    if any(token in first for token in ("open", "time", "date")):
        frame = frame.iloc[1:].copy()
    if frame.shape[1] < 5:
        raise ValueError(f"unexpected kline width {frame.shape[1]}")
    result = pd.DataFrame()
    result["timestamp"] = pd.to_datetime(
        frame.iloc[:, 0].map(_timestamp_ms), unit="ms", utc=True, errors="coerce"
    )
    result[f"open_{venue}"] = pd.to_numeric(frame.iloc[:, 1], errors="coerce")
    result[f"close_{venue}"] = pd.to_numeric(frame.iloc[:, 4], errors="coerce")
    result = result.dropna().copy()
    result = result[(result[f"open_{venue}"] > 0) & (result[f"close_{venue}"] > 0)]
    return result.drop_duplicates("timestamp", keep="last").sort_values("timestamp")


def normalize_funding(payload: bytes, venue: str) -> pd.DataFrame:
    raw = _read_single_csv(payload)
    frame = pd.read_csv(io.BytesIO(raw), header=None)
    columns = ["timestamp", f"funding_{venue}", f"funding_interval_hours_{venue}"]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    first = str(frame.iloc[0, 0]).strip().lower()
    has_header = any(token in first for token in ("calc", "fund", "time", "date"))
    header: list[str] | None = None
    if has_header:
        header = [str(value).strip().lower() for value in frame.iloc[0].tolist()]
        frame = frame.iloc[1:].copy()
    if frame.empty:
        return pd.DataFrame(columns=columns)

    if header is not None:
        def find_index(candidates: tuple[str, ...]) -> int | None:
            for i, name in enumerate(header):
                if any(candidate in name for candidate in candidates):
                    return i
            return None

        time_i = find_index(("calc_time", "funding_time", "timestamp", "time"))
        rate_i = find_index(("last_funding_rate", "funding_rate", "rate"))
        interval_i = find_index(("funding_interval_hours", "interval"))
    else:
        time_i = 0
        interval_i = 1 if frame.shape[1] >= 3 else None
        rate_i = 2 if frame.shape[1] >= 3 else (1 if frame.shape[1] >= 2 else None)

    if time_i is None or rate_i is None:
        raise ValueError(f"unable to identify funding columns: width={frame.shape[1]}, header={header}")
    result = pd.DataFrame()
    result["timestamp"] = pd.to_datetime(
        frame.iloc[:, time_i].map(_timestamp_ms), unit="ms", utc=True, errors="coerce"
    )
    result[f"funding_{venue}"] = pd.to_numeric(frame.iloc[:, rate_i], errors="coerce")
    if interval_i is not None and interval_i < frame.shape[1]:
        result[f"funding_interval_hours_{venue}"] = pd.to_numeric(
            frame.iloc[:, interval_i], errors="coerce"
        )
    else:
        result[f"funding_interval_hours_{venue}"] = 8.0
    result = result.dropna(subset=["timestamp", f"funding_{venue}"]).copy()
    result[f"funding_interval_hours_{venue}"] = (
        result[f"funding_interval_hours_{venue}"].fillna(8.0).clip(lower=1.0, upper=24.0)
    )
    scheduled = result["timestamp"].dt.round("h")
    jitter = (result["timestamp"] - scheduled).abs()
    if jitter.gt(pd.Timedelta(minutes=30)).any():
        raise ValueError("funding timestamp outside 30-minute schedule tolerance")
    result["timestamp"] = scheduled
    return result.drop_duplicates("timestamp", keep="last").sort_values("timestamp")


def _archive_url(product: str, dataset: str, symbol: str, month: str) -> str:
    if dataset == "klines":
        filename = f"{symbol}-1h-{month}.zip"
        return f"{BASE}/{product}/monthly/klines/{symbol}/1h/{filename}"
    if dataset == "fundingRate":
        filename = f"{symbol}-fundingRate-{month}.zip"
        return f"{BASE}/{product}/monthly/fundingRate/{symbol}/{filename}"
    raise ValueError(dataset)


def _daily_funding_url(product: str, symbol: str, day: str) -> str:
    filename = f"{symbol}-fundingRate-{day}.zip"
    return f"{BASE}/{product}/daily/fundingRate/{symbol}/{filename}"


def _download_daily_funding_one(
    product: str,
    symbol: str,
    day: str,
    normalizer: Callable[[bytes], pd.DataFrame],
) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    url = _daily_funding_url(product, symbol, day)
    meta: dict[str, Any] = {"day": day, "url": url}
    try:
        checksum = _get(url + ".CHECKSUM")
        archive = _get(url)
        meta["checksum_status"] = checksum.status_code
        meta["http_status"] = archive.status_code
        meta["bytes"] = len(archive.content)
        if checksum.status_code != 200 or archive.status_code != 200:
            meta["valid"] = False
            meta["reason"] = "missing_archive_or_checksum"
            return None, meta
        expected = checksum.text.strip().split()[0].lower()
        actual = sha256_bytes(archive.content)
        meta["sha256"] = actual
        meta["checksum_valid"] = expected == actual
        frame = normalizer(archive.content)
        meta["rows"] = len(frame)
        meta["valid"] = bool(meta["checksum_valid"] and not frame.empty)
        meta["reason"] = "ok" if meta["valid"] else "checksum_or_parse_failure"
        return frame if meta["valid"] else None, meta
    except Exception as exc:
        meta["valid"] = False
        meta["reason"] = f"{type(exc).__name__}: {exc}"
        return None, meta


def _reconstruct_daily_funding_month(
    product: str,
    symbol: str,
    venue: str,
    month: str,
    monthly_request: dict[str, Any],
) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    period = pd.Period(month, freq="M")
    days = [
        value.strftime("%Y-%m-%d")
        for value in pd.date_range(period.start_time, period.end_time, freq="1D")
    ]
    normalizer = lambda payload: normalize_funding(payload, venue)
    frames: list[pd.DataFrame] = []
    daily_requests: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(
                _download_daily_funding_one, product, symbol, day, normalizer
            ): day
            for day in days
        }
        for future in as_completed(futures):
            frame, meta = future.result()
            daily_requests.append(meta)
            if frame is not None:
                frames.append(frame)
    daily_requests.sort(key=lambda item: item["day"])
    valid_days = sum(bool(item.get("valid")) for item in daily_requests)
    valid_share = valid_days / len(days) if days else 0.0
    if frames:
        frame = pd.concat(frames, ignore_index=True)
        frame = frame.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
    else:
        frame = pd.DataFrame(
            columns=[
                "timestamp",
                f"funding_{venue}",
                f"funding_interval_hours_{venue}",
            ]
        )
    valid = bool(valid_share >= 0.95 and not frame.empty)
    meta = {
        "month": month,
        "source": "daily_reconstruction",
        "valid": valid,
        "valid_days": valid_days,
        "expected_days": len(days),
        "valid_day_share": valid_share,
        "rows": len(frame),
        "monthly_request": monthly_request,
        "daily_requests": daily_requests,
        "reason": "ok" if valid else "daily_coverage_below_95pct",
    }
    return frame if valid else None, meta


def _download_one(
    product: str,
    dataset: str,
    symbol: str,
    month: str,
    normalizer: Callable[[bytes], pd.DataFrame],
) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    url = _archive_url(product, dataset, symbol, month)
    meta: dict[str, Any] = {"month": month, "url": url, "source": "monthly"}
    try:
        checksum = _get(url + ".CHECKSUM")
        archive = _get(url)
        meta["checksum_status"] = checksum.status_code
        meta["http_status"] = archive.status_code
        meta["bytes"] = len(archive.content)
        if checksum.status_code != 200 or archive.status_code != 200:
            meta["valid"] = False
            meta["reason"] = "missing_archive_or_checksum"
            return None, meta
        expected = checksum.text.strip().split()[0].lower()
        actual = sha256_bytes(archive.content)
        meta["sha256"] = actual
        meta["checksum_valid"] = expected == actual
        frame = normalizer(archive.content)
        meta["rows"] = len(frame)
        meta["valid"] = bool(meta["checksum_valid"] and not frame.empty)
        meta["reason"] = "ok" if meta["valid"] else "checksum_or_parse_failure"
        return frame if meta["valid"] else None, meta
    except Exception as exc:
        meta["valid"] = False
        meta["reason"] = f"{type(exc).__name__}: {exc}"
        return None, meta


def download_dataset(
    *,
    product: str,
    dataset: str,
    symbol: str,
    venue: str,
    cache: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"{product}_{symbol}_{dataset}.csv"
    meta_path = cache / f"{product}_{symbol}_{dataset}_provenance.json"
    if target.exists() and meta_path.exists():
        frame = pd.read_csv(target)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, format="mixed")
        return frame, json.loads(meta_path.read_text())

    if dataset == "klines":
        normalizer = lambda payload: normalize_kline(payload, venue)
    elif dataset == "fundingRate":
        normalizer = lambda payload: normalize_funding(payload, venue)
    else:
        raise ValueError(dataset)

    months = month_strings()
    frames: list[pd.DataFrame] = []
    requests_meta: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {
            executor.submit(_download_one, product, dataset, symbol, month, normalizer): month
            for month in months
        }
        for number, future in enumerate(as_completed(futures), start=1):
            frame, meta = future.result()
            requests_meta.append(meta)
            if frame is not None:
                frames.append(frame)
            if number % 20 == 0:
                print(f"{product} {symbol} {dataset}: {number}/{len(months)}", flush=True)
    requests_meta.sort(key=lambda item: item["month"])
    if product == "cm" and dataset == "fundingRate":
        rebuilt_meta: list[dict[str, Any]] = []
        for monthly_meta in requests_meta:
            if monthly_meta.get("valid"):
                rebuilt_meta.append(monthly_meta)
                continue
            daily_frame, daily_meta = _reconstruct_daily_funding_month(
                product, symbol, venue, monthly_meta["month"], monthly_meta
            )
            rebuilt_meta.append(daily_meta)
            if daily_frame is not None:
                frames.append(daily_frame)
            print(
                f"{product} {symbol} funding daily fallback {monthly_meta['month']}: "
                f"{daily_meta['valid_days']}/{daily_meta['expected_days']}",
                flush=True,
            )
        requests_meta = rebuilt_meta
    if frames:
        frame = pd.concat(frames, ignore_index=True)
        frame = frame.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
    else:
        if dataset == "klines":
            frame = pd.DataFrame(columns=["timestamp", f"open_{venue}", f"close_{venue}"])
        else:
            frame = pd.DataFrame(
                columns=["timestamp", f"funding_{venue}", f"funding_interval_hours_{venue}"]
            )
    start = pd.Timestamp(START, tz="UTC")
    end = pd.Timestamp(END, tz="UTC")
    if not frame.empty:
        frame = frame[(frame.timestamp >= start) & (frame.timestamp <= end)].copy()
    valid_months = [item["month"] for item in requests_meta if item.get("valid")]
    provenance = {
        "product": product,
        "dataset": dataset,
        "symbol": symbol,
        "venue": venue,
        "expected_months": len(months),
        "valid_months": len(valid_months),
        "valid_month_share": len(valid_months) / len(months),
        "valid_month_list": valid_months,
        "requests": requests_meta,
        "rows": len(frame),
        "timestamp_min": frame.timestamp.min().isoformat() if not frame.empty else None,
        "timestamp_max": frame.timestamp.max().isoformat() if not frame.empty else None,
    }
    frame.to_csv(target, index=False)
    meta_path.write_text(json.dumps(provenance, indent=2) + "\n")
    return frame, provenance


def build_asset_frame(
    asset: str,
    usdm_prices: pd.DataFrame,
    coinm_prices: pd.DataFrame,
    usdm_funding: pd.DataFrame,
    coinm_funding: pd.DataFrame,
) -> pd.DataFrame:
    start = pd.Timestamp(START, tz="UTC")
    end = pd.Timestamp(END, tz="UTC").floor("h")
    frame = pd.DataFrame({"timestamp": pd.date_range(start, end, freq=f"{BAR_HOURS}h", tz="UTC")})
    for source in (usdm_prices, coinm_prices, usdm_funding, coinm_funding):
        frame = frame.merge(source, on="timestamp", how="left")
    frame["asset"] = asset
    price_columns = ["open_usdm", "close_usdm", "open_coinm", "close_coinm"]
    frame["price_complete"] = frame[price_columns].notna().all(axis=1)
    for venue in ("usdm", "coinm"):
        rate = f"funding_{venue}"
        interval = f"funding_interval_hours_{venue}"
        frame[f"funding_event_{venue}"] = frame[rate].notna()
        frame[rate] = pd.to_numeric(frame[rate], errors="coerce").fillna(0.0)
        frame[interval] = pd.to_numeric(frame[interval], errors="coerce").fillna(0.0)
    close_ratio = frame.close_coinm / frame.close_usdm
    open_ratio = frame.open_coinm / frame.open_usdm
    frame["basis_close_bps"] = np.where(close_ratio.gt(0), np.log(close_ratio) * 10_000.0, np.nan)
    frame["basis_open_bps"] = np.where(open_ratio.gt(0), np.log(open_ratio) * 10_000.0, np.nan)
    frame["funding_spread_event"] = frame.funding_usdm - frame.funding_coinm
    return frame


def load_all(root: Path, cache: Path) -> tuple[dict[str, pd.DataFrame], dict[str, Any], dict[str, Any]]:
    normalized = root / "inputs" / "normalized"
    normalized.mkdir(parents=True, exist_ok=True)
    markets: dict[str, pd.DataFrame] = {}
    provenance: dict[str, Any] = {"assets": {}}
    coverage_assets: dict[str, Any] = {}
    for asset in ASSETS:
        print(f"loading {asset}", flush=True)
        usdm_symbol = USD_M_SYMBOLS[asset]
        coinm_symbol = COIN_M_SYMBOLS[asset]
        usdm_prices, usdm_price_meta = download_dataset(
            product="um", dataset="klines", symbol=usdm_symbol, venue="usdm", cache=cache
        )
        coinm_prices, coinm_price_meta = download_dataset(
            product="cm", dataset="klines", symbol=coinm_symbol, venue="coinm", cache=cache
        )
        usdm_funding, usdm_funding_meta = download_dataset(
            product="um", dataset="fundingRate", symbol=usdm_symbol, venue="usdm", cache=cache
        )
        coinm_funding, coinm_funding_meta = download_dataset(
            product="cm", dataset="fundingRate", symbol=coinm_symbol, venue="coinm", cache=cache
        )
        frame = build_asset_frame(asset, usdm_prices, coinm_prices, usdm_funding, coinm_funding)
        frame.to_csv(normalized / f"{asset}_hourly.csv", index=False)
        markets[asset] = frame
        dataset_meta = {
            "usdm_prices": usdm_price_meta,
            "coinm_prices": coinm_price_meta,
            "usdm_funding": usdm_funding_meta,
            "coinm_funding": coinm_funding_meta,
        }
        price_coverage = float(frame.price_complete.mean())
        month_shares = {
            key: float(value["valid_month_share"]) for key, value in dataset_meta.items()
        }
        asset_passed = bool(
            price_coverage >= MIN_PRICE_COVERAGE
            and all(value >= MIN_MONTH_ARCHIVE_SHARE for value in month_shares.values())
        )
        coverage_assets[asset] = {
            "rows": len(frame),
            "price_coverage": price_coverage,
            "dataset_month_shares": month_shares,
            "timestamp_min": frame.loc[frame.price_complete, "timestamp"].min().isoformat()
            if frame.price_complete.any()
            else None,
            "timestamp_max": frame.loc[frame.price_complete, "timestamp"].max().isoformat()
            if frame.price_complete.any()
            else None,
            "passed": asset_passed,
        }
        provenance["assets"][asset] = dataset_meta
    gate = {
        "candidate": "V245_DUAL_PERP_DATA_COVERAGE",
        "minimum_month_archive_share": MIN_MONTH_ARCHIVE_SHARE,
        "minimum_price_coverage": MIN_PRICE_COVERAGE,
        "assets": coverage_assets,
        "passed": all(value["passed"] for value in coverage_assets.values()),
    }
    (root / "inputs" / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    return markets, provenance, gate
