from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from basis_config import (
    ASSETS,
    EXIT_ABS_Z,
    GROSS,
    INITIAL_EQUITY,
    LEG_GROSS,
    Audit,
    Policy,
)


@dataclass(slots=True)
class PreparedMarket:
    index: pd.DatetimeIndex
    arrays: dict[str, dict[str, np.ndarray]]
    zscores: dict[tuple[str, int], np.ndarray]
    entry_masks: dict[tuple[str, int, float, float, int], np.ndarray]


def prepare(markets: dict[str, pd.DataFrame]) -> PreparedMarket:
    nonempty = [frame for frame in markets.values() if not frame.empty]
    if not nonempty:
        raise ValueError("no aligned markets")
    start = min(frame.timestamp.min() for frame in nonempty)
    end = max(frame.timestamp.max() for frame in nonempty)
    index = pd.date_range(start.floor("h"), end.ceil("h"), freq="h", tz="UTC")
    arrays: dict[str, dict[str, np.ndarray]] = {}
    zscores: dict[tuple[str, int], np.ndarray] = {}
    entry_masks: dict[tuple[str, int, float, float, int], np.ndarray] = {}

    lookbacks = (168, 336, 672)
    z_thresholds = (3.0, 4.0, 5.0)
    spread_thresholds = (30.0, 45.0, 60.0)
    stability_values = (1, 2)

    for asset in ASSETS:
        frame = markets.get(asset, pd.DataFrame()).copy()
        if frame.empty:
            aligned = pd.DataFrame(index=index)
        else:
            frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
            aligned = frame.set_index("timestamp").sort_index().reindex(index)

        def column(name: str) -> np.ndarray:
            series = (
                aligned[name]
                if name in aligned.columns
                else pd.Series(np.nan, index=index, dtype=float)
            )
            return pd.to_numeric(series, errors="coerce").to_numpy(float)

        values = {
            "open_binance": column("open_binance"),
            "close_binance": column("close_binance"),
            "open_okx": column("open_okx"),
            "close_okx": column("close_okx"),
        }
        arrays[asset] = values
        close_binance = pd.Series(values["close_binance"], index=index)
        close_okx = pd.Series(values["close_okx"], index=index)
        spread = np.log(close_okx / close_binance)
        spread = spread.replace([np.inf, -np.inf], np.nan)
        spread_bps = spread * 10_000.0
        values["spread"] = spread.to_numpy(float)
        values["spread_bps"] = spread_bps.to_numpy(float)

        for lookback in lookbacks:
            center = spread.rolling(lookback, min_periods=lookback).median()
            residual = (spread - center).abs()
            scale = 1.4826 * residual.rolling(
                lookback, min_periods=lookback
            ).median()
            scale = scale.where(scale > 1e-10)
            z = ((spread - center) / scale).replace([np.inf, -np.inf], np.nan)
            zscores[(asset, lookback)] = z.to_numpy(float)
            sign = np.sign(z)
            for threshold in z_thresholds:
                for spread_threshold in spread_thresholds:
                    base = z.abs().ge(threshold) & spread_bps.abs().ge(
                        spread_threshold
                    )
                    for stability in stability_values:
                        stable = base.copy()
                        for offset in range(1, stability):
                            stable &= base.shift(offset, fill_value=False)
                            stable &= sign.eq(sign.shift(offset))
                        entry_masks[
                            (
                                asset,
                                lookback,
                                threshold,
                                spread_threshold,
                                stability,
                            )
                        ] = stable.fillna(False).to_numpy(bool)
    return PreparedMarket(
        index=index,
        arrays=arrays,
        zscores=zscores,
        entry_masks=entry_masks,
    )


def _finite_prices(values: dict[str, np.ndarray], i: int, field: str) -> bool:
    return bool(
        i >= 0
        and i < len(values[field + "_binance"])
        and np.isfinite(values[field + "_binance"][i])
        and np.isfinite(values[field + "_okx"][i])
        and values[field + "_binance"][i] > 0
        and values[field + "_okx"][i] > 0
    )


def simulate(
    prepared: PreparedMarket,
    policy: Policy,
    audit: Audit,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    index = prepared.index
    n = len(index)
    realized = INITIAL_EQUITY
    marked = INITIAL_EQUITY
    equity = np.full(n, INITIAL_EQUITY, dtype=float)
    gross = np.zeros(n, dtype=float)
    turnover = np.zeros(n, dtype=float)
    costs = np.zeros(n, dtype=float)
    funding_buffer = np.zeros(n, dtype=float)
    forced_costs = np.zeros(n, dtype=float)
    trade_events = np.zeros(n, dtype=int)

    active: dict[str, Any] | None = None
    pending_entry: dict[str, Any] | None = None
    pending_exit: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    trade_rate = (audit.fee_bps + audit.slippage_bps) / 10_000.0

    def mark_position(
        position: dict[str, Any],
        i: int,
        field: str,
    ) -> tuple[float, bool]:
        values = prepared.arrays[position["asset"]]
        if not _finite_prices(values, i, field):
            return 0.0, False
        binance_price = float(values[field + "_binance"][i])
        okx_price = float(values[field + "_okx"][i])
        direction = float(position["direction"])
        pnl = direction * position["q_binance"] * (
            binance_price - position["entry_binance"]
        )
        pnl -= direction * position["q_okx"] * (
            okx_price - position["entry_okx"]
        )
        return float(pnl), True

    def record_close(
        position: dict[str, Any],
        i: int,
        timestamp: pd.Timestamp,
        field: str,
        reason: str,
        *,
        apply_forced_penalty: bool,
    ) -> tuple[float, bool]:
        pnl, available = mark_position(position, i, field)
        if available:
            before_cost = max(0.0, position["capital_after_entry"] + pnl)
        elif reason == "end_of_sample":
            before_cost = max(0.0, marked)
        else:
            return 0.0, False
        exit_cost = before_cost * GROSS * trade_rate
        fund_cost = (
            position["capital_after_entry"]
            * GROSS
            * audit.funding_buffer_bps
            / 10_000.0
        )
        forced = (
            before_cost * GROSS * audit.forced_exit_extra_bps / 10_000.0
            if apply_forced_penalty
            else 0.0
        )
        after = max(0.0, before_cost - exit_cost - fund_cost - forced)
        turnover[i] += GROSS
        costs[i] += exit_cost
        funding_buffer[i] += fund_cost
        forced_costs[i] += forced
        trade_events[i] += 1
        values = prepared.arrays[position["asset"]]
        exit_binance = (
            float(values[field + "_binance"][i])
            if _finite_prices(values, i, field)
            else None
        )
        exit_okx = (
            float(values[field + "_okx"][i])
            if _finite_prices(values, i, field)
            else None
        )
        trades.append(
            {
                "asset": position["asset"],
                "direction": position["direction"],
                "signal_time": position["signal_time"],
                "entry_time": position["entry_time"],
                "exit_time": timestamp,
                "entry_binance": position["entry_binance"],
                "entry_okx": position["entry_okx"],
                "exit_binance": exit_binance,
                "exit_okx": exit_okx,
                "entry_z": position["entry_z"],
                "entry_spread_bps": position["entry_spread_bps"],
                "exit_reason": reason,
                "holding_hours": i - position["entry_index"],
                "pnl_before_exit_cost": pnl if available else None,
                "entry_cost": position["entry_cost"],
                "exit_cost": exit_cost,
                "funding_buffer_cost": fund_cost,
                "forced_cost": forced,
                "equity_after": after,
            }
        )
        return after, True

    for i, timestamp in enumerate(index):
        # Exit before entry at the same open, so risk reductions have priority.
        if active is not None and pending_exit is not None and i >= pending_exit["index"]:
            after, closed = record_close(
                active,
                i,
                timestamp,
                "open",
                pending_exit["reason"],
                apply_forced_penalty=pending_exit["reason"] == "missing_price",
            )
            if closed:
                realized = after
                active = None
                pending_exit = None
                marked = realized
            else:
                pending_exit["index"] = i + 1

        if active is None and pending_entry is not None and i >= pending_entry["index"]:
            values = prepared.arrays[pending_entry["asset"]]
            if _finite_prices(values, i, "open"):
                entry_cost = realized * GROSS * trade_rate
                capital_after_entry = max(0.0, realized - entry_cost)
                notional = capital_after_entry * LEG_GROSS
                entry_binance = float(values["open_binance"][i])
                entry_okx = float(values["open_okx"][i])
                active = {
                    **pending_entry,
                    "entry_index": i,
                    "entry_time": timestamp,
                    "entry_binance": entry_binance,
                    "entry_okx": entry_okx,
                    "q_binance": notional / entry_binance,
                    "q_okx": notional / entry_okx,
                    "capital_after_entry": capital_after_entry,
                    "entry_cost": entry_cost,
                }
                realized = capital_after_entry
                turnover[i] += GROSS
                costs[i] += entry_cost
                trade_events[i] += 1
            pending_entry = None

        if active is not None:
            pnl, available = mark_position(active, i, "close")
            if available:
                marked = max(0.0, active["capital_after_entry"] + pnl)
            else:
                marked = max(0.0, marked)
                if pending_exit is None:
                    pending_exit = {"index": i + 1, "reason": "missing_price"}
            gross[i] = GROSS
        else:
            marked = realized

        equity[i] = marked

        if active is not None and pending_exit is None:
            z = prepared.zscores[(active["asset"], policy.lookback_hours)][i]
            age = i - active["entry_index"] + 1
            if age >= policy.max_hold_hours:
                pending_exit = {
                    "index": i + 1 + audit.execution_delay_hours,
                    "reason": "max_hold",
                }
            elif np.isfinite(z) and abs(z) <= EXIT_ABS_Z:
                pending_exit = {
                    "index": i + 1 + audit.execution_delay_hours,
                    "reason": "convergence",
                }

        if active is None and pending_entry is None and pending_exit is None:
            choices: list[tuple[float, str, float, float]] = []
            for asset in ASSETS:
                mask = prepared.entry_masks[
                    (
                        asset,
                        policy.lookback_hours,
                        policy.entry_abs_z,
                        policy.entry_abs_spread_bps,
                        policy.stability_bars,
                    )
                ]
                if not mask[i]:
                    continue
                z = prepared.zscores[(asset, policy.lookback_hours)][i]
                spread_bps = prepared.arrays[asset]["spread_bps"][i]
                if np.isfinite(z) and np.isfinite(spread_bps):
                    choices.append(
                        (abs(float(z)), asset, float(z), float(spread_bps))
                    )
            if choices:
                _, asset, z, spread_bps = max(choices)
                # Positive z means OKX is expensive: long Binance, short OKX.
                direction = 1 if z > 0 else -1
                pending_entry = {
                    "index": i + 1 + audit.execution_delay_hours,
                    "asset": asset,
                    "direction": direction,
                    "signal_time": timestamp,
                    "entry_z": z,
                    "entry_spread_bps": spread_bps,
                }

    # The last marked position must pay its real exit/funding costs. Without this,
    # the terminal metric can be overstated solely because the sample ended.
    if active is not None and n > 0:
        i = n - 1
        after, closed = record_close(
            active,
            i,
            index[i],
            "close",
            "end_of_sample",
            apply_forced_penalty=not _finite_prices(
                prepared.arrays[active["asset"]], i, "close"
            ),
        )
        if closed:
            equity[i] = after
            gross[i] = 0.0

    account = pd.DataFrame(
        {
            "equity": equity,
            "gross": gross,
            "turnover": turnover,
            "costs": costs,
            "funding_buffer_cost": funding_buffer,
            "forced_costs": forced_costs,
            "trade_events": trade_events,
            "liquidated_notional": 0.0,
            "min_margin_buffer": 1.0 - gross,
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
            "trade_count": 0,
            "liquidations": 0,
            "min_margin_buffer": 1.0,
        }
    years = elapsed_years(account.index)
    final = float(account.equity.iloc[-1])
    total_return = final / INITIAL_EQUITY - 1.0
    cagr = (
        (final / INITIAL_EQUITY) ** (1.0 / years) - 1.0
        if years > 0 and final > 0
        else -1.0
    )
    returns = (
        account.equity.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    )
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
        "annual_turnover": float(account.turnover.sum() / years)
        if years > 0
        else 0.0,
        "average_gross": float(account.gross.mean()),
        "max_gross": float(account.gross.max()),
        "costs": float(
            (
                account.costs
                + account.funding_buffer_cost
                + account.forced_costs
            ).sum()
        ),
        "trade_count": int(account.trade_events.sum() // 2),
        "liquidations": int((account.liquidated_notional > 0).sum()),
        "min_margin_buffer": float(account.min_margin_buffer.min()),
        "observations_per_year": observations_per_year,
    }


def slice_account(account: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    selected = account[
        (account.index >= start_ts) & (account.index <= end_ts)
    ].copy()
    if selected.empty:
        return selected
    before = account[account.index < start_ts]
    base = float(before.equity.iloc[-1]) if not before.empty else INITIAL_EQUITY
    scale = INITIAL_EQUITY / base
    selected["equity"] *= scale
    selected["costs"] *= scale
    selected["funding_buffer_cost"] *= scale
    selected["forced_costs"] *= scale
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
    hourly_returns = []
    for account in accounts:
        first = account.equity.iloc[0] / INITIAL_EQUITY - 1.0
        values = account.equity.pct_change()
        values.iloc[0] = first
        hourly_returns.append(values.to_numpy(float))
    mean_return = np.nanmean(np.vstack(hourly_returns), axis=0)
    equity = INITIAL_EQUITY * np.cumprod(1.0 + mean_return)
    result = pd.DataFrame(index=index)
    result["equity"] = equity
    for column in (
        "gross",
        "turnover",
        "costs",
        "funding_buffer_cost",
        "forced_costs",
        "trade_events",
        "liquidated_notional",
        "min_margin_buffer",
    ):
        values = np.vstack(
            [account[column].to_numpy(float) for account in accounts]
        )
        result[column] = np.mean(values, axis=0)
    return result


def policy_dict(policy: Policy) -> dict[str, Any]:
    return asdict(policy) | {"name": policy.name}
