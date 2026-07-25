from __future__ import annotations

import numpy as np
import pandas as pd

from config import (
    DRAWDOWN_HARD,
    DRAWDOWN_SOFT,
    FINANCING_RATE,
    PORTFOLIO_GROSS_CAP,
    RECOVERY,
    TRANSFER_COST_BPS,
)


def load_account(path):
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    frame.index = pd.to_datetime(frame.index, utc=True)
    return frame.sort_index()


def align(accounts):
    index = accounts[0].index
    for account in accounts[1:]:
        index = index.intersection(account.index)
    output = []
    for account in accounts:
        frame = account.reindex(index).copy()
        frame["return"] = frame.equity.pct_change().fillna(0.0)
        output.append(frame)
    return output


def cut(account, period, periods):
    start, end = periods[period]
    return account.loc[
        (account.index >= pd.Timestamp(start, tz="UTC"))
        & (account.index < pd.Timestamp(end, tz="UTC"))
    ]


def monthly_target(index, target):
    return pd.DataFrame(
        np.tile(np.asarray(target, dtype=float), (len(index), 1)),
        index=index,
        columns=("atlas", "crisis", "rotation"),
    )


def inverse_vol_target(returns):
    volatility = returns.rolling(63, min_periods=30).std() * np.sqrt(252.0)
    inverse = 1.0 / volatility.replace(0.0, np.nan)
    weights = inverse.div(inverse.sum(axis=1), axis=0)
    fallback = pd.Series((0.80, 0.10, 0.10), index=returns.columns)
    weights = weights.fillna(fallback)
    floors = (0.50, 0.05, 0.05)
    caps = (0.85, 0.25, 0.25)
    for i, column in enumerate(returns.columns):
        weights[column] = weights[column].clip(floors[i], caps[i])
    return weights.div(weights.sum(axis=1), axis=0)


def average_pairwise_correlation(returns):
    rolling = returns.rolling(126, min_periods=60).corr()
    values = []
    for timestamp in returns.index:
        try:
            matrix = rolling.loc[timestamp].to_numpy()
            pairwise = matrix[np.triu_indices(3, 1)]
            values.append(float(np.nanmean(pairwise)))
        except Exception:
            values.append(1.0)
    return pd.Series(values, index=returns.index)


def simulate(
    accounts,
    base_weights,
    mode,
    target_vol=0.20,
    corr_threshold=0.55,
    leverage_cap=1.15,
    rebalance=20,
    extra_cost_bps=0.0,
    delay=0,
):
    atlas, crisis, rotation = align(accounts)
    index = atlas.index
    returns = pd.concat(
        {
            "atlas": atlas["return"],
            "crisis": crisis["return"],
            "rotation": rotation["return"],
        },
        axis=1,
    )
    gross = pd.concat(
        {
            "atlas": atlas.get("gross", 0.0),
            "crisis": crisis.get("gross", 0.0),
            "rotation": rotation.get("gross", 0.0),
        },
        axis=1,
    ).fillna(0.0)

    if mode == "static":
        target = monthly_target(index, base_weights)
    else:
        target = inverse_vol_target(returns).shift(1)
        target = target.fillna(pd.Series(base_weights, index=returns.columns))
    target = target.shift(delay).fillna(pd.Series(base_weights, index=returns.columns))
    raw_return = (target * returns).sum(axis=1)

    if mode == "dynamic":
        realised_vol = raw_return.rolling(63, min_periods=30).std() * np.sqrt(252.0)
        scale = (target_vol / realised_vol.replace(0.0, np.nan)).shift(1).clip(0.70, leverage_cap).fillna(1.0)
        correlation = average_pairwise_correlation(returns).shift(1).fillna(1.0)
        scale = scale.where(correlation < corr_threshold, np.minimum(scale, 1.0))
    else:
        scale = pd.Series(1.0, index=index)

    equity_value = 1.0
    high_water = 1.0
    risk_state = 0
    equity, turnover, gross_output, scale_output = [], [], [], []
    previous_weights = np.asarray(base_weights, dtype=float)
    previous_scale = 1.0

    for i, timestamp in enumerate(index):
        drawdown = equity_value / high_water - 1.0
        if drawdown <= DRAWDOWN_HARD:
            risk_state = 2
        elif drawdown <= DRAWDOWN_SOFT and risk_state < 1:
            risk_state = 1
        elif drawdown >= RECOVERY:
            risk_state = 0

        live_scale = float(scale.iloc[i])
        live_scale = min(
            live_scale,
            1.0 if risk_state == 1 else 0.80 if risk_state == 2 else leverage_cap,
        )
        weights = target.iloc[i].to_numpy(float)
        total_gross = float(np.dot(weights, gross.iloc[i].to_numpy(float))) * live_scale
        if total_gross > PORTFOLIO_GROSS_CAP and total_gross > 0.0:
            live_scale *= PORTFOLIO_GROSS_CAP / total_gross
            total_gross = PORTFOLIO_GROSS_CAP

        allocation_turnover = float(np.abs(weights - previous_weights).sum() + abs(live_scale - previous_scale))
        transfer_cost = allocation_turnover * (TRANSFER_COST_BPS + extra_cost_bps) / 10000.0
        financing_cost = max(0.0, live_scale - 1.0) * FINANCING_RATE / 252.0
        daily_return = live_scale * float(np.dot(weights, returns.iloc[i].to_numpy(float))) - transfer_cost - financing_cost
        equity_value *= 1.0 + daily_return
        high_water = max(high_water, equity_value)
        equity.append(10000.0 * equity_value)
        turnover.append(allocation_turnover)
        gross_output.append(total_gross)
        scale_output.append(live_scale)
        previous_weights = weights
        previous_scale = live_scale

    return pd.DataFrame(
        {
            "equity": equity,
            "gross": gross_output,
            "turnover": turnover,
            "leverage_scale": scale_output,
            "atlas_weight": target["atlas"].values,
            "crisis_weight": target["crisis"].values,
            "rotation_weight": target["rotation"].values,
        },
        index=index,
    )
