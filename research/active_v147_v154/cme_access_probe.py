#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import requests

BASE = "https://www.cmegroup.com"
SETTLEMENT_TEMPLATE = BASE + "/CmeWS/mvc/Settlements/Futures/Settlements/{product_id}/FUT"

# Candidate IDs are discovery hints only. Every ID is verified by an actual
# official CME settlement response before it is accepted by the probe.
PRODUCTS: dict[str, dict[str, Any]] = {
    "ES": {
        "name": "E-mini S&P 500",
        "page": "/markets/equities/sp/e-mini-sandp500.html",
        "candidate_ids": [138],
    },
    "NQ": {
        "name": "E-mini Nasdaq-100",
        "page": "/markets/equities/nasdaq/e-mini-nasdaq-100.html",
        "candidate_ids": [209],
    },
    "ZN": {
        "name": "10-Year U.S. Treasury Note",
        "page": "/markets/interest-rates/us-treasury/10-year-us-treasury-note.html",
        "candidate_ids": [316],
    },
    "GC": {
        "name": "Gold",
        "page": "/markets/metals/precious/gold.html",
        "candidate_ids": [437],
    },
    "CL": {
        "name": "WTI Crude Oil",
        "page": "/markets/energy/crude-oil/light-sweet-crude.html",
        "candidate_ids": [425],
    },
    "NG": {
        "name": "Henry Hub Natural Gas",
        "page": "/markets/energy/natural-gas/natural-gas.html",
        "candidate_ids": [444],
    },
    "6E": {
        "name": "Euro FX",
        "page": "/markets/fx/g10/euro-fx.html",
        "candidate_ids": [58],
    },
}

# One historical and one recent completed trade date are enough for the access
# gate. A later full collector will use a complete business-day calendar.
PROBE_DATES = ("03/26/2020", "07/24/2026")
ID_PATTERNS = (
    re.compile(r'"productId"\s*:\s*"?(\d+)"?'),
    re.compile(r"productId\s*[=:]\s*['\"]?(\d+)"),
    re.compile(r"Settlements/Futures/Settlements/(\d+)/"),
    re.compile(r'"productIds?"\s*:\s*\[?\s*"?(\d+)"?'),
)


@dataclass
class HttpEvidence:
    url: str
    status_code: int | None
    content_type: str | None
    bytes: int
    sha256: str | None
    error: str | None = None


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "Chrome/126.0 Safari/537.36 fin-research-v147"
            ),
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": BASE + "/market-data.html",
        }
    )
    return session


def get_with_retries(
    session: requests.Session,
    url: str,
    *,
    params: dict[str, str] | None = None,
    attempts: int = 2,
    timeout: int = 12,
) -> requests.Response:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            response = session.get(url, params=params, timeout=timeout)
            if response.status_code in {403, 429, 500, 502, 503, 504}:
                time.sleep(0.75 * (attempt + 1))
                continue
            return response
        except requests.RequestException as exc:
            last = exc
            time.sleep(0.75 * (attempt + 1))
    if last is not None:
        raise last
    raise RuntimeError(f"unable to fetch {url}")


def evidence_from_response(response: requests.Response) -> HttpEvidence:
    return HttpEvidence(
        url=response.url,
        status_code=response.status_code,
        content_type=response.headers.get("content-type"),
        bytes=len(response.content),
        sha256=sha256_bytes(response.content),
    )


def discover_ids(html: str) -> list[int]:
    counts: dict[int, int] = {}
    for pattern in ID_PATTERNS:
        for match in pattern.findall(html):
            value = int(match)
            counts[value] = counts.get(value, 0) + 1
    return [item[0] for item in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def clean_numeric(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "UNCH", "--"}:
        return None
    text = re.sub(r"[^0-9+\-.]", "", text)
    if text in {"", "+", "-", "."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def settlement_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows = payload.get("settlements")
    if not isinstance(rows, list):
        return []
    result: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        month = str(raw.get("month", "")).strip()
        if not month or month.lower() == "total":
            continue
        row = dict(raw)
        row["_parsed"] = {
            "open": clean_numeric(raw.get("open")),
            "high": clean_numeric(raw.get("high")),
            "low": clean_numeric(raw.get("low")),
            "last": clean_numeric(raw.get("last")),
            "settle": clean_numeric(raw.get("settle")),
            "volume": clean_numeric(raw.get("volume")),
            "open_interest": clean_numeric(raw.get("openInterest")),
        }
        result.append(row)
    return result


def probe_product(
    session: requests.Session,
    symbol: str,
    spec: dict[str, Any],
    output: Path,
) -> dict[str, Any]:
    page_url = BASE + str(spec["page"])
    page_evidence: HttpEvidence
    discovered: list[int] = []
    page_excerpt = ""
    try:
        response = get_with_retries(session, page_url)
        page_evidence = evidence_from_response(response)
        text = response.text
        discovered = discover_ids(text)
        page_excerpt = text[:500].replace("\n", " ")
    except Exception as exc:  # noqa: BLE001 - evidence capture is intentional
        page_evidence = HttpEvidence(page_url, None, None, 0, None, repr(exc))

    # Probe the explicit candidate first, then at most three IDs discovered from
    # the official product page. This bounds both runtime and request volume.
    candidates: list[int] = []
    for value in [*spec.get("candidate_ids", []), *discovered[:3]]:
        if int(value) not in candidates:
            candidates.append(int(value))
    candidates = candidates[:4]

    attempts: list[dict[str, Any]] = []
    accepted_id: int | None = None
    accepted_dates: list[str] = []
    raw_dir = output / "raw" / symbol
    raw_dir.mkdir(parents=True, exist_ok=True)

    for product_id in candidates:
        product_result: dict[str, Any] = {
            "product_id": product_id,
            "dates": {},
            "nonempty_dates": 0,
        }
        for trade_date in PROBE_DATES:
            url = SETTLEMENT_TEMPLATE.format(product_id=product_id)
            params = {
                "tradeDate": trade_date,
                "strategy": "DEFAULT",
                "pageSize": "500",
            }
            try:
                response = get_with_retries(session, url, params=params)
                http = evidence_from_response(response)
                try:
                    payload = response.json()
                    rows = settlement_rows(payload)
                    json_error = None
                except Exception as exc:  # noqa: BLE001
                    payload = None
                    rows = []
                    json_error = repr(exc)
                key = trade_date.replace("/", "-")
                if payload is not None:
                    (raw_dir / f"{product_id}_{key}.json").write_text(
                        json.dumps(payload, indent=2, sort_keys=True) + "\n"
                    )
                product_result["dates"][trade_date] = {
                    "http": asdict(http),
                    "top_level_keys": sorted(payload.keys()) if isinstance(payload, dict) else [],
                    "row_count": len(rows),
                    "months": [row.get("month") for row in rows[:8]],
                    "sample": rows[:2],
                    "json_error": json_error,
                    "body_prefix": response.text[:300] if payload is None else None,
                }
                if rows:
                    product_result["nonempty_dates"] += 1
            except Exception as exc:  # noqa: BLE001
                product_result["dates"][trade_date] = {"error": repr(exc)}
            time.sleep(0.15)
        attempts.append(product_result)
        if product_result["nonempty_dates"] == len(PROBE_DATES):
            accepted_id = product_id
            accepted_dates = [
                d
                for d, item in product_result["dates"].items()
                if item.get("row_count", 0) > 0
            ]
            break

    return {
        "symbol": symbol,
        "name": spec["name"],
        "page": asdict(page_evidence),
        "page_excerpt": page_excerpt,
        "discovered_product_ids": discovered[:25],
        "candidate_product_ids": candidates,
        "accepted_product_id": accepted_id,
        "accepted_dates": accepted_dates,
        "attempts": attempts,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/v147_cme_probe"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    session = build_session()
    products: dict[str, Any] = {}
    for symbol, spec in PRODUCTS.items():
        print(f"probing {symbol}: {spec['name']}", flush=True)
        products[symbol] = probe_product(session, symbol, spec, args.output)

    accepted = [symbol for symbol, item in products.items() if item["accepted_product_id"]]
    core_access = all(symbol in accepted for symbol in ("CL", "6E"))
    broad_access = len(accepted) >= 5
    if core_access and broad_access:
        status = "official_dated_settlement_access_confirmed"
    elif core_access:
        status = "official_dated_settlement_access_partial"
    else:
        status = "official_dated_settlement_access_blocked"

    summary = {
        "candidate": "V147_CME_DATED_CONTRACT_ACCESS_PROBE",
        "as_of": date.today().isoformat(),
        "status": status,
        "accepted_symbols": accepted,
        "accepted_symbol_count": len(accepted),
        "required_for_full_v148_v154": ["ES", "ZN", "GC", "CL", "6E"],
        "full_research_permitted": broad_access,
        "live_ready": False,
        "real_leverage_authorized": False,
        "products": products,
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    report = [
        "# V147 CME dated-contract access probe",
        "",
        f"Status: `{status}`",
        "",
        "| Symbol | Accepted product id | Successful dates |",
        "|---|---:|---|",
    ]
    for symbol, item in products.items():
        report.append(
            f"| {symbol} | {item['accepted_product_id'] or '—'} | "
            f"{', '.join(item['accepted_dates']) or '—'} |"
        )
    report += [
        "",
        "`full_research_permitted` requires at least five independently verified products.",
        "No strategy result is produced by this probe.",
    ]
    (args.output / "REPORT_RU.md").write_text("\n".join(report) + "\n")
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if core_access else 2


if __name__ == "__main__":
    raise SystemExit(main())
