from __future__ import annotations

import hashlib
import io
import json
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

from flow_config import (
    DEVELOPMENT_END,
    END,
    FINAL_START,
    HOLDOUT_END,
    HOLDOUT_START,
    MIN_FULL_MONTHS,
    REQUIRED_MONTH_COVERAGE,
    ROLLING_MIN_PERIODS,
    ROLLING_WINDOW_MINUTES,
    START,
    SYMBOLS,
    VALIDATION_END,
    VALIDATION_START,
)

PARENT = Path(__file__).resolve().parents[1] / "active_v197_v204"
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))
import data as depth_data  # noqa: E402

BASE = "https://data.binance.vision/data/futures/cm"
THREAD_LOCAL = threading.local()
KLINE_COLUMNS = (
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
                "User-Agent": "fin-research-v205/1.0 (+https://github.com/balkhaev/fin)",
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


def monthly_url(symbol: str, month: str) -> str:
    filename = f"{symbol}-1m-{month}.zip"
    return f"{BASE}/monthly/klines/{symbol}/1m/{filename}"


def daily_url(symbol: str, day: str) -> str:
    filename = f"{symbol}-1m-{day}.zip"
    return f"{BASE}/daily/klines/{symbol}/1m/{filename}"


def download_one(cache: Path, symbol: str, period: str, url: str) -> Path | None:
    target = cache / symbol / f"{period}.zip"
    meta_path = target.with_suffix(".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and meta_path.exists():
        meta = json.loads(meta_path.read_text())
        if sha256_file(target) == meta.get("sha256"):
            return target
        target.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)

    checksum_response = request(url + ".CHECKSUM")
    archive_response = request(url)
    if checksum_response.status_code == 404 or archive_response.status_code == 404:
        return None
    if checksum_response.status_code != 200 or archive_response.status_code != 200:
        raise RuntimeError(
            f"download failed {symbol} {period}: "
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
    return target


def download_month(cache: Path, symbol: str, month: str) -> list[Path]:
    monthly = download_one(cache, symbol, month, monthly_url(symbol, month))
    if monthly is not None:
        return [monthly]
    outputs: list[Path] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(download_one, cache, symbol, day, daily_url(symbol, day)): day
            for day in month_dates(month)
        }
        for future in as_completed(futures):
            value = future.result()
            if value is not None:
                outputs.append(value)
    return sorted(outputs)


def read_member(path: Path) -> bytes:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise ValueError(f"expected one CSV in {path}, got {names}")
        return archive.read(names[0])


def parse_kline_bytes(raw: bytes) -> pd.DataFrame:
    sample = pd.read_csv(io.BytesIO(raw), nrows=2, header=None)
    first = str(sample.iloc[0, 0]).lower()
    has_header = "open" in first or "time" in first
    frame = pd.read_csv(io.BytesIO(raw), header=0 if has_header else None)
    if not has_header:
        if frame.shape[1] < len(KLINE_COLUMNS):
            raise ValueError(f"unexpected kline width: {frame.shape[1]}")
        frame = frame.iloc[:, : len(KLINE_COLUMNS)]
        frame.columns = KLINE_COLUMNS
    else:
        normalized: dict[str, str] = {}
        for column in frame.columns:
            key = "".join(character for character in str(column).lower() if character.isalnum())
            normalized[key] = str(column)
        mapping = {
            "open_time": normalized.get("opentime"),
            "volume": normalized.get("volume"),
            "taker_buy_volume": normalized.get("takerbuyvolume"),
        }
        if not all(mapping.values()):
            raise ValueError(f"invalid kline header: {frame.columns.tolist()}")
        frame = frame.rename(columns={value: key for key, value in mapping.items()})
    output = frame[["open_time", "volume", "taker_buy_volume"]].copy()
    timestamp = pd.to_numeric(output.open_time, errors="coerce")
    timestamp = timestamp.where(timestamp < 10**15, timestamp / 1000.0)
    output["timestamp"] = pd.to_datetime(timestamp, unit="ms", utc=True, errors="coerce")
    output["volume"] = pd.to_numeric(output.volume, errors="coerce")
    output["taker_buy_volume"] = pd.to_numeric(output.taker_buy_volume, errors="coerce")
    output = output.dropna(subset=["timestamp", "volume", "taker_buy_volume"])
    output = output[(output.volume >= 0) & (output.taker_buy_volume >= 0)]
    return output.drop_duplicates("timestamp", keep="last").set_index("timestamp")[[
        "volume",
        "taker_buy_volume",
    ]]


def load_flow_symbol(
    symbol: str,
    cache: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    provenance: list[dict[str, Any]] = []
    quality: list[dict[str, Any]] = []
    for month in month_strings():
        paths = download_month(cache, symbol, month)
        monthly_frames: list[pd.DataFrame] = []
        for path in paths:
            frame = parse_kline_bytes(read_member(path))
            if not frame.empty:
                monthly_frames.append(frame)
            meta = json.loads(path.with_suffix(".json").read_text())
            provenance.append(meta | {"dataset": "regular_1m_klines"})
        month_frame = (
            pd.concat(monthly_frames).sort_index().loc[
                lambda value: ~value.index.duplicated(keep="last")
            ]
            if monthly_frames
            else pd.DataFrame()
        )
        period = pd.Period(month, freq="M")
        expected = int((period.end_time.date() - period.start_time.date()).days + 1) * 1440
        complete = (
            month_frame[["volume", "taker_buy_volume"]].notna().all(axis=1)
            if not month_frame.empty
            else pd.Series(dtype=bool)
        )
        coverage = float(complete.sum() / expected) if expected else 0.0
        quality.append(
            {
                "symbol": symbol,
                "month": month,
                "expected_minutes": expected,
                "complete_minutes": int(complete.sum()),
                "coverage": coverage,
                "full_month": coverage >= REQUIRED_MONTH_COVERAGE,
                "archive_count": len(paths),
            }
        )
        if not month_frame.empty:
            frames.append(month_frame)
    if not frames:
        return pd.DataFrame(), provenance, quality
    output = pd.concat(frames).sort_index()
    return output.loc[~output.index.duplicated(keep="last")], provenance, quality


def add_flow_features(panel: pd.DataFrame, flow: pd.DataFrame) -> pd.DataFrame:
    frame = panel.join(flow, how="left")
    volume = frame.volume.where(frame.volume > 0)
    frame["taker_imbalance"] = 2.0 * frame.taker_buy_volume / volume - 1.0
    frame["signed_flow"] = 2.0 * frame.taker_buy_volume - frame.volume
    flow_mean = frame.signed_flow.rolling(
        ROLLING_WINDOW_MINUTES, min_periods=ROLLING_MIN_PERIODS
    ).mean()
    flow_std = frame.signed_flow.rolling(
        ROLLING_WINDOW_MINUTES, min_periods=ROLLING_MIN_PERIODS
    ).std(ddof=1)
    frame["flow_z"] = (frame.signed_flow - flow_mean) / flow_std.where(flow_std > 1e-12)
    log_volume = np.log1p(frame.volume.clip(lower=0))
    volume_mean = log_volume.rolling(
        ROLLING_WINDOW_MINUTES, min_periods=ROLLING_MIN_PERIODS
    ).mean()
    volume_std = log_volume.rolling(
        ROLLING_WINDOW_MINUTES, min_periods=ROLLING_MIN_PERIODS
    ).std(ddof=1)
    frame["volume_z"] = (log_volume - volume_mean) / volume_std.where(volume_std > 1e-12)
    frame["impact_bps"] = frame.price_move.abs() * 10_000.0
    frame["flow_quality"] = frame[
        ["volume", "taker_buy_volume", "flow_z", "volume_z"]
    ].notna().all(axis=1)
    frame["quality"] = frame.quality.fillna(False).astype(bool) & frame.flow_quality
    return frame.replace([np.inf, -np.inf], np.nan)


def period_months(start: str, end: str) -> set[str]:
    return {
        str(value)
        for value in pd.period_range(
            pd.Timestamp(start).to_period("M"),
            pd.Timestamp(end).to_period("M"),
            freq="M",
        )
    }


def flow_coverage_gate(quality: list[dict[str, Any]]) -> dict[str, Any]:
    periods = {
        "development": (START, DEVELOPMENT_END),
        "validation": (VALIDATION_START, VALIDATION_END),
        "holdout": (HOLDOUT_START, HOLDOUT_END),
        "final": (FINAL_START, END),
    }
    symbols: dict[str, Any] = {}
    passed = True
    for symbol in SYMBOLS:
        values = [row for row in quality if row["symbol"] == symbol]
        rows: dict[str, Any] = {}
        for name, (start, end) in periods.items():
            months = period_months(start, end)
            selected = [row for row in values if row["month"] in months]
            full = sum(bool(row["full_month"]) for row in selected)
            required = MIN_FULL_MONTHS[name]
            value = {
                "months": len(months),
                "full_months": full,
                "required_full_months": required,
                "minimum_coverage": min([row["coverage"] for row in selected], default=0.0),
                "median_coverage": float(
                    np.median([row["coverage"] for row in selected]) if selected else 0.0
                ),
                "passed": full >= required,
            }
            rows[name] = value
            passed = passed and value["passed"]
        symbols[symbol] = {"periods": rows}
    return {
        "candidate": "V205_TAKER_FLOW_COVERAGE",
        "required_month_coverage": REQUIRED_MONTH_COVERAGE,
        "symbols": symbols,
        "passed": passed,
    }


def load_all(cache: Path) -> tuple[
    dict[str, pd.DataFrame],
    dict[str, Any],
    pd.DataFrame,
    dict[str, Any],
]:
    depth_panels, depth_provenance, depth_quality, depth_gate = depth_data.load_all(
        cache / "depth"
    )
    panels: dict[str, pd.DataFrame] = {}
    flow_provenance: list[dict[str, Any]] = []
    flow_quality: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        flow, provenance, quality = load_flow_symbol(symbol, cache / "flow")
        panels[symbol] = add_flow_features(depth_panels[symbol], flow)
        flow_provenance.extend(provenance)
        flow_quality.extend(quality)
    flow_gate = flow_coverage_gate(flow_quality)
    gate = {
        "candidate": "V206_FLOW_DEPTH_PANEL_COVERAGE",
        "depth_gate": depth_gate,
        "flow_gate": flow_gate,
        "passed": bool(depth_gate["passed"] and flow_gate["passed"]),
    }
    provenance = {
        "candidate": "V206_CAUSAL_FLOW_DEPTH_PANEL",
        "depth": depth_provenance,
        "flow_archives": flow_provenance,
        "feature_contract": {
            "signal_information": "completed depth, volume and taker_buy_volume only",
            "execution": "next-minute mark open plus frozen cost floor",
        },
    }
    quality = pd.concat(
        [
            depth_quality.assign(dataset="depth_mark"),
            pd.DataFrame(flow_quality).assign(dataset="regular_kline_flow"),
        ],
        ignore_index=True,
        sort=False,
    )
    return panels, provenance, quality, gate
