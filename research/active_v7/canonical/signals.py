from __future__ import annotations

import numpy as np
import pandas as pd
from config import HEDGE_COMPONENTS


def _schedule(desired: pd.DataFrame, every: int) -> pd.DataFrame:
    out = np.zeros_like(desired.to_numpy(float))
    current = np.zeros(desired.shape[1])
    for i, row in enumerate(desired.to_numpy(float)):
        row = np.nan_to_num(row)
        current[(current < 0) & (row >= 0)] = 0.0
        if i % every == 0 and np.abs(row - current).sum() > 1e-12:
            current = row.copy()
        out[i] = current
    return pd.DataFrame(out, index=desired.index, columns=desired.columns)


def _component(perp_close, funding, spot_close, base, spec):
    ema = perp_close.ewm(span=spec["ema_days"], adjust=False, min_periods=spec["ema_days"]).mean()
    momentum = perp_close.pct_change(spec["mom_days"])
    bear = (perp_close < ema) & (momentum < 0)
    desired = pd.DataFrame(0.0, index=perp_close.index, columns=perp_close.columns)
    btc, eth = perp_close.columns
    if spec["kind"] == "dual":
        count = bear.sum(axis=1).replace(0, np.nan)
        desired = -bear.astype(float).div(count, axis=0).fillna(0.0)
    else:
        trailing_funding = funding.rolling(21, min_periods=21).mean()
        for timestamp in perp_close.index:
            candidates = [symbol for symbol in (btc, eth) if bool(bear.at[timestamp, symbol])]
            if candidates:
                chosen = max(candidates, key=lambda symbol: float(trailing_funding.at[timestamp, symbol]))
                desired.at[timestamp, chosen] = -1.0
    unit_return = (desired.shift(1).fillna(0.0) * perp_close.pct_change()).sum(axis=1)
    vol = unit_return.rolling(spec["vol_days"], min_periods=spec["vol_days"]).std() * np.sqrt(365.0)
    scale = (spec["target_vol"] / vol.replace(0.0, np.nan)).clip(upper=spec["size"]).fillna(0.0)
    desired = desired.mul(scale, axis=0)
    desired = _schedule(desired, spec["every"])
    capacity = (1.0 - base.abs().sum(axis=1)).clip(0.0, 1.0)
    gross = desired.abs().sum(axis=1).replace(0.0, np.nan)
    return desired.mul((capacity / gross).clip(upper=1.0).fillna(0.0), axis=0)


def build(perp_close, funding, spot_close, base):
    components = [_component(perp_close, funding, spot_close, base, spec) for spec in HEDGE_COMPONENTS]
    return sum(components) / len(components)
