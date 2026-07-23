from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SimulationSettings:
    starting_equity: float = 10_000.0
    target_gross_cap: float = 0.85
    initial_scale: float = 1.0
    first_high_water_multiple: float = 1.5
    first_reduced_scale: float = 1.0
    second_high_water_multiple: float = 2.0
    second_reduced_scale: float = 0.75
    ratchet: bool = False


def _utc(value: str | pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def simulate(
    data,
    spot_signal: pd.DataFrame,
    perp_open: pd.DataFrame,
    perp_close: pd.DataFrame,
    funding: pd.DataFrame,
    perp_signal: pd.DataFrame,
    start: str,
    end: str,
    *,
    spot_cost_rate: float,
    perp_cost_rate: float,
    forced_penalty_rate: float,
    settings: SimulationSettings,
) -> pd.DataFrame:
    """Event-driven next-open simulation.

    A signal observed at today's close is eligible only at tomorrow's open.
    Existing spot and perpetual positions receive the close-to-next-open move
    before the next rebalance. Spot delistings are conservatively liquidated
    with the larger of the normal trading cost and forced-listing penalty.
    """
    begin, finish = _utc(start), _utc(end)
    locations = np.flatnonzero((data.index >= begin) & (data.index < finish))
    if len(locations) < 2:
        raise ValueError("simulation period is too short")

    spot_open = data.open.to_numpy(float)
    spot_close = data.close.to_numpy(float)
    available = data.available.to_numpy(bool)
    perp_open_values = perp_open.reindex(data.index).to_numpy(float)
    perp_close_values = perp_close.reindex(data.index).to_numpy(float)
    funding_values = funding.reindex(data.index).fillna(0.0).to_numpy(float)
    spot_signals = spot_signal.reindex(data.index).fillna(0.0).to_numpy(float)
    perp_signals = perp_signal.reindex(data.index).fillna(0.0).to_numpy(float)

    spot_count = spot_open.shape[1]
    perp_count = perp_open_values.shape[1]
    pending_spot = (
        spot_signals[locations[0] - 1].copy()
        if locations[0] > 0
        else np.zeros(spot_count, dtype=float)
    )
    pending_perp = (
        perp_signals[locations[0] - 1].copy()
        if locations[0] > 0
        else np.zeros(perp_count, dtype=float)
    )

    spot_values = np.zeros(spot_count, dtype=float)
    perp_notionals = np.zeros(perp_count, dtype=float)
    cash = float(settings.starting_equity)
    initial_equity = float(settings.starting_equity)
    high_water = initial_equity
    locked_scale = float(settings.initial_scale)
    previous_location: int | None = None
    rows: list[dict[str, float]] = []

    for location in locations:
        forced_notional = 0.0
        forced_cost = 0.0

        if previous_location is not None:
            for column in np.flatnonzero(spot_values > 0.0):
                previous_close = spot_close[previous_location, column]
                current_open = spot_open[location, column]
                if np.isfinite(previous_close) and np.isfinite(current_open):
                    spot_values[column] *= current_open / previous_close
                else:
                    notional = float(spot_values[column])
                    penalty = notional * max(spot_cost_rate, forced_penalty_rate)
                    cash += max(0.0, notional - penalty)
                    spot_values[column] = 0.0
                    forced_notional += notional
                    forced_cost += penalty

            overnight_ratio = np.divide(
                perp_open_values[location],
                perp_close_values[previous_location],
                out=np.ones(perp_count, dtype=float),
                where=(
                    np.isfinite(perp_open_values[location])
                    & np.isfinite(perp_close_values[previous_location])
                ),
            )
            cash += float(np.sum(perp_notionals * (overnight_ratio - 1.0)))
            perp_notionals *= overnight_ratio

        equity_open = float(cash + spot_values.sum())
        if equity_open <= 0.0:
            raise RuntimeError("account equity became non-positive")

        if settings.ratchet:
            if high_water >= settings.second_high_water_multiple * initial_equity:
                locked_scale = min(locked_scale, settings.second_reduced_scale)
            elif high_water >= settings.first_high_water_multiple * initial_equity:
                locked_scale = min(locked_scale, settings.first_reduced_scale)

        actual_spot = spot_values / equity_open
        actual_perp = perp_notionals / equity_open

        target_spot = np.nan_to_num(pending_spot.copy(), nan=0.0, posinf=0.0, neginf=0.0)
        target_spot[(~available[location]) | (target_spot < 0.0)] = 0.0
        target_perp = np.nan_to_num(pending_perp.copy(), nan=0.0, posinf=0.0, neginf=0.0)

        target_spot *= locked_scale
        target_perp *= locked_scale
        target_gross = float(target_spot.sum() + np.abs(target_perp).sum())
        if target_gross > settings.target_gross_cap:
            multiplier = settings.target_gross_cap / target_gross
            target_spot *= multiplier
            target_perp *= multiplier

        spot_turnover = float(np.abs(target_spot - actual_spot).sum())
        perp_turnover = float(np.abs(target_perp - actual_perp).sum())
        transaction_cost = equity_open * (
            spot_turnover * spot_cost_rate + perp_turnover * perp_cost_rate
        )
        after_cost = max(0.0, equity_open - transaction_cost)

        spot_values = target_spot * after_cost
        cash = after_cost - float(spot_values.sum())
        perp_notionals = target_perp * after_cost

        intraday_spot_ratio = np.divide(
            spot_close[location],
            spot_open[location],
            out=np.ones(spot_count, dtype=float),
            where=np.isfinite(spot_open[location]) & np.isfinite(spot_close[location]),
        )
        spot_values *= intraday_spot_ratio

        intraday_perp_ratio = np.divide(
            perp_close_values[location],
            perp_open_values[location],
            out=np.ones(perp_count, dtype=float),
            where=(
                np.isfinite(perp_open_values[location])
                & np.isfinite(perp_close_values[location])
            ),
        )
        cash += float(np.sum(perp_notionals * (intraday_perp_ratio - 1.0)))
        funding_pnl = float(np.sum(-(perp_notionals * funding_values[location])))
        cash += funding_pnl
        perp_notionals *= intraday_perp_ratio

        equity = float(cash + spot_values.sum())
        high_water = max(high_water, equity)
        gross = float((spot_values.sum() + np.abs(perp_notionals).sum()) / equity)
        turnover = (
            spot_turnover
            + perp_turnover
            + (forced_notional / equity_open if equity_open > 0.0 else 0.0)
        )
        rows.append(
            {
                "equity": equity,
                "spot_gross": float(spot_values.sum() / equity),
                "perp_gross": float(np.abs(perp_notionals).sum() / equity),
                "gross": gross,
                "turnover": turnover,
                "costs": transaction_cost + forced_cost,
                "funding_pnl": funding_pnl,
                "risk_scale": locked_scale,
                "high_water": high_water,
            }
        )

        pending_spot = spot_signals[location].copy()
        pending_perp = perp_signals[location].copy()
        previous_location = int(location)

    return pd.DataFrame(rows, index=data.index[locations])


def metrics(account: pd.DataFrame) -> dict[str, float]:
    equity = account["equity"].dropna()
    returns = equity.pct_change().dropna()
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    elapsed_days = max(
        (equity.index[-1] - equity.index[0]).total_seconds() / 86_400.0,
        1.0,
    )
    annualized = (
        float((1.0 + total_return) ** (365.0 / elapsed_days) - 1.0)
        if total_return > -1.0
        else -1.0
    )
    drawdown = equity / equity.cummax() - 1.0
    standard_deviation = float(returns.std(ddof=1)) if len(returns) > 1 else math.nan
    sharpe = (
        float(np.sqrt(365.0) * returns.mean() / standard_deviation)
        if np.isfinite(standard_deviation) and standard_deviation > 0.0
        else math.nan
    )
    downside = returns[returns < 0.0]
    downside_deviation = float(downside.std(ddof=1)) if len(downside) > 1 else math.nan
    sortino = (
        float(np.sqrt(365.0) * returns.mean() / downside_deviation)
        if np.isfinite(downside_deviation) and downside_deviation > 0.0
        else math.nan
    )
    max_drawdown = float(drawdown.min())
    years = max(elapsed_days / 365.0, 1.0 / 365.0)
    return {
        "total_return": total_return,
        "annualized_return": annualized,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": float(annualized / abs(max_drawdown)) if max_drawdown < 0.0 else math.nan,
        "worst_day": float(returns.min()) if len(returns) else math.nan,
        "annual_turnover": float(account["turnover"].sum() / years),
        "average_gross": float(account["gross"].mean()),
        "max_gross": float(account["gross"].max()),
        "total_costs": float(account["costs"].sum()),
        "funding_pnl": float(account["funding_pnl"].sum()),
        "final_equity": float(equity.iloc[-1]),
        "ending_risk_scale": float(account["risk_scale"].iloc[-1]),
    }


def rolling_diagnostics(account: pd.DataFrame, window: int = 365) -> dict[str, float]:
    equity = account["equity"]
    rolling = (equity / equity.shift(window) - 1.0).dropna()
    return {
        f"rolling_{window}_windows": float(len(rolling)),
        f"rolling_{window}_positive_share": (
            float((rolling > 0.0).mean()) if len(rolling) else math.nan
        ),
        f"rolling_{window}_worst": float(rolling.min()) if len(rolling) else math.nan,
        f"rolling_{window}_median": float(rolling.median()) if len(rolling) else math.nan,
    }
