#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yfinance as yf

START = "2007-01-01"
END_EXCLUSIVE = "2026-07-02"
ETF_SYMBOLS = (
    "SPY", "QQQ", "IWM", "EFA", "EEM", "VNQ",
    "TLT", "IEF", "HYG", "GLD", "DBC", "UUP", "FXE", "FXY",
)
FX_SYMBOLS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "AUDUSD": "AUDUSD=X",
    "NZDUSD": "NZDUSD=X",
    "USDJPY": "JPY=X",
    "USDCAD": "CAD=X",
    "USDCHF": "CHF=X",
}
CORE = ("SPY", "TLT", "GLD")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean_index(series: pd.Series) -> pd.Series:
    output = pd.to_numeric(series, errors="coerce").dropna()
    index = pd.to_datetime(output.index, utc=True).normalize()
    output.index = index
    output = output[~output.index.duplicated(keep="last")].sort_index()
    return output[(output.index >= pd.Timestamp(START, tz="UTC")) &
                  (output.index < pd.Timestamp(END_EXCLUSIVE, tz="UTC"))]


def yahoo_close(ticker: str) -> pd.Series:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            frame = yf.download(
                ticker,
                start=START,
                end=END_EXCLUSIVE,
                auto_adjust=True,
                progress=False,
                threads=False,
            )
            if frame.empty:
                raise RuntimeError("empty Yahoo response")
            if isinstance(frame.columns, pd.MultiIndex):
                if "Close" in frame.columns.get_level_values(0):
                    close = frame.xs("Close", axis=1, level=0).iloc[:, 0]
                else:
                    raise RuntimeError(f"Yahoo response lacks Close: {frame.columns}")
            else:
                close = frame["Close"]
            close = clean_index(close)
            if len(close) < 100:
                raise RuntimeError(f"too few Yahoo rows: {len(close)}")
            return close
        except Exception as error:  # network/vendor fallback is explicit below
            last_error = error
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Yahoo download failed for {ticker}: {last_error!r}")


def stooq_close(symbol: str, *, is_etf: bool) -> pd.Series:
    stooq_symbol = f"{symbol.lower()}.us" if is_etf else symbol.lower()
    url = (
        "https://stooq.com/q/d/l/?s=" + stooq_symbol
        + "&d1=20070101&d2=20260701&i=d"
    )
    response = requests.get(url, timeout=45, headers={"User-Agent": "fin-research/1.0"})
    response.raise_for_status()
    frame = pd.read_csv(io.StringIO(response.text))
    if frame.empty or not {"Date", "Close"}.issubset(frame.columns):
        raise RuntimeError(f"invalid Stooq response for {symbol}")
    frame["Date"] = pd.to_datetime(frame["Date"], utc=True)
    return clean_index(frame.set_index("Date")["Close"])


def download_series(name: str, ticker: str, *, is_etf: bool) -> tuple[pd.Series, str]:
    try:
        return yahoo_close(ticker), "yahoo_auto_adjusted"
    except Exception as yahoo_error:
        try:
            return stooq_close(name, is_etf=is_etf), "stooq_close_fallback"
        except Exception as stooq_error:
            raise RuntimeError(
                f"both vendors failed for {name}/{ticker}: "
                f"Yahoo={yahoo_error!r}; Stooq={stooq_error!r}"
            ) from stooq_error


def frame_manifest(frame: pd.DataFrame, sources: dict[str, str]) -> dict[str, Any]:
    return {
        "rows": len(frame),
        "columns": list(frame.columns),
        "start": frame.index.min().isoformat() if len(frame) else None,
        "end": frame.index.max().isoformat() if len(frame) else None,
        "valid_rows": {column: int(frame[column].notna().sum()) for column in frame},
        "first_valid": {
            column: frame[column].first_valid_index().isoformat()
            if frame[column].first_valid_index() is not None else None
            for column in frame
        },
        "last_valid": {
            column: frame[column].last_valid_index().isoformat()
            if frame[column].last_valid_index() is not None else None
            for column in frame
        },
        "missing_fraction_on_union": {
            column: float(frame[column].isna().mean()) for column in frame
        },
        "sources": sources,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    etf_series: dict[str, pd.Series] = {}
    etf_sources: dict[str, str] = {}
    failures: dict[str, str] = {}
    for symbol in ETF_SYMBOLS:
        try:
            etf_series[symbol], etf_sources[symbol] = download_series(
                symbol, symbol, is_etf=True
            )
        except Exception as error:
            failures[symbol] = repr(error)

    fx_series: dict[str, pd.Series] = {}
    fx_sources: dict[str, str] = {}
    for name, ticker in FX_SYMBOLS.items():
        try:
            fx_series[name], fx_sources[name] = download_series(
                name, ticker, is_etf=False
            )
        except Exception as error:
            failures[name] = repr(error)

    etf = pd.concat(etf_series, axis=1).sort_index() if etf_series else pd.DataFrame()
    fx = pd.concat(fx_series, axis=1).sort_index() if fx_series else pd.DataFrame()
    etf.index.name = "date"
    fx.index.name = "date"

    etf_path = args.output / "etf_adjusted_close.csv"
    fx_path = args.output / "fx_reference_prices.csv"
    etf.to_csv(etf_path)
    fx.to_csv(fx_path)

    core_missing = {
        symbol: float(etf[symbol].isna().mean()) if symbol in etf else 1.0
        for symbol in CORE
    }
    core_start_ok = all(
        symbol in etf
        and etf[symbol].first_valid_index() is not None
        and etf[symbol].first_valid_index() <= pd.Timestamp("2008-01-10", tz="UTC")
        for symbol in CORE
    )
    latest_required = pd.Timestamp("2026-06-29", tz="UTC")
    latest_ok = all(
        symbol in etf
        and etf[symbol].last_valid_index() is not None
        and etf[symbol].last_valid_index() >= latest_required
        for symbol in CORE
    )
    gate_checks = {
        "minimum_etf_symbols": len(etf_series) >= 12,
        "minimum_fx_symbols": len(fx_series) >= 5,
        "core_start": core_start_ok,
        "core_latest": latest_ok,
        "core_missing_fraction": all(value <= 0.03 for value in core_missing.values()),
    }

    account_index = etf.index.union(fx.index).sort_values()
    account_index = account_index[
        (account_index >= pd.Timestamp("2008-01-01", tz="UTC"))
        & (account_index < pd.Timestamp("2026-07-01", tz="UTC"))
    ]
    flat = pd.DataFrame(
        {"equity": 10_000.0, "gross": 0.0, "turnover": 0.0},
        index=account_index,
    )
    flat.index.name = "date"
    flat_path = args.output / "flat_atlas_interface.csv"
    flat.to_csv(flat_path)

    manifest = {
        "program": "V485_V492_INDEPENDENT_MACRO_CRISIS_REPLAY",
        "download_window": [START, END_EXCLUSIVE],
        "etf": frame_manifest(etf, etf_sources),
        "fx": frame_manifest(fx, fx_sources),
        "failures": failures,
        "core_missing_fraction": core_missing,
        "gate_checks": gate_checks,
        "data_gate_passed": bool(all(gate_checks.values())),
        "files": {
            etf_path.name: {"bytes": etf_path.stat().st_size, "sha256": sha256(etf_path)},
            fx_path.name: {"bytes": fx_path.stat().st_size, "sha256": sha256(fx_path)},
            flat_path.name: {"bytes": flat_path.stat().st_size, "sha256": sha256(flat_path)},
        },
        "execution_grade": False,
    }
    (args.output / "DATA_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0 if manifest["data_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
