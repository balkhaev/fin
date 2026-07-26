from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from config import (
    ASSETS,
    BASIS_WINDOW,
    INITIAL_EQUITY,
    MAX_ABS_BASIS_BPS,
    MIN_FEATURE_PERIODS,
    RETURN_WINDOW,
    FLOW_WINDOW,
    TARGET_GROSS,
    Audit,
    Policy,
)


@dataclass(slots=True)
class PreparedMarket:
    index: pd.DatetimeIndex
    arrays: dict[str, dict[str, np.ndarray]]


def _causal_z(series: pd.Series, window: int) -> pd.Series:
    history = series.shift(1)
    mean = history.rolling(window, min_periods=min(window, MIN_FEATURE_PERIODS)).mean()
    std = history.rolling(window, min_periods=min(window, MIN_FEATURE_PERIODS)).std(ddof=1)
    std = std.where(std > 1e-12)
    return ((series - mean) / std).replace([np.inf, -np.inf], np.nan)


def prepare(markets: dict[str, pd.DataFrame]) -> PreparedMarket:
    nonempty = [frame for frame in markets.values() if not frame.empty]
    if not nonempty:
        raise ValueError("no market data")
    index = pd.DatetimeIndex(nonempty[0].timestamp)
    for frame in nonempty[1:]:
        index = index.union(pd.DatetimeIndex(frame.timestamp))
    index = index.sort_values()
    arrays: dict[str, dict[str, np.ndarray]] = {}
    for asset in ASSETS:
        source = markets[asset].copy()
        source["timestamp"] = pd.to_datetime(source["timestamp"], utc=True)
        frame = source.set_index("timestamp").sort_index().reindex(index)
        close_spot = pd.to_numeric(frame.close_spot, errors="coerce")
        close_perp = pd.to_numeric(frame.close_perp, errors="coerce")
        ret_spot = np.log(close_spot).diff()
        ret_perp = np.log(close_perp).diff()
        flow_spot = pd.to_numeric(frame.flow_spot, errors="coerce")
        flow_perp = pd.to_numeric(frame.flow_perp, errors="coerce")
        basis_bps = np.log(close_perp / close_spot) * 10_000.0
        frame["ret_spot_z"] = _causal_z(ret_spot, RETURN_WINDOW)
        frame["ret_perp_z"] = _causal_z(ret_perp, RETURN_WINDOW)
        frame["flow_spot_z"] = _causal_z(flow_spot, FLOW_WINDOW)
        frame["flow_perp_z"] = _causal_z(flow_perp, FLOW_WINDOW)
        frame["basis_bps"] = basis_bps
        frame["basis_z"] = _causal_z(basis_bps, BASIS_WINDOW)
        values: dict[str, np.ndarray] = {}
        for column in (
            "open_spot",
            "close_spot",
            "open_perp",
            "close_perp",
            "ret_spot_z",
            "ret_perp_z",
            "flow_spot_z",
            "flow_perp_z",
            "basis_bps",
            "basis_z",
        ):
            values[column] = pd.to_numeric(frame[column], errors="coerce").to_numpy(float)
        values["complete"] = (
            frame.get("complete", pd.Series(False, index=index))
            .astype("boolean")
            .fillna(False)
            .to_numpy(bool)
        )
        arrays[asset] = values
    return PreparedMarket(index=index, arrays=arrays)


def subset(prepared: PreparedMarket, end: str) -> PreparedMarket:
    end_ts = pd.Timestamp(end, tz="UTC")
    keep = prepared.index <= end_ts
    return PreparedMarket(
        index=prepared.index[keep],
        arrays={
            asset: {name: values[keep] for name, values in columns.items()}
            for asset, columns in prepared.arrays.items()
        },
    )


def _finite_prices(values: dict[str, np.ndarray], i: int, field: str, pair: bool) -> bool:
    if i < 0 or i >= len(values[f"{field}_perp"]):
        return False
    perp = values[f"{field}_perp"][i]
    if not np.isfinite(perp) or perp <= 0:
        return False
    if pair:
        spot = values[f"{field}_spot"][i]
        return bool(np.isfinite(spot) and spot > 0)
    return True


def signal_arrays(
    values: dict[str, np.ndarray], policy: Policy
) -> tuple[np.ndarray, np.ndarray, np.ndarray, bool]:
    rs = values["ret_spot_z"]
    rp = values["ret_perp_z"]
    fs = values["flow_spot_z"]
    fp = values["flow_perp_z"]
    bz = values["basis_z"]
    basis = values["basis_bps"]
    n = len(rs)
    mask = np.zeros(n, dtype=bool)
    direction = np.zeros(n, dtype=float)
    score = np.full(n, np.nan, dtype=float)
    pair = policy.family == "basis_flow_convergence"

    if policy.family in {"spot_lead_continuation", "reversed_spot_lead_control"}:
        sign = np.sign(rs)
        base = (
            np.isfinite(rs)
            & np.isfinite(rp)
            & np.isfinite(fs)
            & np.isfinite(fp)
            & (sign != 0)
            & (np.sign(fs) == sign)
            & (np.abs(rs) >= policy.shock_z)
            & (np.abs(fs) >= policy.shock_z)
            & (sign * (rs - rp) >= policy.gap_z)
            & (sign * (fs - fp) >= policy.gap_z)
        )
        direction = sign if policy.family == "spot_lead_continuation" else -sign
        score = np.minimum.reduce(
            [np.abs(rs), np.abs(fs), sign * (rs - rp), sign * (fs - fp)]
        )
        mask = base
    elif policy.family == "perp_unconfirmed_reversal":
        sign = np.sign(rp)
        mask = (
            np.isfinite(rs)
            & np.isfinite(rp)
            & np.isfinite(fs)
            & np.isfinite(fp)
            & (sign != 0)
            & (np.sign(fp) == sign)
            & (np.abs(rp) >= policy.shock_z)
            & (np.abs(fp) >= policy.shock_z)
            & (sign * (rp - rs) >= policy.gap_z)
            & (sign * (fp - fs) >= policy.gap_z)
        )
        direction = -sign
        score = np.minimum.reduce(
            [np.abs(rp), np.abs(fp), sign * (rp - rs), sign * (fp - fs)]
        )
    elif policy.family == "basis_flow_convergence":
        sign = np.sign(bz)
        flow_gap = fp - fs
        mask = (
            np.isfinite(bz)
            & np.isfinite(flow_gap)
            & np.isfinite(basis)
            & (sign != 0)
            & (np.abs(bz) >= policy.shock_z)
            & (np.abs(basis) <= MAX_ABS_BASIS_BPS)
            & (sign * flow_gap >= policy.gap_z)
        )
        # Positive basis z means perpetual is expensive: short perp / long spot.
        direction = -sign
        score = np.minimum(np.abs(bz), sign * flow_gap)
    else:
        raise ValueError(policy.family)

    mask &= values["complete"]
    direction = np.where(mask, direction, 0.0)
    score = np.where(mask, score, np.nan)
    if policy.persistence > 1:
        original = mask.copy()
        stable = original.copy()
        for offset in range(1, policy.persistence):
            shifted = np.zeros(n, dtype=bool)
            shifted[offset:] = original[:-offset]
            same = np.zeros(n, dtype=bool)
            same[offset:] = direction[offset:] == direction[:-offset]
            stable &= shifted & same
        mask = stable
        direction = np.where(mask, direction, 0.0)
        score = np.where(mask, score, np.nan)
    return mask, direction, score, pair


def simulate(
    prepared: PreparedMarket,
    policy: Policy,
    audit: Audit,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    index = prepared.index
    n = len(index)
    signals = {
        asset: signal_arrays(prepared.arrays[asset], policy) for asset in ASSETS
    }
    equity = np.full(n, INITIAL_EQUITY, dtype=float)
    gross = np.zeros(n, dtype=float)
    turnover = np.zeros(n, dtype=float)
    costs = np.zeros(n, dtype=float)
    trade_events = np.zeros(n, dtype=float)
    forced_exits = np.zeros(n, dtype=float)

    realized = INITIAL_EQUITY
    marked = INITIAL_EQUITY
    active: dict[str, Any] | None = None
    pending: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []

    def mark_position(position: dict[str, Any], i: int, field: str) -> tuple[float, float, float] | None:
        values = prepared.arrays[position["asset"]]
        pair = bool(position["pair"])
        if not _finite_prices(values, i, field, pair):
            return None
        perp = float(values[f"{field}_perp"][i])
        pnl = float(position["direction"] * position["q_perp"] * (perp - position["entry_perp"]))
        gross_notional = abs(position["q_perp"] * perp)
        if pair:
            spot = float(values[f"{field}_spot"][i])
            pnl += float(-position["direction"] * position["q_spot"] * (spot - position["entry_spot"]))
            gross_notional += abs(position["q_spot"] * spot)
        value = position["capital_after_entry"] + pnl
        return value, pnl, gross_notional

    def close_position(position: dict[str, Any], i: int, reason: str, forced: bool) -> float | None:
        nonlocal marked
        result = mark_position(position, i, "open")
        if result is None:
            return None
        value, pnl, gross_notional = result
        rate_bps = audit.pair_round_trip_bps if position["pair"] else audit.single_round_trip_bps
        exit_cost = gross_notional * rate_bps / 20_000.0
        forced_cost = gross_notional * audit.forced_exit_extra_bps / 10_000.0 if forced else 0.0
        after = max(0.0, value - exit_cost - forced_cost)
        costs[i] += exit_cost + forced_cost
        turnover[i] += gross_notional / max(value, 1e-12)
        trade_events[i] += 1.0
        if forced:
            forced_exits[i] += 1.0
        capital_before = float(position["capital_before"])
        trades.append(
            {
                "asset": position["asset"],
                "family": policy.family,
                "policy": policy.name,
                "pair": bool(position["pair"]),
                "direction_perp": position["direction"],
                "signal_time": position["signal_time"],
                "entry_time": position["entry_time"],
                "exit_time": index[i],
                "entry_perp": position["entry_perp"],
                "entry_spot": position.get("entry_spot"),
                "exit_reason": reason,
                "holding_bars": i - position["entry_index"],
                "holding_minutes": 5 * (i - position["entry_index"]),
                "signal_score": position["signal_score"],
                "entry_cost": position["entry_cost"],
                "exit_cost": exit_cost,
                "forced_cost": forced_cost,
                "pnl_before_exit_cost": pnl,
                "capital_before": capital_before,
                "equity_after": after,
                "net_pnl": after - capital_before,
                "net_return": after / capital_before - 1.0 if capital_before > 0 else -1.0,
            }
        )
        marked = after
        return after

    def enter(signal: dict[str, Any], i: int) -> dict[str, Any] | None:
        nonlocal realized, marked
        values = prepared.arrays[signal["asset"]]
        pair = bool(signal["pair"])
        if not _finite_prices(values, i, "open", pair):
            return None
        perp = float(values["open_perp"][i])
        capital_before = realized
        rate_bps = audit.pair_round_trip_bps if pair else audit.single_round_trip_bps
        if pair:
            spot = float(values["open_spot"][i])
            q = capital_before * TARGET_GROSS / (perp + spot)
            q_perp = q
            q_spot = q
            gross_notional = q * (perp + spot)
        else:
            spot = None
            q_perp = capital_before * TARGET_GROSS / perp
            q_spot = 0.0
            gross_notional = abs(q_perp * perp)
        entry_cost = gross_notional * rate_bps / 20_000.0
        capital_after = max(0.0, capital_before - entry_cost)
        costs[i] += entry_cost
        turnover[i] += gross_notional / max(capital_before, 1e-12)
        trade_events[i] += 1.0
        realized = capital_after
        marked = capital_after
        return {
            **signal,
            "entry_index": i,
            "entry_time": index[i],
            "exit_index": i + policy.hold_bars,
            "capital_before": capital_before,
            "capital_after_entry": capital_after,
            "entry_cost": entry_cost,
            "entry_perp": perp,
            "entry_spot": spot,
            "q_perp": q_perp,
            "q_spot": q_spot,
        }

    for i, timestamp in enumerate(index):
        if active is not None and i >= active["exit_index"]:
            after = close_position(active, i, "fixed_horizon", forced=False)
            if after is not None:
                realized = after
                active = None
                marked = realized

        if active is None and pending is not None and i >= pending["target_index"]:
            active = enter(pending, i)
            pending = None

        if active is not None:
            result = mark_position(active, i, "close")
            if result is None:
                active["exit_index"] = min(active["exit_index"], i + 1)
                active["force_exit"] = True
            else:
                marked, _, gross_notional = result
                gross[i] = gross_notional / max(marked, 1e-12)
        else:
            marked = realized

        if active is not None and active.get("force_exit") and i >= active["exit_index"]:
            after = close_position(active, i, "missing_price", forced=True)
            if after is not None:
                realized = after
                active = None
                marked = realized
                gross[i] = 0.0

        equity[i] = max(0.0, marked)

        if active is None and pending is None:
            choices: list[tuple[float, str, float, bool]] = []
            for asset in ASSETS:
                mask, direction, score, pair = signals[asset]
                if i >= len(mask) or not mask[i]:
                    continue
                if np.isfinite(score[i]) and direction[i] != 0:
                    choices.append((float(score[i]), asset, float(direction[i]), pair))
            if choices:
                score_value, asset, direction_value, pair_value = max(
                    choices, key=lambda item: (item[0], item[1])
                )
                pending = {
                    "target_index": i + 1 + audit.delay_bars,
                    "asset": asset,
                    "direction": direction_value,
                    "pair": pair_value,
                    "signal_time": timestamp,
                    "signal_score": score_value,
                }

    if active is not None and n:
        i = n - 1
        result = mark_position(active, i, "close")
        if result is not None:
            value, pnl, gross_notional = result
            rate_bps = audit.pair_round_trip_bps if active["pair"] else audit.single_round_trip_bps
            exit_cost = gross_notional * rate_bps / 20_000.0
            after = max(0.0, value - exit_cost)
            costs[i] += exit_cost
            trades.append(
                {
                    "asset": active["asset"],
                    "family": policy.family,
                    "policy": policy.name,
                    "pair": bool(active["pair"]),
                    "direction_perp": active["direction"],
                    "signal_time": active["signal_time"],
                    "entry_time": active["entry_time"],
                    "exit_time": index[i],
                    "entry_perp": active["entry_perp"],
                    "entry_spot": active.get("entry_spot"),
                    "exit_reason": "end_of_sample",
                    "holding_bars": i - active["entry_index"],
                    "holding_minutes": 5 * (i - active["entry_index"]),
                    "signal_score": active["signal_score"],
                    "entry_cost": active["entry_cost"],
                    "exit_cost": exit_cost,
                    "forced_cost": 0.0,
                    "pnl_before_exit_cost": pnl,
                    "capital_before": active["capital_before"],
                    "equity_after": after,
                    "net_pnl": after - active["capital_before"],
                    "net_return": after / active["capital_before"] - 1.0,
                }
            )
            equity[i] = after
            gross[i] = 0.0
            trade_events[i] += 1.0

    account = pd.DataFrame(
        {
            "equity": equity,
            "gross": gross,
            "turnover": turnover,
            "costs": costs,
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
        1.0 / 365.2425,
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
            "trade_count": 0,
            "forced_exits": 0,
            "unexplained_events": 0,
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
    obs_per_year = len(returns) / years if years > 0 else 0.0
    std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    sharpe = float(returns.mean() / std * np.sqrt(obs_per_year)) if std > 0 else 0.0
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
        "trade_count": int(account.trade_events.sum() // 2),
        "forced_exits": int(account.forced_exits.sum()),
        "unexplained_events": int(account.equity.isna().sum()),
        "observations_per_year": obs_per_year,
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
    selected["equity"] *= scale
    selected["costs"] *= scale
    return selected


def yearly_returns(account: pd.DataFrame, name: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    previous = INITIAL_EQUITY
    for year, frame in account.groupby(account.index.year):
        end_value = float(frame.equity.iloc[-1])
        rows.append({"year": int(year), name: end_value / previous - 1.0})
        previous = end_value
    return pd.DataFrame(rows)


def quarterly_returns(account: pd.DataFrame) -> pd.Series:
    if account.empty:
        return pd.Series(dtype=float)
    ends = account.equity.resample("QE").last().dropna()
    starts = ends.shift(1)
    starts.iloc[0] = INITIAL_EQUITY
    return ends / starts - 1.0


def monthly_pnl_share(account: pd.DataFrame) -> float:
    if account.empty:
        return 0.0
    month_ends = account.equity.resample("ME").last().dropna()
    month_starts = month_ends.shift(1)
    month_starts.iloc[0] = INITIAL_EQUITY
    pnl = month_ends - month_starts
    positive = pnl[pnl > 0]
    return float(positive.max() / positive.sum()) if positive.sum() > 0 else 0.0


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
    for column in ("gross", "turnover", "costs", "trade_events", "forced_exits"):
        result[column] = np.mean(
            np.vstack([account[column].to_numpy(float) for account in accounts]), axis=0
        )
    return result


def policy_dict(policy: Policy) -> dict[str, Any]:
    return asdict(policy) | {"name": policy.name}
