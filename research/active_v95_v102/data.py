from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import pandas as pd

@dataclass
class MarketData:
    prices: pd.DataFrame
    returns: pd.DataFrame
    groups: dict[str, str]
    source_manifest: dict


def _read_prices(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    frame.index = pd.to_datetime(frame.index, utc=True)
    frame = frame.sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    return frame.apply(pd.to_numeric, errors="coerce")


def load(etf_path: Path, fx_path: Path, groups: dict[str, tuple[str, ...]]) -> MarketData:
    etf = _read_prices(etf_path)
    fx = _read_prices(fx_path).rename(columns=lambda c: f"FX_{c}")
    index = etf.index.union(fx.index).sort_values()
    prices = pd.concat([etf.reindex(index), fx.reindex(index)], axis=1).ffill(limit=5)
    prices = prices.loc[prices.index >= pd.Timestamp("2007-01-01", tz="UTC")]
    returns = prices.pct_change(fill_method=None)
    group_map: dict[str, str] = {}
    for group, names in groups.items():
        for name in names:
            if name in prices.columns:
                group_map[name] = group
    for name in fx.columns:
        group_map[name] = "fx_spot"
    if not prices.index.is_monotonic_increasing or prices.index.has_duplicates:
        raise ValueError("invalid market-data index")
    manifest = {
        "etf_path": str(etf_path),
        "fx_path": str(fx_path),
        "start": str(prices.index.min()),
        "end": str(prices.index.max()),
        "symbols": list(prices.columns),
        "rows": len(prices),
        "missing_fraction": {c: float(prices[c].isna().mean()) for c in prices},
    }
    return MarketData(prices, returns, group_map, manifest)


def load_atlas(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    frame.index = pd.to_datetime(frame.index, utc=True)
    frame = frame.sort_index()
    if "equity" not in frame:
        raise ValueError("ATLAS input lacks equity")
    if (frame.equity <= 0).any():
        raise ValueError("nonpositive ATLAS equity")
    return frame
