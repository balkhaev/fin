from __future__ import annotations

import numpy as np
import pandas as pd

from config import V7_HEDGE_COMPONENTS, V8_COMPONENTS, V8_OVERLAY_SCALE


def _schedule(desired: pd.DataFrame, every: int, immediate_exit: bool = True) -> pd.DataFrame:
    output = np.zeros_like(desired.to_numpy(float))
    current = np.zeros(desired.shape[1], dtype=float)
    for index, row in enumerate(desired.to_numpy(float)):
        row = np.nan_to_num(row)
        if immediate_exit:
            current[(np.abs(current) > 1e-15) & (np.abs(row) <= 1e-15)] = 0.0
        if index % every == 0 and np.abs(row - current).sum() > 1e-12:
            current = row.copy()
        output[index] = current
    return pd.DataFrame(output, index=desired.index, columns=desired.columns)


def _v7_component(
    perp_close: pd.DataFrame,
    funding: pd.DataFrame,
    spot_signal: pd.DataFrame,
    spec: dict[str, float | int | str],
) -> pd.DataFrame:
    ema_days = int(spec["ema_days"])
    mom_days = int(spec["mom_days"])
    ema = perp_close.ewm(span=ema_days, adjust=False, min_periods=ema_days).mean()
    momentum = perp_close.pct_change(mom_days)
    bear = (perp_close < ema) & (momentum < 0)
    desired = pd.DataFrame(0.0, index=perp_close.index, columns=perp_close.columns)

    if spec["kind"] == "dual":
        count = bear.sum(axis=1).replace(0, np.nan)
        desired = -bear.astype(float).div(count, axis=0).fillna(0.0)
    else:
        trailing_funding = funding.rolling(21, min_periods=21).mean()
        for timestamp in perp_close.index:
            candidates = [
                symbol for symbol in perp_close.columns
                if bool(bear.at[timestamp, symbol])
            ]
            if candidates:
                chosen = max(
                    candidates,
                    key=lambda symbol: float(trailing_funding.at[timestamp, symbol]),
                )
                desired.at[timestamp, chosen] = -1.0

    unit_return = (desired.shift(1).fillna(0.0) * perp_close.pct_change()).sum(axis=1)
    vol_days = int(spec["vol_days"])
    volatility = (
        unit_return.rolling(vol_days, min_periods=vol_days).std() * np.sqrt(365.0)
    )
    size = float(spec["max_size"])
    scale = (
        float(spec["target_vol"]) / volatility.replace(0.0, np.nan)
    ).clip(upper=size).fillna(0.0)
    desired = _schedule(desired.mul(scale, axis=0), int(spec["every"]))

    capacity = (1.0 - spot_signal.abs().sum(axis=1)).clip(0.0, 1.0)
    gross = desired.abs().sum(axis=1).replace(0.0, np.nan)
    return desired.mul((capacity / gross).clip(upper=1.0).fillna(0.0), axis=0)


def build_v7_hedge(
    perp_close: pd.DataFrame,
    funding: pd.DataFrame,
    spot_signal: pd.DataFrame,
) -> pd.DataFrame:
    components = [
        _v7_component(perp_close, funding, spot_signal, dict(spec))
        for spec in V7_HEDGE_COMPONENTS
    ]
    return sum(components) / len(components)


def _relative_component(
    perp_close: pd.DataFrame,
    spec: dict[str, float | int],
) -> pd.DataFrame:
    btc, eth = "BTCUSDT", "ETHUSDT"
    ratio = np.log(perp_close[eth] / perp_close[btc])
    momentum = ratio.diff(int(spec["lookback_days"]))
    spread_return = 0.5 * (
        perp_close[eth].pct_change() - perp_close[btc].pct_change()
    )
    vol_days = int(spec["vol_days"])
    spread_vol = (
        spread_return.rolling(vol_days, min_periods=vol_days).std().shift(1)
        * np.sqrt(365.0)
    )

    direction = np.zeros(len(perp_close), dtype=float)
    state = 0.0
    threshold = float(spec["threshold"])
    for index, value in enumerate(momentum.to_numpy(float)):
        if not np.isfinite(value):
            state = 0.0
        elif value > threshold:
            state = 1.0
        elif value < -threshold:
            state = -1.0
        direction[index] = state

    gross = (
        float(spec["target_vol"]) / spread_vol.replace(0.0, np.nan)
    ).clip(upper=float(spec["max_gross"])).fillna(0.0)
    desired = pd.DataFrame(0.0, index=perp_close.index, columns=[btc, eth])
    desired[btc] = -0.5 * direction * gross
    desired[eth] = 0.5 * direction * gross
    return _schedule(desired, int(spec["rebalance_days"]), immediate_exit=False)


def build_v8_relative(perp_close: pd.DataFrame) -> pd.DataFrame:
    components = [
        _relative_component(perp_close, dict(spec))
        for spec in V8_COMPONENTS
    ]
    return sum(components) / len(components)


def build_combined_hedge(v7_hedge: pd.DataFrame, v8_relative: pd.DataFrame) -> pd.DataFrame:
    return v7_hedge + V8_OVERLAY_SCALE * v8_relative
