#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ASSETS = ("BTC", "ETH", "SOL", "XRP", "DOGE")
BINANCE_MONTHS = ("2021-01", "2023-01", "2025-01", "2026-06")
BINANCE_BASE = "https://data.binance.vision/data/futures/um/monthly"
OKX_BASE = "https://www.okx.com/api/v5"
TARGET_START_MS = int(datetime(2021, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def session() -> requests.Session:
    client = requests.Session()
    client.headers.update(
        {
            "User-Agent": "fin-research-v164/1.0 (+https://github.com/balkhaev/fin)",
            "Accept": "application/json,text/plain,application/zip,*/*",
        }
    )
    return client


def get(client: requests.Session, url: str, params: dict[str, str] | None = None) -> requests.Response:
    last: Exception | None = None
    for attempt in range(4):
        try:
            response = client.get(url, params=params, timeout=45)
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


def timestamp_iso(value: Any) -> str | None:
    try:
        number = int(float(str(value)))
    except (TypeError, ValueError):
        return None
    if number > 10**15:
        number //= 1000
    if number < 10**11:
        number *= 1000
    try:
        return datetime.fromtimestamp(number / 1000, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def inspect_zip(payload: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise ValueError(f"expected one file, got {names}")
        raw = archive.read(names[0])
    text = raw.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    nonempty = [row for row in rows if any(cell.strip() for cell in row)]
    if not nonempty:
        return {"filename": names[0], "row_count": 0, "raw_sha256": sha256(raw)}
    first = nonempty[0]
    has_header = not timestamp_iso(first[0])
    data = nonempty[1:] if has_header else nonempty
    timestamps = [value for row in data if row and (value := timestamp_iso(row[0]))]
    return {
        "filename": names[0],
        "zip_bytes": len(payload),
        "zip_sha256": sha256(payload),
        "raw_bytes": len(raw),
        "raw_sha256": sha256(raw),
        "has_header": has_header,
        "columns": first if has_header else [],
        "row_count": len(data),
        "timestamp_min": min(timestamps) if timestamps else None,
        "timestamp_max": max(timestamps) if timestamps else None,
        "sample_rows": data[:3],
    }


def probe_binance(client: requests.Session) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for asset in ASSETS:
        symbol = f"{asset}USDT"
        for month in BINANCE_MONTHS:
            paths = {
                "fundingRate": f"fundingRate/{symbol}/{symbol}-fundingRate-{month}.zip",
                "markPriceKlines": f"markPriceKlines/{symbol}/8h/{symbol}-8h-{month}.zip",
            }
            for dataset, suffix in paths.items():
                url = f"{BINANCE_BASE}/{suffix}"
                item: dict[str, Any] = {
                    "venue": "binance",
                    "asset": asset,
                    "symbol": symbol,
                    "month": month,
                    "dataset": dataset,
                    "url": url,
                }
                try:
                    checksum_response = get(client, url + ".CHECKSUM")
                    zip_response = get(client, url)
                    item["checksum_status"] = checksum_response.status_code
                    item["http_status"] = zip_response.status_code
                    item["content_type"] = zip_response.headers.get("content-type")
                    if checksum_response.status_code != 200 or zip_response.status_code != 200:
                        item["valid"] = False
                    else:
                        expected = checksum_response.text.strip().split()[0].lower()
                        actual = sha256(zip_response.content)
                        item["checksum_expected"] = expected
                        item["checksum_actual"] = actual
                        item["checksum_valid"] = expected == actual
                        item["inspection"] = inspect_zip(zip_response.content)
                        item["valid"] = bool(item["checksum_valid"] and item["inspection"]["row_count"] > 0)
                except Exception as exc:  # noqa: BLE001
                    item["valid"] = False
                    item["error"] = f"{type(exc).__name__}: {exc}"
                records.append(item)
                time.sleep(0.05)
    valid = [item for item in records if item.get("valid")]
    return {
        "records": records,
        "valid_count": len(valid),
        "total_count": len(records),
        "valid_assets": sorted({item["asset"] for item in valid}),
        "valid_datasets": sorted({item["dataset"] for item in valid}),
    }


def okx_pages(
    client: requests.Session,
    endpoint: str,
    params: dict[str, str],
    *,
    timestamp_key: str | int,
    max_pages: int,
) -> tuple[list[Any], list[dict[str, Any]]]:
    rows: list[Any] = []
    requests_meta: list[dict[str, Any]] = []
    after: int | None = None
    seen: set[str] = set()
    for page in range(max_pages):
        query = dict(params)
        if after is not None:
            query["after"] = str(after)
        response = get(client, f"{OKX_BASE}/{endpoint}", query)
        meta = {
            "page": page,
            "url": response.url,
            "status": response.status_code,
            "bytes": len(response.content),
            "sha256": sha256(response.content),
        }
        requests_meta.append(meta)
        if response.status_code != 200:
            meta["body_prefix"] = response.text[:300]
            break
        payload = response.json()
        page_rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(page_rows, list) or not page_rows:
            break
        fresh = 0
        timestamps: list[int] = []
        for row in page_rows:
            key = json.dumps(row, sort_keys=True, separators=(",", ":"))
            if key not in seen:
                seen.add(key)
                rows.append(row)
                fresh += 1
            try:
                raw = row[timestamp_key] if isinstance(timestamp_key, int) else row[timestamp_key]
                timestamps.append(int(raw))
            except (KeyError, IndexError, TypeError, ValueError):
                pass
        if not timestamps or fresh == 0:
            break
        oldest = min(timestamps)
        if oldest <= TARGET_START_MS:
            break
        if after is not None and oldest >= after:
            break
        after = oldest
        time.sleep(0.12)
    return rows, requests_meta


def probe_okx(client: requests.Session) -> dict[str, Any]:
    assets: dict[str, Any] = {}
    for asset in ASSETS:
        inst = f"{asset}-USDT-SWAP"
        item: dict[str, Any] = {"inst_id": inst}
        try:
            funding, funding_requests = okx_pages(
                client,
                "public/funding-rate-history",
                {"instId": inst, "limit": "400"},
                timestamp_key="fundingTime",
                max_pages=20,
            )
            funding_ts = sorted(
                {int(row["fundingTime"]) for row in funding if isinstance(row, dict) and row.get("fundingTime")}
            )
            item["funding"] = {
                "row_count": len(funding),
                "timestamp_min": timestamp_iso(funding_ts[0]) if funding_ts else None,
                "timestamp_max": timestamp_iso(funding_ts[-1]) if funding_ts else None,
                "covers_2021": bool(funding_ts and funding_ts[0] <= TARGET_START_MS),
                "sample_rows": funding[:3],
                "requests": funding_requests,
            }
        except Exception as exc:  # noqa: BLE001
            item["funding"] = {"row_count": 0, "error": f"{type(exc).__name__}: {exc}"}
        try:
            marks, mark_requests = okx_pages(
                client,
                "market/history-mark-price-candles",
                {"instId": inst, "bar": "8H", "limit": "100"},
                timestamp_key=0,
                max_pages=70,
            )
            mark_ts = sorted({int(row[0]) for row in marks if isinstance(row, list) and row})
            item["mark_price_8h"] = {
                "row_count": len(marks),
                "timestamp_min": timestamp_iso(mark_ts[0]) if mark_ts else None,
                "timestamp_max": timestamp_iso(mark_ts[-1]) if mark_ts else None,
                "covers_2021": bool(mark_ts and mark_ts[0] <= TARGET_START_MS),
                "sample_rows": marks[:3],
                "requests": mark_requests,
            }
        except Exception as exc:  # noqa: BLE001
            item["mark_price_8h"] = {"row_count": 0, "error": f"{type(exc).__name__}: {exc}"}
        assets[asset] = item
    valid_assets = [
        asset
        for asset, item in assets.items()
        if item["funding"].get("row_count", 0) > 0
        and item["mark_price_8h"].get("row_count", 0) > 0
    ]
    full_assets = [
        asset
        for asset, item in assets.items()
        if item["funding"].get("covers_2021")
        and item["mark_price_8h"].get("covers_2021")
    ]
    return {
        "assets": assets,
        "valid_assets": valid_assets,
        "covers_2021_assets": full_assets,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    client = session()
    binance = probe_binance(client)
    okx = probe_okx(client)
    binance_assets = set(binance["valid_assets"])
    okx_assets = set(okx["valid_assets"])
    paired = sorted(binance_assets & okx_assets)
    paired_2021 = sorted(binance_assets & set(okx["covers_2021_assets"]))
    summary = {
        "candidate": "V164_HISTORICAL_FUNDING_ACCESS_PROBE",
        "as_of_utc": datetime.now(timezone.utc).isoformat(),
        "binance_valid_assets": sorted(binance_assets),
        "okx_valid_assets": sorted(okx_assets),
        "paired_assets": paired,
        "paired_assets_with_okx_2021_coverage": paired_2021,
        "full_historical_download_permitted": len(paired) >= 2,
        "status": "historical_pair_access_confirmed" if len(paired) >= 2 else "historical_pair_access_insufficient",
        "live_ready": False,
        "real_leverage_authorized": False,
        "profitability_proven": False,
    }
    payloads = {
        "binance_access.json": binance,
        "okx_access.json": okx,
        "summary.json": summary,
    }
    manifest: dict[str, Any] = {"files": {}}
    for name, value in payloads.items():
        data = json.dumps(value, indent=2, ensure_ascii=False, default=str).encode("utf-8") + b"\n"
        (args.output / name).write_bytes(data)
        manifest["files"][name] = {"bytes": len(data), "sha256": sha256(data)}
    (args.output / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
