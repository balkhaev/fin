from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from config import (
    ASSETS,
    BLOCK_HOURS,
    INITIAL_EQUITY,
    LEG_GROSS,
    TARGET_GROSS,
    Audit,
    Policy,
)


@dataclass(slots=True)
class PreparedMarket:
    index: pd.DatetimeIndex
    frames: dict[str, pd.DataFrame]


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
        source = markets.get(asset, pd.DataFrame()).copy()
        if source.empty:
            frame = pd.DataFrame(index=index)
        else:
            source["timestamp"] = pd.to_datetime(source["timestamp"], utc=True)
            frame = source.set_index("timestamp").sort_index().reindex(index)
        for lookback in (3, 6, 12):
            spread = pd.to_numeric(frame.get("funding_spread"), errors="coerce")
            complete = frame.get(
                "funding_complete", pd.Series(False, index=index, dtype=bool)
            )
            complete = complete.astype("boolean").fillna(False).astype(bool)
            lagged = spread.shift(1).where(complete.shift(1, fill_value=False))
            median = lagged.rolling(lookback, min_periods=lookback).median()
            same_sign = np.sign(lagged).eq(np.sign(median)) & lagged.ne(0) & median.ne(0)
            magnitude = pd.concat([lagged.abs(), median.abs()], axis=1).min(axis=1)
            forecast = np.sign(median) * magnitude
            frame[f"forecast_{lookback}"] = forecast.where(same_sign)
        frames[asset] = frame
    return PreparedMarket(index=index, frames=frames)


def _finite(value: Any) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def _prices(frame: pd.DataFrame, i: int) -> tuple[float, float] | None:
    if i < 0 or i >= len(frame):
        return None
    b = frame.open_binance.iloc[i] if "open_binance" in frame else np.nan
    h = frame.open_hyperliquid.iloc[i] if "open_hyperliquid" in frame else np.nan
    if not (_finite(b) and _finite(h)) or float(b) <= 0 or float(h) <= 0:
        return None
    return float(b), float(h)


def simulate(
    prepared: PreparedMarket,
    policy: Policy,
    audit: Audit,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    index = prepared.index
    n = len(index)
    equity_values = np.full(n, INITIAL_EQUITY, dtype=float)
    gross_values = np.zeros(n, dtype=float)
    turnover = np.zeros(n, dtype=float)
    costs = np.zeros(n, dtype=float)
    funding_pnl = np.zeros(n, dtype=float)
    price_pnl = np.zeros(n, dtype=float)
    missing_penalties = np.zeros(n, dtype=float)
    trade_events = np.zeros(n, dtype=float)
    forced_exits = np.zeros(n, dtype=float)

    realized = INITIAL_EQUITY
    marked = INITIAL_EQUITY
    active: dict[str, Any] | None = None
    pending: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []

    def current_mark(position: dict[str, Any], i: int) -> tuple[float, float, float, float] | None:
        frame = prepared.frames[position["asset"]]
        current = _prices(frame, i)
        if current is None:
            return None
        binance_price, hl_price = current
        direction = float(position["direction"])
        px_pnl = direction * position["q_hl"] * (hl_price - position["entry_hl"])
        px_pnl -= direction * position["q_binance"] * (
            binance_price - position["entry_binance"]
        )
        gross_notional = (
            abs(position["q_hl"] * hl_price)
            + abs(position["q_binance"] * binance_price)
        )
        value = (
            position["capital_after_entry"]
            + px_pnl
            + position["cumulative_funding"]
            - position["cumulative_penalty"]
        )
        return value, px_pnl, gross_notional, binance_price, hl_price

    def close_position(
        position: dict[str, Any],
        i: int,
        reason: str,
        *,
        extra_penalty: bool,
    ) -> float:
        nonlocal marked
        mark = current_mark(position, i)
        if mark is None:
            binance_price = position["last_binance"]
            hl_price = position["last_hl"]
            value = marked
            px_pnl = position["last_price_pnl"]
            gross_notional = (
                abs(position["q_hl"] * hl_price)
                + abs(position["q_binance"] * binance_price)
            )
        else:
            value, px_pnl, gross_notional, binance_price, hl_price = mark
        exit_cost = max(0.0, gross_notional) * audit.trade_rate
        penalty = (
            max(0.0, gross_notional) * audit.forced_exit_extra_bps / 10_000.0
            if extra_penalty
            else 0.0
        )
        after = max(0.0, value - exit_cost - penalty)
        costs[i] += exit_cost
        missing_penalties[i] += penalty
        turnover[i] += gross_notional / max(value, 1e-12)
        trade_events[i] += 1.0
        if extra_penalty and reason != "end_of_sample":
            forced_exits[i] += 1.0
        capital_before = float(position["capital_before_entry"])
        trades.append(
            {
                "asset": position["asset"],
                "direction": position["direction"],
                "signal_time": position["signal_time"],
                "entry_time": position["entry_time"],
                "exit_time": index[i],
                "entry_binance": position["entry_binance"],
                "entry_hyperliquid": position["entry_hl"],
                "exit_binance": binance_price,
                "exit_hyperliquid": hl_price,
                "forecast_spread": position["forecast_spread"],
                "predicted_edge_bps": position["predicted_edge_bps"],
                "entry_basis_bps": position["entry_basis_bps"],
                "holding_blocks": i - position["entry_index"],
                "holding_hours": (i - position["entry_index"]) * BLOCK_HOURS,
                "exit_reason": reason,
                "price_pnl": px_pnl,
                "funding_pnl": position["cumulative_funding"],
                "entry_cost": position["entry_cost"],
                "exit_cost": exit_cost,
                "penalty": position["cumulative_penalty"] + penalty,
                "capital_before": capital_before,
                "equity_after": after,
                "net_return": after / capital_before - 1.0 if capital_before > 0 else -1.0,
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
        binance_price, hl_price = current
        capital_before = realized
        # Equal base quantity removes residual directional delta.  Solve for q so
        # post-entry equity equals marked gross notional after entry costs.
        price_sum = binance_price + hl_price
        q = capital_before / (price_sum * (1.0 + audit.trade_rate))
        gross_notional = q * price_sum
        entry_cost = gross_notional * audit.trade_rate
        capital_after = max(0.0, capital_before - entry_cost)
        q_binance = q
        q_hl = q
        costs[i] += entry_cost
        turnover[i] += gross_notional / max(capital_before, 1e-12)
        trade_events[i] += 1.0
        realized = capital_after
        marked = capital_after
        return {
            **signal,
            "entry_index": i,
            "entry_time": index[i],
            "exit_index": i + policy.hold_blocks,
            "capital_before_entry": capital_before,
            "capital_after_entry": capital_after,
            "entry_cost": entry_cost,
            "entry_binance": binance_price,
            "entry_hl": hl_price,
            "q_binance": q_binance,
            "q_hl": q_hl,
            "cumulative_funding": 0.0,
            "cumulative_penalty": 0.0,
            "last_binance": binance_price,
            "last_hl": hl_price,
            "last_price_pnl": 0.0,
            "reported_price_pnl": 0.0,
        }

    for i, timestamp in enumerate(index):
        # Accrue the completed block [i-1, i) before any exit or new entry at i.
        if active is not None and i > active["entry_index"]:
            frame = prepared.frames[active["asset"]]
            mark = current_mark(active, i)
            if mark is None:
                realized = close_position(active, i, "missing_price", extra_penalty=True)
                active = None
            else:
                value, px_value, gross_notional, binance_price, hl_price = mark
                block = frame.iloc[i - 1]
                if bool(block.get("funding_complete", False)) and _finite(
                    block.get("funding_binance")
                ) and _finite(block.get("funding_hyperliquid")):
                    direction = float(active["direction"])
                    flow = direction * (
                        active["q_binance"]
                        * binance_price
                        * float(block.funding_binance)
                        - active["q_hl"]
                        * hl_price
                        * float(block.funding_hyperliquid)
                    )
                    active["cumulative_funding"] += flow
                    funding_pnl[i] += flow
                else:
                    penalty = (
                        gross_notional
                        * audit.missing_funding_penalty_bps
                        / 10_000.0
                    )
                    active["cumulative_penalty"] += penalty
                    missing_penalties[i] += penalty
                    active["last_binance"] = binance_price
                    active["last_hl"] = hl_price
                    active["last_price_pnl"] = px_value
                    realized = close_position(
                        active, i, "missing_funding", extra_penalty=True
                    )
                    active = None
                    marked = realized
                if active is not None:
                    active["last_binance"] = binance_price
                    active["last_hl"] = hl_price
                    active["last_price_pnl"] = px_value
                    mark = current_mark(active, i)
                    if mark is not None:
                        marked, px_value, gross_notional, _, _ = mark
                        price_pnl[i] = px_value - float(active["reported_price_pnl"])
                        active["reported_price_pnl"] = px_value
                        gross_values[i] = gross_notional / max(marked, 1e-12)

        if active is not None and i >= active["exit_index"]:
            realized = close_position(active, i, "fixed_horizon", extra_penalty=False)
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
                if i >= len(frame):
                    continue
                forecast = frame[f"forecast_{policy.lookback_blocks}"].iloc[i]
                basis = frame.basis_bps.iloc[i] if "basis_bps" in frame else np.nan
                current = _prices(frame, i)
                if current is None or not (_finite(forecast) and _finite(basis)):
                    continue
                predicted_edge_bps = (
                    LEG_GROSS
                    * abs(float(forecast))
                    * policy.hold_blocks
                    * 10_000.0
                )
                if predicted_edge_bps < policy.min_predicted_edge_bps:
                    continue
                if abs(float(basis)) > policy.max_abs_basis_bps:
                    continue
                direction = 1 if float(forecast) > 0 else -1
                # Penalise entering long on the already-expensive venue.
                adverse_basis = max(0.0, direction * float(basis))
                score = predicted_edge_bps - adverse_basis
                signal = {
                    "target_index": i + audit.execution_delay_blocks,
                    "asset": asset,
                    "direction": direction,
                    "signal_time": timestamp,
                    "forecast_spread": float(forecast),
                    "predicted_edge_bps": predicted_edge_bps,
                    "entry_basis_bps": float(basis),
                }
                choices.append((score, asset, signal))
            if choices:
                _, _, signal = max(choices, key=lambda value: (value[0], value[1]))
                if audit.execution_delay_blocks == 0:
                    active = enter(signal, i)
                    if active is not None:
                        gross_values[i] = TARGET_GROSS
                else:
                    pending = signal

        if active is not None:
            mark = current_mark(active, i)
            if mark is not None:
                marked, px_value, gross_notional, _, _ = mark
                gross_values[i] = gross_notional / max(marked, 1e-12)
        else:
            marked = realized
        equity_values[i] = max(0.0, marked)

    if active is not None and n:
        i = n - 1
        realized = close_position(active, i, "end_of_sample", extra_penalty=True)
        equity_values[i] = realized
        gross_values[i] = 0.0

    account = pd.DataFrame(
        {
            "equity": equity_values,
            "gross": gross_values,
            "turnover": turnover,
            "costs": costs,
            "funding_pnl": funding_pnl,
            "price_pnl": price_pnl,
            "missing_penalties": missing_penalties,
            "trade_events": trade_events,
            "forced_exits": forced_exits,
        },
        index=index,
    )
    return account, pd.DataFrame(trades)


def elapsed_years(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 0.0
    return max(
        (index[-1] - index[0]).total_seconds() / (365.2425 * 86400.0),
        1 / 365.2425,
    )


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
        }
    years = elapsed_years(account.index)
    final = float(account.equity.iloc[-1])
    total_return = final / INITIAL_EQUITY - 1.0
    cagr = (
        (final / INITIAL_EQUITY) ** (1.0 / years) - 1.0
        if years > 0 and final > 0
        else -1.0
    )
    returns = account.equity.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    observations_per_year = len(returns) / years if years > 0 else 0.0
    std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    sharpe = (
        float(returns.mean() / std * np.sqrt(observations_per_year))
        if std > 0
        else 0.0
    )
    drawdown = account.equity / account.equity.cummax() - 1.0
    return {
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": float(drawdown.min()),
        "sharpe": sharpe,
        "annual_turnover": float(account.turnover.sum() / years) if years > 0 else 0.0,
        "average_gross": float(account.gross.mean()),
        "max_gross": float(account.gross.max()),
        "costs": float((account.costs + account.missing_penalties).sum()),
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
    for column in (
        "equity",
        "costs",
        "funding_pnl",
        "price_pnl",
        "missing_penalties",
    ):
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


def ensemble(accounts: list[pd.DataFrame]) -> pd.DataFrame:
    if not accounts:
        raise ValueError("empty account ensemble")
    index = accounts[0].index
    returns = []
    for account in accounts:
        values = account.equity.pct_change()
        values.iloc[0] = account.equity.iloc[0] / INITIAL_EQUITY - 1.0
        returns.append(values.to_numpy(float))
    mean_return = np.nanmean(np.vstack(returns), axis=0)
    result = pd.DataFrame(index=index)
    result["equity"] = INITIAL_EQUITY * np.cumprod(1.0 + mean_return)
    for column in (
        "gross",
        "turnover",
        "costs",
        "funding_pnl",
        "price_pnl",
        "missing_penalties",
        "trade_events",
        "forced_exits",
    ):
        result[column] = np.mean(
            np.vstack([account[column].to_numpy(float) for account in accounts]),
            axis=0,
        )
    return result


def bootstrap_trade_mean(
    trades: pd.DataFrame,
    *,
    samples: int = 5000,
    seed: int = 171,
) -> dict[str, float | int]:
    if trades.empty or "net_return" not in trades:
        return {"trade_count": 0, "mean": 0.0, "ci_05": 0.0, "ci_95": 0.0}
    values = pd.to_numeric(trades.net_return, errors="coerce").dropna().to_numpy(float)
    if not len(values):
        return {"trade_count": 0, "mean": 0.0, "ci_05": 0.0, "ci_95": 0.0}
    rng = np.random.default_rng(seed)
    means = rng.choice(values, size=(samples, len(values)), replace=True).mean(axis=1)
    return {
        "trade_count": int(len(values)),
        "mean": float(values.mean()),
        "ci_05": float(np.quantile(means, 0.05)),
        "ci_95": float(np.quantile(means, 0.95)),
    }


def circular_block_bootstrap_total_return(
    account: pd.DataFrame,
    *,
    block_size: int = 21,
    samples: int = 3000,
    seed: int = 178,
) -> dict[str, float | int]:
    if account.empty or len(account) < 3:
        return {
            "observations": 0,
            "block_size": block_size,
            "samples": samples,
            "probability_positive": 0.0,
            "p05": 0.0,
            "p50": 0.0,
            "p95": 0.0,
        }
    returns = account.equity.pct_change().replace([np.inf, -np.inf], np.nan)
    returns.iloc[0] = account.equity.iloc[0] / INITIAL_EQUITY - 1.0
    values = returns.fillna(0.0).to_numpy(float)
    n = len(values)
    block_size = max(1, min(int(block_size), n))
    rng = np.random.default_rng(seed)
    totals = np.empty(samples, dtype=float)
    blocks_needed = int(np.ceil(n / block_size))
    offsets = np.arange(block_size)
    for sample in range(samples):
        starts = rng.integers(0, n, size=blocks_needed)
        indices = (starts[:, None] + offsets[None, :]) % n
        selected = values[indices.ravel()[:n]]
        totals[sample] = float(np.prod(1.0 + selected) - 1.0)
    return {
        "observations": int(n),
        "block_size": int(block_size),
        "samples": int(samples),
        "probability_positive": float(np.mean(totals > 0.0)),
        "p05": float(np.quantile(totals, 0.05)),
        "p50": float(np.quantile(totals, 0.50)),
        "p95": float(np.quantile(totals, 0.95)),
    }


def policy_dict(policy: Policy) -> dict[str, Any]:
    return asdict(policy) | {"name": policy.name}
