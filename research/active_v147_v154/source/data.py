from __future__ import annotations

import csv
import hashlib
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from config import (
    ARCHIVE_TEMPLATE,
    END_YEAR,
    MODERN_TEMPLATE,
    RESEARCH_END,
    START_YEAR,
    VIX_SPOT_URL,
)
from dates import archive_contract_code, expiry_url_candidates, nominal_vix_expiry

PRICE_COLUMNS = ("Open", "High", "Low", "Close", "Settle", "Change")
NUMERIC_COLUMNS = (*PRICE_COLUMNS, "Total Volume", "EFP", "Open Interest")


@dataclass
class DownloadEvidence:
    year: int
    month: int
    url: str
    status_code: int
    bytes: int
    sha256: str
    expiry: str
    rows: int
    error: str | None = None


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def get_bytes(url: str, attempts: int = 3) -> tuple[int, bytes]:
    headers = {
        "User-Agent": "fin-research-v149/1.0 (+https://github.com/balkhaev/fin)",
        "Accept": "text/csv,text/plain;q=0.9,*/*;q=0.5",
    }
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            response = requests.get(url, headers=headers, timeout=35)
            if response.status_code in {429, 500, 502, 503, 504}:
                time.sleep(1.0 + attempt)
                continue
            return response.status_code, response.content
        except requests.RequestException as exc:
            last = exc
            time.sleep(1.0 + attempt)
    if last:
        raise last
    raise RuntimeError(f"unable to download {url}")


def decode(value: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            pass
    return value.decode("utf-8", errors="replace")


def parse_contract_csv(content: bytes, expiry: str, url: str) -> pd.DataFrame:
    lines = decode(content).splitlines()
    start = next(
        (index for index, line in enumerate(lines[:100]) if line.lstrip("\ufeff").startswith("Trade Date,")),
        None,
    )
    if start is None:
        return pd.DataFrame()
    reader = csv.reader(lines[start:])
    header = [str(value).strip() for value in next(reader)]
    rows = []
    for row in reader:
        if not row or not str(row[0]).strip():
            continue
        row = list(row[: len(header)]) + [""] * max(0, len(header) - len(row))
        rows.append(row[: len(header)])
    frame = pd.DataFrame(rows, columns=header)
    if "Trade Date" not in frame or "Settle" not in frame:
        return pd.DataFrame()
    frame["Trade Date"] = pd.to_datetime(frame["Trade Date"], errors="coerce")
    frame = frame.dropna(subset=["Trade Date"]).copy()
    for column in NUMERIC_COLUMNS:
        if column not in frame:
            frame[column] = pd.NA
        frame[column] = pd.to_numeric(
            frame[column].astype(str).str.replace(",", "", regex=False).str.replace("*", "", regex=False),
            errors="coerce",
        )
    expiry_ts = pd.Timestamp(expiry)
    frame["Expiry"] = expiry_ts
    frame["Source URL"] = url
    frame = frame[(frame["Trade Date"] <= expiry_ts) & (frame["Trade Date"] < pd.Timestamp(RESEARCH_END))]
    # Cboe's earliest files use a ten-times price scale through 2007-03-26.
    old = frame["Trade Date"] <= pd.Timestamp("2007-03-26")
    frame.loc[old, list(PRICE_COLUMNS)] = frame.loc[old, list(PRICE_COLUMNS)] * 0.1
    frame = frame[frame["Settle"].notna() & frame["Settle"].gt(0)]
    return frame


def contract_urls(year: int, month: int) -> list[tuple[str, str]]:
    if year <= 2012:
        code = archive_contract_code(year, month)
        url = ARCHIVE_TEMPLATE.format(month_code=code[0], year2=code[1:])
        return [(url, nominal_vix_expiry(year, month).isoformat())]
    return [
        (MODERN_TEMPLATE.format(expiry=value.isoformat()), value.isoformat())
        for value in expiry_url_candidates(year, month)
    ]


def fetch_contract(year: int, month: int, cache: Path) -> tuple[pd.DataFrame, DownloadEvidence]:
    errors: list[str] = []
    for url, expiry in contract_urls(year, month):
        cache_path = cache / f"{year:04d}_{month:02d}_{expiry}.csv"
        try:
            if cache_path.exists():
                content = cache_path.read_bytes()
                status = 200
            else:
                status, content = get_bytes(url)
                if status == 200:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_bytes(content)
            frame = parse_contract_csv(content, expiry, url) if status == 200 else pd.DataFrame()
            if not frame.empty:
                evidence = DownloadEvidence(
                    year,
                    month,
                    url,
                    status,
                    len(content),
                    sha256_bytes(content),
                    expiry,
                    len(frame),
                )
                return frame, evidence
            errors.append(f"{url}: status={status}, rows={len(frame)}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url}: {exc!r}")
    nominal = nominal_vix_expiry(year, month).isoformat()
    return pd.DataFrame(), DownloadEvidence(
        year,
        month,
        contract_urls(year, month)[0][0],
        0,
        0,
        "",
        nominal,
        0,
        "; ".join(errors),
    )


def collect_contracts(cache: Path, processed: Path, provenance: Path) -> pd.DataFrame:
    work = [
        (year, month)
        for year in range(START_YEAR, END_YEAR + 1)
        for month in range(1, 13)
    ]
    frames: list[pd.DataFrame] = []
    evidence: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {
            pool.submit(fetch_contract, year, month, cache / "contracts"): (year, month)
            for year, month in work
        }
        for count, future in enumerate(as_completed(futures), start=1):
            frame, item = future.result()
            evidence.append(asdict(item))
            if not frame.empty:
                frames.append(frame)
            if count % 24 == 0:
                print(f"downloaded {count}/{len(work)} contract months", flush=True)
    if not frames:
        raise RuntimeError("no official Cboe contract files were collected")
    contracts = pd.concat(frames, ignore_index=True)
    contracts = contracts.sort_values(["Trade Date", "Expiry"])
    contracts = contracts.drop_duplicates(["Trade Date", "Expiry"], keep="last")
    contracts = contracts[
        [
            "Trade Date",
            "Expiry",
            "Futures",
            "Open",
            "High",
            "Low",
            "Close",
            "Settle",
            "Change",
            "Total Volume",
            "EFP",
            "Open Interest",
            "Source URL",
        ]
    ]
    processed.mkdir(parents=True, exist_ok=True)
    provenance.mkdir(parents=True, exist_ok=True)
    contracts.to_csv(processed / "vx_monthly_contracts.csv", index=False)
    (provenance / "contract_downloads.json").write_text(
        json.dumps(sorted(evidence, key=lambda x: (x["year"], x["month"])), indent=2) + "\n"
    )
    return contracts


def collect_vix_spot(cache: Path, processed: Path, provenance: Path) -> pd.DataFrame:
    cache_path = cache / "VIX_History.csv"
    if cache_path.exists():
        content = cache_path.read_bytes()
        status = 200
    else:
        status, content = get_bytes(VIX_SPOT_URL)
        if status == 200:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(content)
    if status != 200:
        raise RuntimeError(f"VIX spot status {status}")
    frame = pd.read_csv(io.BytesIO(content))
    frame.columns = [str(value).strip().upper() for value in frame.columns]
    frame["DATE"] = pd.to_datetime(frame["DATE"], errors="coerce")
    for column in ("OPEN", "HIGH", "LOW", "CLOSE"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["DATE", "CLOSE"]).sort_values("DATE")
    frame = frame[frame["DATE"] < pd.Timestamp(RESEARCH_END)]
    processed.mkdir(parents=True, exist_ok=True)
    frame.to_csv(processed / "vix_spot.csv", index=False)
    (provenance / "vix_spot.json").write_text(
        json.dumps(
            {
                "url": VIX_SPOT_URL,
                "bytes": len(content),
                "sha256": sha256_bytes(content),
                "rows": len(frame),
                "date_min": frame.DATE.min().date().isoformat(),
                "date_max": frame.DATE.max().date().isoformat(),
            },
            indent=2,
        )
        + "\n"
    )
    return frame


def load_or_collect(cache: Path, root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    processed = root / "inputs" / "processed"
    provenance = root / "inputs" / "provenance"
    contract_path = processed / "vx_monthly_contracts.csv"
    spot_path = processed / "vix_spot.csv"
    if contract_path.exists():
        contracts = pd.read_csv(contract_path, parse_dates=["Trade Date", "Expiry"])
    else:
        contracts = collect_contracts(cache, processed, provenance)
    if spot_path.exists():
        spot = pd.read_csv(spot_path, parse_dates=["DATE"])
    else:
        spot = collect_vix_spot(cache, processed, provenance)
    return contracts, spot
