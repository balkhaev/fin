from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import numpy as np
import pandas as pd


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load(v6_dir: Path, v5_dir: Path):
    source = v6_dir / "source"
    for name in ("config", "market", "metrics"):
        _module(name, source / f"{name}.py")
    strategy = _module("v6_strategy", source / "strategy.py")
    frames = {}
    for path in sorted(v6_dir.glob("processed_*_1d.csv")):
        symbol = path.name.removeprefix("processed_").removesuffix("_1d.csv")
        frame = pd.read_csv(path, index_col=0, parse_dates=True)
        index = pd.to_datetime(frame.index, utc=True)
        frame.index = index
        frames[symbol] = frame
    data = strategy.MarketData(frames)
    families, _ = strategy.build_family_experts(data)
    base = sum(families[name] for name in ("momentum_top1", "momentum_top2", "momentum_top3", "momentum_breadth")) / 4.0

    spot = pd.DataFrame({symbol: data.close[symbol] for symbol in data.symbols}, index=data.index)
    processed = v5_dir / "processed"
    perp_close, perp_open, funding = {}, {}, {}
    for symbol in ("BTCUSDT", "ETHUSDT"):
        perp = pd.read_csv(processed / f"perp_{symbol}_8h.csv", index_col=0, parse_dates=True)
        perp.index = pd.to_datetime(perp.index, utc=True)
        daily = perp.resample("1D").agg({"open": "first", "close": "last"})
        perp_open[symbol] = daily.open.reindex(data.index).ffill().bfill()
        perp_close[symbol] = daily.close.reindex(data.index).ffill().bfill()
        fund = pd.read_csv(processed / f"funding_{symbol}.csv", index_col=0, parse_dates=True)
        fund.index = pd.to_datetime(fund.index, utc=True, format="mixed")
        funding[symbol] = fund.funding_rate.resample("1D").sum().reindex(data.index).fillna(0.0)
    return (
        data,
        base.reindex(data.index).fillna(0.0),
        spot,
        pd.DataFrame(perp_open),
        pd.DataFrame(perp_close),
        pd.DataFrame(funding),
    )
