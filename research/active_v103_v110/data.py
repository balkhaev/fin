from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import requests

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"


def fetch_one(ticker: str, cache: Path) -> tuple[pd.Series, dict]:
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{ticker}.json"
    if not path.exists():
        params = {
            "period1": 1167609600,
            "period2": 1782864000,
            "interval": "1d",
            "events": "div,splits",
            "includeAdjustedClose": "true",
        }
        last_error = None
        for attempt in range(6):
            try:
                response = requests.get(
                    URL.format(ticker=ticker),
                    params=params,
                    timeout=45,
                    headers={"User-Agent": "fin-research/1.0"},
                )
                response.raise_for_status()
                path.write_text(response.text)
                break
            except Exception as error:
                last_error = error
                time.sleep(2**attempt)
        else:
            raise RuntimeError((ticker, last_error))
    payload = json.loads(path.read_text())
    result = payload["chart"]["result"][0]
    index = pd.to_datetime(result["timestamp"], unit="s", utc=True).normalize()
    indicators = result["indicators"]
    values = indicators.get("adjclose", [{}])[0].get("adjclose")
    if values is None:
        values = indicators["quote"][0]["close"]
    series = pd.Series(values, index=index, name=ticker, dtype=float)
    series = series[~series.index.duplicated(keep="last")].sort_index()
    return series, {
        "ticker": ticker,
        "raw": str(path),
        "rows": int(series.notna().sum()),
        "start": str(series.first_valid_index()),
        "end": str(series.last_valid_index()),
    }


def load(universe: tuple[str, ...], cache: Path) -> tuple[pd.DataFrame, list[dict]]:
    series, manifest = [], []
    for ticker in universe:
        item, record = fetch_one(ticker, cache)
        series.append(item)
        manifest.append(record)
    prices = pd.concat(series, axis=1).sort_index().ffill(limit=5)
    if prices.index.has_duplicates or not prices.index.is_monotonic_increasing:
        raise ValueError("invalid index")
    return prices, manifest
