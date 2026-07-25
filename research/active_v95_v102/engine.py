from __future__ import annotations

import numpy as np
import pandas as pd

GROUP_LIMITS = {
    "equity": 0.45,
    "rates_credit": 0.35,
    "real_assets": 0.30,
    "usd_fx_etf": 0.20,
    "fx_spot": 0.30,
}


def normalize_targets(raw: pd.DataFrame, groups: dict[str, str], gross_cap: float) -> pd.DataFrame:
    weights = raw.replace([np.inf, -np.inf], np.nan).fillna(0.0).copy()
    for group, limit in GROUP_LIMITS.items():
        columns = [c for c in weights if groups.get(c) == group]
        if not columns:
            continue
        gross = weights[columns].abs().sum(axis=1)
        scale = (limit / gross.replace(0.0, np.nan)).clip(upper=1.0).fillna(1.0)
        weights.loc[:, columns] = weights[columns].mul(scale, axis=0)
    gross = weights.abs().sum(axis=1)
    scale = (gross_cap / gross.replace(0.0, np.nan)).clip(upper=1.0).fillna(1.0)
    return weights.mul(scale, axis=0)


def scheduled_targets(target: pd.DataFrame, rebalance_days: int, band: float) -> pd.DataFrame:
    source = target.to_numpy(float)
    output = np.zeros_like(source)
    current = np.zeros(source.shape[1])
    for i in range(len(source)):
        desired = np.nan_to_num(source[i])
        urgent_exit = np.any((current != 0.0) & (desired == 0.0))
        scheduled = i % rebalance_days == 0
        if urgent_exit or (scheduled and float(np.abs(desired - current).sum()) >= band):
            current = desired.copy()
        output[i] = current
    return pd.DataFrame(output, index=target.index, columns=target.columns)


def volatility_scale(
    weights: pd.DataFrame,
    returns: pd.DataFrame,
    target_vol: float,
    gross_cap: float,
) -> pd.DataFrame:
    raw_return = (weights.shift(1) * returns).sum(axis=1).fillna(0.0)
    realised = raw_return.rolling(63, min_periods=30).std() * np.sqrt(252.0)
    scale = (target_vol / realised.replace(0.0, np.nan)).shift(1).clip(0.0, 2.0).fillna(0.0)
    output = weights.mul(scale, axis=0)
    gross = output.abs().sum(axis=1)
    cap_scale = (gross_cap / gross.replace(0.0, np.nan)).clip(upper=1.0).fillna(1.0)
    return output.mul(cap_scale, axis=0)


def simulate(
    prices: pd.DataFrame,
    returns: pd.DataFrame,
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
    weights = target.reindex(index).fillna(0.0).shift(1).fillna(0.0)
    realised_returns = returns.reindex(index).fillna(0.0)
    turnover = weights.diff().abs().sum(axis=1).fillna(weights.abs().sum(axis=1))
    gross = weights.abs().sum(axis=1)
    short_gross = weights.clip(upper=0.0).abs().sum(axis=1)
    pnl = (weights * realised_returns).sum(axis=1)
    trading_cost = turnover * (cost_bps / 10000.0)
    borrow_cost = short_gross * (annual_short_borrow / 252.0)
    financing = (gross - 1.0).clip(lower=0.0) * (annual_financing / 252.0)
    net_return = pnl - trading_cost - borrow_cost - financing
    equity = 10000.0 * (1.0 + net_return).cumprod()
    return pd.DataFrame(
        {
            "equity": equity,
            "gross": gross,
            "turnover": turnover,
            "costs": trading_cost * 10000.0,
            "short_gross": short_gross,
            "financing_drag": financing * 10000.0,
        },
        index=index,
    )


def combine_separate_accounts(
    atlas: pd.DataFrame,
    sleeve: pd.DataFrame,
    sleeve_weight: float,
) -> pd.DataFrame:
    index = atlas.index.intersection(sleeve.index)
    atlas = atlas.loc[index]
    sleeve = sleeve.loc[index]
    atlas_equity = (1.0 - sleeve_weight) * 10000.0 * atlas.equity / atlas.equity.iloc[0]
    sleeve_equity = sleeve_weight * 10000.0 * sleeve.equity / sleeve.equity.iloc[0]
    equity = atlas_equity + sleeve_equity
    atlas_weight = atlas_equity / equity
    live_sleeve_weight = sleeve_equity / equity
    atlas_gross = atlas.get("gross", pd.Series(0.0, index=index))
    sleeve_gross = sleeve.get("gross", pd.Series(0.0, index=index))
    atlas_turnover = atlas.get("turnover", pd.Series(0.0, index=index))
    sleeve_turnover = sleeve.get("turnover", pd.Series(0.0, index=index))
    return pd.DataFrame(
        {
            "equity": equity,
            "gross": atlas_weight * atlas_gross + live_sleeve_weight * sleeve_gross,
            "turnover": atlas_weight * atlas_turnover + live_sleeve_weight * sleeve_turnover,
            "atlas_weight": atlas_weight,
            "sleeve_weight": live_sleeve_weight,
        },
        index=index,
    )
