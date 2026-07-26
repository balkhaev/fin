#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import run_research as core
from features import build_market

_ORIGINAL_LOAD_ATLAS = core.load_atlas


def load_atlas_with_account_fields(path: Path) -> pd.DataFrame:
    frame = _ORIGINAL_LOAD_ATLAS(path)
    for column, value in {
        "gross": 0.0,
        "turnover": 0.0,
        "trading_cost": 0.0,
        "forced_cost": 0.0,
        "min_margin_buffer": 1.0,
        "liquidated_notional": 0.0,
    }.items():
        frame[column] = value
    return frame


def feature_self_test() -> None:
    index = pd.date_range("2020-01-01", periods=300, freq="B")
    expiries = pd.to_datetime(["2022-06-15", "2022-07-20", "2022-08-17"])
    contracts = []
    for number, expiry in enumerate(expiries):
        for offset, day in enumerate(index):
            value = 15.0 + number + 0.01 * offset
            contracts.append(
                {
                    "Trade Date": day,
                    "Expiry": expiry,
                    "Open": value,
                    "High": value * 1.01,
                    "Low": value * 0.99,
                    "Settle": value,
                    "Total Volume": 100,
                    "Open Interest": 1000,
                }
            )
    spot = pd.DataFrame({"DATE": index, "CLOSE": np.linspace(15, 30, len(index))})
    market = build_market(pd.DataFrame(contracts), spot)
    assert len(market.index) == len(index)
    assert market.features["curve"].notna().all()


core.load_atlas = load_atlas_with_account_fields
core.features_self_test = feature_self_test

if __name__ == "__main__":
    raise SystemExit(core.main())
