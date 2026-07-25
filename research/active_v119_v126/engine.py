from __future__ import annotations

import numpy as np
import pandas as pd

from config import FINANCING_RATE, PORTFOLIO_GROSS_CAP
from allocators import AllocationSpec

COLUMNS = ("atlas", "crisis", "rotation")


def load_account(path) -> pd.DataFrame:
    frame = pd.read_csv(path, index_col=0, parse_dates=True)
    frame.index = pd.to_datetime(frame.index, utc=True)
    frame = frame.sort_index()
    if "equity" not in frame:
        raise ValueError(f"missing equity: {path}")
    return frame


def align_accounts(accounts: list[pd.DataFrame]) -> tuple[list[pd.DataFrame], pd.DataFrame, pd.DataFrame]:
    index = accounts[0].index
    for account in accounts[1:]:
        index = index.intersection(account.index)
    aligned = []
    for account in accounts:
        frame = account.reindex(index).copy()
        frame["return"] = frame.equity.pct_change().fillna(0.0)
        if "gross" not in frame:
            frame["gross"] = 0.0
        aligned.append(frame)
    returns = pd.DataFrame(
        {name: frame["return"] for name, frame in zip(COLUMNS, aligned)},
        index=index,
    )
    gross = pd.DataFrame(
        {name: frame["gross"].fillna(0.0) for name, frame in zip(COLUMNS, aligned)},
        index=index,
    )
    return aligned, returns, gross


def simulate(
    accounts: list[pd.DataFrame],
    allocation: pd.DataFrame,
    requested_scale: pd.Series,
    spec: AllocationSpec,
    delay: int = 0,
    extra_transfer_cost_bps: float = 0.0,
    force_no_leverage: bool = False,
) -> pd.DataFrame:
    aligned, returns, sleeve_gross = align_accounts(accounts)
    index = returns.index
    allocation = allocation.reindex(index).shift(1 + delay).fillna(
        pd.Series(spec.base_weights, index=COLUMNS)
    )
    requested_scale = requested_scale.reindex(index).shift(1 + delay).fillna(1.0)

    equity_value = 10000.0
    high_water = equity_value
    notionals = np.zeros(3, dtype=float)
    cash = equity_value
    current_weights = np.asarray(spec.base_weights, dtype=float)
    current_scale = 0.0

    equity_rows, gross_rows, turnover_rows, scale_rows = [], [], [], []
    weight_rows = []

    for i, timestamp in enumerate(index):
        previous_equity = equity_value
        drawdown = previous_equity / high_water - 1.0
        throttle = 0.80 if drawdown <= -0.15 else 0.90 if drawdown <= -0.10 else 1.0
        desired_weights = allocation.iloc[i].to_numpy(float)
        desired_weights = np.clip(desired_weights, 0.0, None)
        desired_weights = desired_weights / desired_weights.sum() if desired_weights.sum() > 0.0 else np.asarray(spec.base_weights)
        desired_scale = float(requested_scale.iloc[i]) * throttle
        if force_no_leverage:
            desired_scale = min(desired_scale, 1.0)
        desired_scale = max(0.0, min(desired_scale, spec.leverage_cap))

        estimated_gross = float(np.dot(desired_weights, sleeve_gross.iloc[i].to_numpy(float))) * desired_scale
        if estimated_gross > PORTFOLIO_GROSS_CAP and estimated_gross > 0.0:
            desired_scale *= PORTFOLIO_GROSS_CAP / estimated_gross

        rebalance = i == 0 or i % spec.rebalance == 0
        turnover = 0.0
        if rebalance:
            target_notionals = previous_equity * desired_scale * desired_weights
            turnover = float(np.abs(target_notionals - notionals).sum() / max(previous_equity, 1e-12))
            cost_rate = (spec.transfer_cost_bps + extra_transfer_cost_bps) / 10000.0
            transfer_cost = previous_equity * turnover * cost_rate
            notionals = target_notionals
            cash = previous_equity - transfer_cost - float(notionals.sum())
            current_weights = desired_weights
            current_scale = desired_scale

        financing_cost = max(0.0, -cash) * FINANCING_RATE / 252.0
        cash -= financing_cost
        day_returns = returns.iloc[i].to_numpy(float)
        notionals *= 1.0 + np.nan_to_num(day_returns)
        equity_value = float(cash + notionals.sum())
        if not np.isfinite(equity_value) or equity_value <= 0.0:
            equity_value = 1e-9
            notionals[:] = 0.0
            cash = equity_value

        high_water = max(high_water, equity_value)
        live_weights = notionals / max(equity_value, 1e-12)
        live_scale = float(np.abs(live_weights).sum())
        total_gross = float(np.dot(np.abs(live_weights), sleeve_gross.iloc[i].to_numpy(float)))

        equity_rows.append(equity_value)
        gross_rows.append(total_gross)
        turnover_rows.append(turnover)
        scale_rows.append(live_scale)
        weight_rows.append(live_weights.copy())

    result = pd.DataFrame(
        {
            "equity": equity_rows,
            "gross": gross_rows,
            "turnover": turnover_rows,
            "scale": scale_rows,
        },
        index=index,
    )
    weights = pd.DataFrame(weight_rows, index=index, columns=[f"weight_{name}" for name in COLUMNS])
    return pd.concat([result, weights], axis=1)
