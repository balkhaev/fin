from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from config import (
    ASSETS,
    BAR_HOURS,
    INITIAL_EQUITY,
    TARGET_GROSS,
    Audit,
    Policy,
)


@dataclass(slots=True)
class PreparedMarket:
    index: pd.DatetimeIndex
    frames: dict[str, pd.DataFrame]


def _finite(value: Any) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def prepare(markets: dict[str, pd.DataFrame]) -> PreparedMarket:
    nonempty = [frame for frame in markets.values() if not frame.empty]
    if not nonempty:
        raise ValueError("no market data")
    index = pd.DatetimeIndex(nonempty[0].timestamp)
    for frame in nonempty[1:]:
        index = index.union(pd.DatetimeIndex(frame.timestamp))
    index = index.sort_values()
    frames: dict[str, pd.DataFrame] = {}
    for asset in ASSETS:
        source = markets[asset].copy()
        source["timestamp"] = pd.to_datetime(source["timestamp"], utc=True)
        frame = source.set_index("timestamp").sort_index().reindex(index)
        basis = pd.to_numeric(frame.basis_close_bps, errors="coerce")
        funding = pd.to_numeric(frame.funding_spread_event, errors="coerce").fillna(0.0)
        funding_24h = funding.rolling(24, min_periods=24).sum()
        for lookback in (168, 336):
            history = basis.shift(1)
            mean = history.rolling(lookback, min_periods=lookback).mean()
            std = history.rolling(lookback, min_periods=lookback).std(ddof=1)
            frame[f"basis_mean_{lookback}"] = mean
            frame[f"basis_deviation_{lookback}"] = basis - mean
            frame[f"basis_z_{lookback}"] = (basis - mean) / std.replace(0.0, np.nan)
            frame[f"funding_forecast_{lookback}"] = funding.rolling(
                lookback, min_periods=lookback
            ).mean()
            funding_history = funding_24h.shift(1)
            funding_mean = funding_history.rolling(lookback, min_periods=lookback).mean()
            funding_std = funding_history.rolling(lookback, min_periods=lookback).std(ddof=1)
            frame[f"funding_z_{lookback}"] = (
                funding_24h - funding_mean
            ) / funding_std.replace(0.0, np.nan)
        frames[asset] = frame
    return PreparedMarket(index=index, frames=frames)


def _prices(frame: pd.DataFrame, i: int) -> tuple[float, float] | None:
    if i < 0 or i >= len(frame):
        return None
    usdm = frame.open_usdm.iloc[i] if "open_usdm" in frame else np.nan
    coinm = frame.open_coinm.iloc[i] if "open_coinm" in frame else np.nan
    if not (_finite(usdm) and _finite(coinm)) or float(usdm) <= 0 or float(coinm) <= 0:
        return None
    return float(usdm), float(coinm)


def _signal_for(
    frame: pd.DataFrame,
    policy: Policy,
    i: int,
    timestamp: pd.Timestamp,
    delay: int,
) -> dict[str, Any] | None:
    lookback = policy.lookback_hours
    basis_z = frame[f"basis_z_{lookback}"].iloc[i]
    funding_z = frame[f"funding_z_{lookback}"].iloc[i]
    basis_dev = frame[f"basis_deviation_{lookback}"].iloc[i]
    funding_forecast = frame[f"funding_forecast_{lookback}"].iloc[i]
    basis = frame.basis_close_bps.iloc[i]
    if not _finite(basis):
        return None

    direction: int
    predicted_edge_bps: float
    if policy.family == "dual_perp_basis_convergence":
        if not (_finite(basis_z) and _finite(basis_dev)):
            return None
        if abs(float(basis_z)) < policy.entry_abs_z or float(basis_dev) == 0.0:
            return None
        direction = -1 if float(basis_dev) > 0 else 1
        predicted_edge_bps = abs(float(basis_dev))
    elif policy.family == "funding_spread_carry":
        if not (_finite(funding_z) and _finite(funding_forecast)):
            return None
        if abs(float(funding_z)) < policy.entry_abs_z or float(funding_forecast) == 0.0:
            return None
        direction = 1 if float(funding_forecast) > 0 else -1
        predicted_edge_bps = abs(float(funding_forecast)) * policy.hold_hours * 10_000.0
    elif policy.family == "funding_basis_joint":
        if not all(_finite(value) for value in (basis_z, basis_dev, funding_z, funding_forecast)):
            return None
        if abs(float(basis_z)) < policy.entry_abs_z or abs(float(funding_z)) < policy.entry_abs_z:
            return None
        convergence_direction = -1 if float(basis_dev) > 0 else 1
        funding_direction = 1 if float(funding_forecast) > 0 else -1
        if convergence_direction != funding_direction:
            return None
        direction = convergence_direction
        predicted_edge_bps = abs(float(basis_dev)) + (
            abs(float(funding_forecast)) * policy.hold_hours * 10_000.0
        )
    elif policy.family == "reversed_dual_perp_control":
        if not (_finite(basis_z) and _finite(basis_dev)):
            return None
        if abs(float(basis_z)) < policy.entry_abs_z or float(basis_dev) == 0.0:
            return None
        direction = 1 if float(basis_dev) > 0 else -1
        predicted_edge_bps = abs(float(basis_dev))
    else:
        raise ValueError(policy.family)

    if predicted_edge_bps < policy.minimum_expected_edge_bps:
        return None
    adverse_deviation = max(0.0, direction * float(basis_dev)) if _finite(basis_dev) else 0.0
    score = predicted_edge_bps - adverse_deviation
    return {
        "target_index": i + 1 + delay,
        "signal_time": timestamp,
        "direction": direction,
        "predicted_edge_bps": predicted_edge_bps,
        "score": score,
        "signal_basis_bps": float(basis),
        "basis_deviation_bps": float(basis_dev) if _finite(basis_dev) else np.nan,
        "basis_z": float(basis_z) if _finite(basis_z) else np.nan,
        "funding_z": float(funding_z) if _finite(funding_z) else np.nan,
        "funding_forecast": float(funding_forecast) if _finite(funding_forecast) else np.nan,
    }


def simulate(
    prepared: PreparedMarket,
    policy: Policy,
    audit: Audit,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    index = prepared.index
    n = len(index)
    equity_values = np.full(n, INITIAL_EQUITY, dtype=float)
    gross_values = np.zeros(n, dtype=float)
    turnover_values = np.zeros(n, dtype=float)
    costs_values = np.zeros(n, dtype=float)
    funding_values = np.zeros(n, dtype=float)
    price_values = np.zeros(n, dtype=float)
    trade_events = np.zeros(n, dtype=float)
    forced_exits = np.zeros(n, dtype=float)

    realized = INITIAL_EQUITY
    marked = INITIAL_EQUITY
    active: dict[str, Any] | None = None
    pending: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []

    def current_mark(position: dict[str, Any], i: int) -> tuple[float, float, float, float, float] | None:
        frame = prepared.frames[position["asset"]]
        current = _prices(frame, i)
        if current is None:
            return None
        usdm_price, coinm_price = current
        direction = float(position["direction"])
        q = float(position["q_base"])
        price_pnl = direction * q * (coinm_price - position["entry_coinm"])
        price_pnl -= direction * q * (usdm_price - position["entry_usdm"])
        gross_notional = q * (usdm_price + coinm_price)
        value = position["capital_after_entry"] + price_pnl + position["cumulative_funding"]
        return value, price_pnl, gross_notional, usdm_price, coinm_price

    def close_position(
        position: dict[str, Any],
        i: int,
        reason: str,
        *,
        forced: bool,
    ) -> float:
        nonlocal marked
        mark = current_mark(position, i)
        if mark is None:
            value = marked
            price_pnl = float(position["last_price_pnl"])
            usdm_price = float(position["last_usdm"])
            coinm_price = float(position["last_coinm"])
            gross_notional = position["q_base"] * (usdm_price + coinm_price)
        else:
            value, price_pnl, gross_notional, usdm_price, coinm_price = mark
        exit_cost = max(0.0, gross_notional) * audit.one_way_rate
        extra = (
            max(0.0, gross_notional) * audit.forced_exit_extra_bps / 10_000.0
            if forced and reason != "end_of_sample"
            else 0.0
        )
        after = max(0.0, value - exit_cost - extra)
        costs_values[i] += exit_cost + extra
        turnover_values[i] += gross_notional / max(value, 1e-12)
        trade_events[i] += 1.0
        if forced and reason != "end_of_sample":
            forced_exits[i] += 1.0
        before = float(position["capital_before_entry"])
        trades.append(
            {
                "policy": policy.name,
                "family": policy.family,
                "asset": position["asset"],
                "direction": position["direction"],
                "signal_time": position["signal_time"],
                "entry_time": position["entry_time"],
                "exit_time": index[i],
                "entry_usdm": position["entry_usdm"],
                "entry_coinm": position["entry_coinm"],
                "exit_usdm": usdm_price,
                "exit_coinm": coinm_price,
                "q_base": position["q_base"],
                "coinm_contract_notional_usd": position["coinm_contract_notional_usd"],
                "predicted_edge_bps": position["predicted_edge_bps"],
                "signal_basis_bps": position["signal_basis_bps"],
                "basis_deviation_bps": position["basis_deviation_bps"],
                "basis_z": position["basis_z"],
                "funding_z": position["funding_z"],
                "funding_forecast": position["funding_forecast"],
                "holding_hours": i - position["entry_index"],
                "exit_reason": reason,
                "price_pnl": price_pnl,
                "funding_pnl": position["cumulative_funding"],
                "entry_cost": position["entry_cost"],
                "exit_cost": exit_cost,
                "extra_penalty": extra,
                "capital_before": before,
                "equity_after": after,
                "net_pnl": after - before,
                "net_return": after / before - 1.0 if before > 0 else -1.0,
            }
        )
        marked = after
        return after

    def enter(signal: dict[str, Any], i: int) -> dict[str, Any] | None:
        nonlocal realized, marked
        frame = prepared.frames[signal["asset"]]
        current = _prices(frame, i)
        if current is None:
            return None
        usdm_price, coinm_price = current
        capital_before = realized
        q_base = TARGET_GROSS * capital_before / (usdm_price + coinm_price)
        gross_notional = q_base * (usdm_price + coinm_price)
        entry_cost = gross_notional * audit.one_way_rate
        capital_after = max(0.0, capital_before - entry_cost)
        costs_values[i] += entry_cost
        turnover_values[i] += gross_notional / max(capital_before, 1e-12)
        trade_events[i] += 1.0
        realized = capital_after
        marked = capital_after
        return {
            **signal,
            "entry_index": i,
            "entry_time": index[i],
            "exit_index": i + policy.hold_hours,
            "capital_before_entry": capital_before,
            "capital_after_entry": capital_after,
            "entry_cost": entry_cost,
            "entry_usdm": usdm_price,
            "entry_coinm": coinm_price,
            "q_base": q_base,
            "coinm_contract_notional_usd": q_base * coinm_price,
            "cumulative_funding": 0.0,
            "last_usdm": usdm_price,
            "last_coinm": coinm_price,
            "last_price_pnl": 0.0,
            "reported_price_pnl": 0.0,
        }

    for i, timestamp in enumerate(index):
        if active is not None and i > active["entry_index"]:
            frame = prepared.frames[active["asset"]]
            mark = current_mark(active, i)
            if mark is None:
                realized = close_position(active, i, "missing_price", forced=True)
                active = None
                marked = realized
            else:
                value, px_pnl, gross_notional, usdm_price, coinm_price = mark
                direction = float(active["direction"])
                usdm_rate = float(frame.funding_usdm.iloc[i]) if _finite(frame.funding_usdm.iloc[i]) else 0.0
                coinm_rate = float(frame.funding_coinm.iloc[i]) if _finite(frame.funding_coinm.iloc[i]) else 0.0
                funding_flow = direction * (
                    active["q_base"] * usdm_price * usdm_rate
                    - active["coinm_contract_notional_usd"] * coinm_rate
                )
                active["cumulative_funding"] += funding_flow
                funding_values[i] += funding_flow
                active["last_usdm"] = usdm_price
                active["last_coinm"] = coinm_price
                active["last_price_pnl"] = px_pnl
                mark = current_mark(active, i)
                if mark is not None:
                    marked, px_pnl, gross_notional, _, _ = mark
                    price_values[i] = px_pnl - float(active["reported_price_pnl"])
                    active["reported_price_pnl"] = px_pnl
                    gross_values[i] = gross_notional / max(marked, 1e-12)

        if active is not None and i >= active["exit_index"]:
            realized = close_position(active, i, "fixed_horizon", forced=False)
            active = None
            marked = realized
            gross_values[i] = 0.0

        if active is None and pending is not None and i >= pending["target_index"]:
            active = enter(pending, i)
            pending = None
            if active is not None:
                gross_values[i] = TARGET_GROSS

        if active is None and pending is None:
            choices: list[tuple[float, str, dict[str, Any]]] = []
            for asset in ASSETS:
                frame = prepared.frames[asset]
                if i >= len(frame) or _prices(frame, i) is None:
                    continue
                signal = _signal_for(
                    frame,
                    policy,
                    i,
                    timestamp,
                    audit.execution_delay_hours,
                )
                if signal is not None and signal["target_index"] < n:
                    signal["asset"] = asset
                    choices.append((float(signal["score"]), asset, signal))
            if choices:
                _, _, pending = max(choices, key=lambda item: (item[0], item[1]))

        if active is not None:
            mark = current_mark(active, i)
            if mark is not None:
                marked, _, gross_notional, _, _ = mark
                gross_values[i] = gross_notional / max(marked, 1e-12)
        else:
            marked = realized
        equity_values[i] = max(0.0, marked)

    if active is not None and n:
        i = n - 1
        realized = close_position(active, i, "end_of_sample", forced=False)
        equity_values[i] = realized
        gross_values[i] = 0.0

    account = pd.DataFrame(
        {
            "equity": equity_values,
            "gross": gross_values,
            "turnover": turnover_values,
            "costs": costs_values,
            "funding_pnl": funding_values,
            "price_pnl": price_values,
            "trade_events": trade_events,
            "forced_exits": forced_exits,
        },
        index=index,
    )
    return account, pd.DataFrame(trades)


def elapsed_years(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 0.0
    return max((index[-1] - index[0]).total_seconds() / (365.2425 * 86400.0), 1 / 365.2425)


def metrics(account: pd.DataFrame) -> dict[str, float | int]:
    if account.empty:
        return {
            "total_return": 0.0,
            "cagr": 0.0,
            "max_drawdown": 0.0,
            "sharpe": 0.0,
            "annual_turnover": 0.0,
            "average_gross": 0.0,
            "max_gross": 0.0,
            "costs": 0.0,
            "funding_pnl": 0.0,
            "trade_count": 0,
            "forced_exits": 0,
            "observations_per_year": 0.0,
        }
    years = elapsed_years(account.index)
    final = float(account.equity.iloc[-1])
    total_return = final / INITIAL_EQUITY - 1.0
    cagr = (final / INITIAL_EQUITY) ** (1.0 / years) - 1.0 if years > 0 and final > 0 else -1.0
    returns = account.equity.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    observations_per_year = len(returns) / years if years > 0 else 0.0
    std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    sharpe = float(returns.mean() / std * np.sqrt(observations_per_year)) if std > 0 else 0.0
    drawdown = account.equity / account.equity.cummax() - 1.0
    return {
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": float(drawdown.min()),
        "sharpe": sharpe,
        "annual_turnover": float(account.turnover.sum() / years) if years > 0 else 0.0,
        "average_gross": float(account.gross.mean()),
        "max_gross": float(account.gross.max()),
        "costs": float(account.costs.sum()),
        "funding_pnl": float(account.funding_pnl.sum()),
        "trade_count": int(account.trade_events.sum() // 2),
        "forced_exits": int(account.forced_exits.sum()),
        "observations_per_year": observations_per_year,
    }


def slice_account(account: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    selected = account[(account.index >= start_ts) & (account.index <= end_ts)].copy()
    if selected.empty:
        return selected
    before = account[account.index < start_ts]
    base = float(before.equity.iloc[-1]) if not before.empty else INITIAL_EQUITY
    scale = INITIAL_EQUITY / base
    for column in ("equity", "costs", "funding_pnl", "price_pnl"):
        selected[column] *= scale
    return selected


def yearly_returns(account: pd.DataFrame, name: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    previous = INITIAL_EQUITY
    for year, frame in account.groupby(account.index.year):
        end_value = float(frame.equity.iloc[-1])
        rows.append({"year": int(year), name: end_value / previous - 1.0})
        previous = end_value
    return pd.DataFrame(rows)


def policy_dict(policy: Policy) -> dict[str, Any]:
    return asdict(policy) | {"name": policy.name}
