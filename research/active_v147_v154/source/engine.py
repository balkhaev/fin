from __future__ import annotations

import math

import numpy as np
import pandas as pd

from config import Audit, FORCED_EXIT_PENALTY
from features import MarketData


def simulate(
    market: MarketData,
    target: pd.DataFrame,
    audit: Audit,
    initial_equity: float = 10_000.0,
) -> pd.DataFrame:
    columns = list(market.expiries)
    open_ = market.open[columns].to_numpy(float)
    high = market.high[columns].to_numpy(float)
    low = market.low[columns].to_numpy(float)
    settle = market.settle[columns].to_numpy(float)
    desired = target.reindex(index=market.index, columns=columns).fillna(0.0).to_numpy(float)
    lag = 1 + audit.execution_delay_days

    equity = initial_equity
    exposures = np.zeros(len(columns), dtype=float)
    previous_settle = np.full(len(columns), np.nan)
    records: list[dict[str, float]] = []
    previous_equity = initial_equity
    rate = audit.cost_bps_per_side * audit.spread_widen_multiplier / 10_000.0

    for i, day in enumerate(market.index):
        forced_cost = 0.0
        liquidated = 0.0
        overnight_pnl = 0.0

        if i > 0:
            valid = np.isfinite(open_[i]) & np.isfinite(previous_settle) & (previous_settle > 0)
            pnl = np.zeros(len(columns), dtype=float)
            pnl[valid] = exposures[valid] * (open_[i, valid] / previous_settle[valid] - 1.0)
            overnight_pnl = float(pnl.sum())
            equity += overnight_pnl
            exposures[valid] *= open_[i, valid] / previous_settle[valid]
            missing = (np.abs(exposures) > 1e-12) & ~valid
            if missing.any():
                notional = float(np.abs(exposures[missing]).sum())
                forced_cost = notional * max(rate, FORCED_EXIT_PENALTY)
                equity -= forced_cost
                exposures[missing] = 0.0

        signal_index = i - lag
        weights = desired[signal_index].copy() if signal_index >= 0 else np.zeros(len(columns))
        weights[~np.isfinite(open_[i])] = 0.0
        gross_target = float(np.abs(weights).sum())
        max_gross_from_margin = max(
            0.0,
            (1.0 - audit.operational_reserve) / max(audit.initial_margin_ratio, 1e-12),
        )
        if gross_target > max_gross_from_margin > 0:
            weights *= max_gross_from_margin / gross_target
            gross_target = max_gross_from_margin

        actual_weights = exposures / max(equity, 1e-12)
        turnover = float(np.abs(weights - actual_weights).sum())
        trading_cost = max(equity, 0.0) * turnover * rate
        equity -= trading_cost
        exposures = weights * max(equity, 0.0)

        valid_intraday = np.isfinite(open_[i]) & np.isfinite(settle[i]) & (open_[i] > 0)
        intraday = np.zeros(len(columns), dtype=float)
        intraday[valid_intraday] = exposures[valid_intraday] * (
            settle[i, valid_intraday] / open_[i, valid_intraday] - 1.0
        )
        intraday_pnl = float(intraday.sum())

        adverse_price = np.where(exposures >= 0, low[i], high[i])
        adverse_valid = np.isfinite(adverse_price) & np.isfinite(open_[i]) & (open_[i] > 0)
        adverse_pnl = np.zeros(len(columns), dtype=float)
        adverse_pnl[adverse_valid] = exposures[adverse_valid] * (
            adverse_price[adverse_valid] / open_[i, adverse_valid] - 1.0
        )
        adverse_notional = np.zeros(len(columns), dtype=float)
        adverse_notional[adverse_valid] = np.abs(
            exposures[adverse_valid] * adverse_price[adverse_valid] / open_[i, adverse_valid]
        )
        maintenance = audit.maintenance_margin_ratio * float(adverse_notional.sum())
        reserve = audit.operational_reserve * max(equity, 0.0)
        margin_buffer = (
            equity + float(adverse_pnl.sum()) - maintenance - reserve
        ) / max(equity, 1e-12)
        if margin_buffer < 0 and np.abs(exposures).sum() > 0:
            liquidated = float(np.abs(exposures).sum())
            penalty = liquidated * FORCED_EXIT_PENALTY
            equity -= penalty
            forced_cost += penalty
            exposures[:] = 0.0
            intraday_pnl = 0.0
        else:
            equity += intraday_pnl
            exposures[valid_intraday] *= settle[i, valid_intraday] / open_[i, valid_intraday]
            exposures[~valid_intraday] = 0.0

        previous_settle = settle[i].copy()
        gross = float(np.abs(exposures).sum() / max(equity, 1e-12))
        net = float(exposures.sum() / max(equity, 1e-12))
        daily_return = equity / max(previous_equity, 1e-12) - 1.0
        previous_equity = equity
        records.append(
            {
                "date": day,
                "equity": equity,
                "daily_return": daily_return,
                "gross": gross,
                "net": net,
                "turnover": turnover,
                "trading_cost": trading_cost,
                "forced_cost": forced_cost,
                "overnight_pnl": overnight_pnl,
                "intraday_pnl": intraday_pnl,
                "min_margin_buffer": margin_buffer,
                "liquidated_notional": liquidated,
            }
        )
    return pd.DataFrame(records).set_index("date")


def metrics(account: pd.DataFrame) -> dict[str, float]:
    if account.empty:
        return {
            "total_return": 0.0,
            "annualized_return": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "annual_turnover": 0.0,
            "average_gross": 0.0,
            "max_gross": 0.0,
            "costs": 0.0,
            "liquidations": 0,
            "min_margin_buffer": 1.0,
        }
    returns = account["daily_return"].fillna(0.0)
    total = float((1.0 + returns).prod() - 1.0)
    elapsed = max((account.index[-1] - account.index[0]).days / 365.2425, 1 / 365.2425)
    annual = float((1.0 + total) ** (1.0 / elapsed) - 1.0) if total > -1 else -1.0
    equity = (1.0 + returns).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    observations_per_year = len(account) / elapsed
    std = float(returns.std(ddof=1))
    sharpe = float(returns.mean() / std * math.sqrt(observations_per_year)) if std > 0 else 0.0
    return {
        "total_return": total,
        "annualized_return": annual,
        "max_drawdown": float(drawdown.min()),
        "sharpe": sharpe,
        "annual_turnover": float(account["turnover"].sum() / elapsed),
        "average_gross": float(account["gross"].mean()),
        "max_gross": float(account["gross"].max()),
        "costs": float(account["trading_cost"].sum() + account["forced_cost"].sum()),
        "liquidations": int((account["liquidated_notional"] > 0).sum()),
        "min_margin_buffer": float(account["min_margin_buffer"].min()),
        "observations_per_year": observations_per_year,
    }


def period(account: pd.DataFrame, start: str, end: str) -> dict[str, float]:
    return metrics(account[(account.index >= pd.Timestamp(start)) & (account.index < pd.Timestamp(end))])


def annual_returns(account: pd.DataFrame, label: str) -> pd.DataFrame:
    rows = []
    for year, group in account["daily_return"].fillna(0.0).groupby(account.index.year):
        rows.append({"year": int(year), label: float((1.0 + group).prod() - 1.0)})
    return pd.DataFrame(rows)


def combine_separate_accounts(
    atlas: pd.DataFrame,
    sleeve: pd.DataFrame,
    sleeve_weight: float,
    transfer_bps: float = 5.0,
) -> pd.DataFrame:
    index = atlas.index.union(sleeve.index).sort_values()
    atlas_return = atlas["daily_return"].reindex(index).fillna(0.0)
    sleeve_return = sleeve["daily_return"].reindex(index).fillna(0.0)
    atlas_equity = (1.0 - sleeve_weight) * 10_000.0 * (1.0 + atlas_return).cumprod()
    sleeve_equity = sleeve_weight * 10_000.0 * (1.0 + sleeve_return).cumprod()
    initial_transfer = 10_000.0 * sleeve_weight * transfer_bps / 10_000.0
    sleeve_equity -= initial_transfer
    total = atlas_equity + sleeve_equity
    output = pd.DataFrame(index=index)
    output["equity"] = total
    output["daily_return"] = total.pct_change().fillna(total.iloc[0] / 10_000.0 - 1.0)
    output["gross"] = 0.0
    output["turnover"] = 0.0
    output["trading_cost"] = 0.0
    output["forced_cost"] = 0.0
    output["min_margin_buffer"] = 1.0
    output["liquidated_notional"] = 0.0
    return output
