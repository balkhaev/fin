from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
V154_SOURCE = REPO / "research" / "active_v147_v154" / "source"
V154_ROOT = REPO / "research" / "active_v147_v154"
V75_PATH = REPO / "research" / "active_v139_v146" / "inputs" / "v75_stress_equity.csv"


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_v154_modules() -> tuple[ModuleType, ModuleType]:
    """Load V154 feature/engine code while isolating its `config` module."""

    previous = sys.modules.get("config")
    v154_config = load_module("v154_config", V154_SOURCE / "config.py")
    sys.modules["config"] = v154_config
    try:
        features = load_module("v154_features_for_v162", V154_SOURCE / "features.py")
        engine = load_module("v154_engine_for_v162", V154_SOURCE / "engine.py")
    finally:
        if previous is None:
            sys.modules.pop("config", None)
        else:
            sys.modules["config"] = previous
    return features, engine


V154_FEATURES, V154_ENGINE = load_v154_modules()


def load_market():
    contracts = pd.read_csv(
        V154_ROOT / "inputs" / "processed" / "vx_monthly_contracts.csv",
        parse_dates=["Trade Date", "Expiry"],
    )
    spot = pd.read_csv(
        V154_ROOT / "inputs" / "processed" / "vix_spot.csv",
        parse_dates=["DATE"],
    )
    return V154_FEATURES.build_market(contracts, spot)


def load_atlas() -> pd.DataFrame:
    frame = pd.read_csv(V75_PATH, index_col=0, parse_dates=True)
    frame.index = pd.to_datetime(frame.index).tz_localize(None)
    frame = frame.sort_index()
    output = pd.DataFrame(index=frame.index)
    output["equity"] = pd.to_numeric(frame["equity"], errors="coerce")
    output = output.dropna()
    output["daily_return"] = output["equity"].pct_change().fillna(
        output["equity"].iloc[0] / 10_000.0 - 1.0
    )
    for column, value in {
        "gross": 0.0,
        "turnover": 0.0,
        "trading_cost": 0.0,
        "forced_cost": 0.0,
        "min_margin_buffer": 1.0,
        "liquidated_notional": 0.0,
    }.items():
        output[column] = value
    return output


def synthetic_market():
    index = pd.date_range("2020-01-01", periods=400, freq="B")
    expiries = pd.to_datetime(["2022-06-15", "2022-07-20", "2022-08-17"])
    rows = []
    for number, expiry in enumerate(expiries):
        for offset, day in enumerate(index):
            value = 18.0 + number - 0.005 * offset
            rows.append(
                {
                    "Trade Date": day,
                    "Expiry": expiry,
                    "Open": value,
                    "High": value * 1.02,
                    "Low": value * 0.98,
                    "Settle": value,
                    "Total Volume": 100,
                    "Open Interest": 1000,
                }
            )
    spot = pd.DataFrame(
        {
            "DATE": index,
            "CLOSE": 20.0 + 2.0 * np.sin(np.arange(len(index)) / 25.0),
        }
    )
    return V154_FEATURES.build_market(pd.DataFrame(rows), spot)
