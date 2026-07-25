from __future__ import annotations

import numpy as np
import pandas as pd


def schedule(target: pd.DataFrame, rebalance_days: int) -> pd.DataFrame:
    source = target.to_numpy(float)
    output = np.zeros_like(source)
    current = np.zeros(source.shape[1])
    for i in range(len(source)):
        desired = np.nan_to_num(source[i])
        urgent_exit = np.any((current != 0.0) & (desired == 0.0))
        if urgent_exit or i % rebalance_days == 0:
            current = desired.copy()
        output[i] = current
    return pd.DataFrame(output, index=target.index, columns=target.columns)


def risk_scale(
    target: pd.DataFrame,
    returns: pd.DataFrame,
    target_vol: float,
    gross_cap: float,
) -> pd.DataFrame:
    raw_return = (target.shift(1) * returns).sum(axis=1).fillna(0.0)
    realised_vol = raw_return.rolling(63, min_periods=30).std() * np.sqrt(252.0)
    scale = (target_vol / realised_vol.replace(0.0, np.nan)).shift(1).clip(0.0, 2.0).fillna(0.0)
    output = target.mul(scale, axis=0)
    gross = output.abs().sum(axis=1)
    cap_scale = (gross_cap / gross.replace(0.0, np.nan)).clip(upper=1.0).fillna(1.0)
    return output.mul(cap_scale, axis=0)


def simulate(
    prices: pd.DataFrame,
    target: pd.DataFrame,
    start: str,
    end: str,
    cost_bps: float,
    annual_short_borrow: float = 0.02,
    annual_financing: float = 0.045,
) -> pd.DataFrame:
    index = prices.index[
        (prices.index >= pd.Timestamp(start, tz="UTC"))
        & (prices.index < pd.Timestamp(end, tz="UTC"))
    ]
    returns = prices.pct_change(fill_method=None).reindex(index).fillna(0.0)
    weights = target.reindex(index).fillna(0.0).shift(1).fillna(0.0)
    turnover = weights.diff().abs().sum(axis=1).fillna(weights.abs().sum(axis=1))
    gross = weights.abs().sum(axis=1)
    short_gross = weights.clip(upper=0.0).abs().sum(axis=1)
    net_return = (
        (weights * returns).sum(axis=1)
        - turnover * cost_bps / 10000.0
        - short_gross * annual_short_borrow / 252.0
        - (gross - 1.0).clip(lower=0.0) * annual_financing / 252.0
    )
    equity = 10000.0 * (1.0 + net_return).cumprod()
    return pd.DataFrame(
        {"equity": equity, "gross": gross, "turnover": turnover, "short_gross": short_gross},
        index=index,
    )


def combine(atlas: pd.DataFrame, sleeve: pd.DataFrame, sleeve_weight: float) -> pd.DataFrame:
    index = atlas.index.intersection(sleeve.index)
    atlas = atlas.loc[index]
    sleeve = sleeve.loc[index]
    atlas_equity = (1.0 - sleeve_weight) * 10000.0 * atlas.equity / atlas.equity.iloc[0]
    sleeve_equity = sleeve_weight * 10000.0 * sleeve.equity / sleeve.equity.iloc[0]
    equity = atlas_equity + sleeve_equity
    atlas_weight = atlas_equity / equity
    live_sleeve_weight = sleeve_equity / equity
    return pd.DataFrame(
        {
            "equity": equity,
            "gross": atlas_weight * atlas.get("gross", 0.0) + live_sleeve_weight * sleeve.get("gross", 0.0),
            "turnover": atlas_weight * atlas.get("turnover", 0.0) + live_sleeve_weight * sleeve.get("turnover", 0.0),
            "atlas_weight": atlas_weight,
            "sleeve_weight": live_sleeve_weight,
        },
        index=index,
    )
