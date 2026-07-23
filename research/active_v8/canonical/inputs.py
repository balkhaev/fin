from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any

import pandas as pd


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load(v6_dir: Path, v5_dir: Path):
    """Reconstruct the frozen V6 spot sleeve and load BTC/ETH perpetual inputs."""
    source = v6_dir / "source"
    saved = {name: sys.modules.get(name) for name in ("config", "market", "metrics")}
    try:
        for name in ("config", "market", "metrics"):
            _load_module(name, source / f"{name}.py")
        strategy = _load_module("_v6_strategy_for_v8", source / "strategy.py")
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    frames: dict[str, pd.DataFrame] = {}
    for path in sorted(v6_dir.glob("processed_*_1d.csv")):
        symbol = path.name.removeprefix("processed_").removesuffix("_1d.csv")
        frame = pd.read_csv(path, index_col=0, parse_dates=True)
        frame.index = pd.to_datetime(frame.index, utc=True)
        frames[symbol] = frame

    data = strategy.MarketData(frames)
    families, _ = strategy.build_family_experts(data)
    spot_signal = sum(
        families[name]
        for name in ("momentum_top1", "momentum_top2", "momentum_top3", "momentum_breadth")
    ) / 4.0

    processed = v5_dir / "processed"
    perp_open: dict[str, pd.Series] = {}
    perp_close: dict[str, pd.Series] = {}
    funding: dict[str, pd.Series] = {}
    for symbol in ("BTCUSDT", "ETHUSDT"):
        perp = pd.read_csv(processed / f"perp_{symbol}_8h.csv", index_col=0, parse_dates=True)
        perp.index = pd.to_datetime(perp.index, utc=True)
        daily = perp.resample("1D").agg({"open": "first", "close": "last"})
        perp_open[symbol] = daily["open"].reindex(data.index).ffill().bfill()
        perp_close[symbol] = daily["close"].reindex(data.index).ffill().bfill()

        fund = pd.read_csv(processed / f"funding_{symbol}.csv", index_col=0, parse_dates=True)
        fund.index = pd.to_datetime(fund.index, utc=True, format="mixed")
        funding[symbol] = (
            fund["funding_rate"].resample("1D").sum().reindex(data.index).fillna(0.0)
        )

    return (
        data,
        spot_signal.reindex(data.index).fillna(0.0),
        pd.DataFrame(perp_open),
        pd.DataFrame(perp_close),
        pd.DataFrame(funding),
    )
