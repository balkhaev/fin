from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from config import (
    BAR_MINUTES,
    INITIAL_EQUITY,
    POLICIES,
    ROLLING_MIN_PERIODS,
    ROLLING_WINDOW_BARS,
    SHOCK_WINDOW_BARS,
    TARGET_GROSS,
    Audit,
    Policy,
)


@dataclass(slots=True)
class PreparedMarket:
    index: pd.DatetimeIndex
    frame: pd.DataFrame
    masks: dict[str, np.ndarray]
    directions: dict[str, np.ndarray]


def _robust_z(series: pd.Series) -> pd.Series:
    history = series.shift(1)
    center = history.rolling(
        ROLLING_WINDOW_BARS, min_periods=ROLLING_MIN_PERIODS
    ).median()
    deviation = (history - center).abs()
    scale = 1.4826 * deviation.rolling(
        ROLLING_WINDOW_BARS, min_periods=ROLLING_MIN_PERIODS
    ).median()
    scale = scale.where(scale > 1e-10)
    return ((series - center) / scale).replace([np.inf, -np.inf], np.nan)


def _persistent(base: pd.Series, direction: pd.Series, bars: int) -> pd.Series:
    result = base.fillna(False).astype(bool).copy()
    for offset in range(1, bars):
        result &= base.shift(offset, fill_value=False).fillna(False).astype(bool)
        result &= direction.eq(direction.shift(offset))
    return result.fillna(False)


def prepare(panel: pd.DataFrame) -> PreparedMarket:
    if panel.empty:
        raise ValueError("empty panel")
    frame = panel.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame = frame.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
    frame = frame.set_index("timestamp")
    index = pd.DatetimeIndex(frame.index)

    btc_close = pd.to_numeric(frame["close_btc"], errors="coerce")
    eth_close = pd.to_numeric(frame["close_eth"], errors="coerce")
    btc_ret = np.log(btc_close / btc_close.shift(1)).replace([np.inf, -np.inf], np.nan)
    eth_ret = np.log(eth_close / eth_close.shift(1)).replace([np.inf, -np.inf], np.nan)
    btc_shock = np.log(btc_close / btc_close.shift(SHOCK_WINDOW_BARS)).replace(
        [np.inf, -np.inf], np.nan
    )
    eth_shock = np.log(eth_close / eth_close.shift(SHOCK_WINDOW_BARS)).replace(
        [np.inf, -np.inf], np.nan
    )
    btc_z = _robust_z(btc_shock)
    eth_z = _robust_z(eth_shock)

    x = btc_ret.shift(1)
    y = eth_ret.shift(1)
    covariance = y.rolling(
        ROLLING_WINDOW_BARS, min_periods=ROLLING_MIN_PERIODS
    ).cov(x)
    variance = x.rolling(
        ROLLING_WINDOW_BARS, min_periods=ROLLING_MIN_PERIODS
    ).var()
    beta = (covariance / variance).replace([np.inf, -np.inf], np.nan)
    beta = beta.clip(lower=0.25, upper=2.5)

    eth_residual = eth_shock - beta * btc_shock
    eth_residual_z = _robust_z(eth_residual)
    inverse_beta = (1.0 / beta).clip(lower=0.4, upper=4.0)
    btc_residual = btc_shock - inverse_beta * eth_shock
    btc_residual_z = _robust_z(btc_residual)

    frame["btc_ret"] = btc_ret
    frame["eth_ret"] = eth_ret
    frame["btc_shock"] = btc_shock
    frame["eth_shock"] = eth_shock
    frame["btc_shock_z"] = btc_z
    frame["eth_shock_z"] = eth_z
    frame["beta"] = beta
    frame["eth_residual_z"] = eth_residual_z
    frame["btc_residual_z"] = btc_residual_z

    complete = frame.get("complete", pd.Series(False, index=index)).fillna(False).astype(bool)
    btc_direction = np.sign(btc_z).replace(0.0, np.nan)
    eth_direction = np.sign(eth_z).replace(0.0, np.nan)
    btc_flow = pd.to_numeric(frame.get("flow_btc"), errors="coerce")
    eth_flow = pd.to_numeric(frame.get("flow_eth"), errors="coerce")
    btc_flow_confirm = np.sign(btc_flow).eq(btc_direction)
    eth_flow_confirm = np.sign(eth_flow).eq(eth_direction)
    underreaction = -btc_direction * eth_residual_z
    overshoot = btc_direction * eth_residual_z
    btc_underreaction_to_eth = -eth_direction * btc_residual_z

    masks: dict[str, np.ndarray] = {}
    directions: dict[str, np.ndarray] = {}
    for policy in POLICIES:
        if policy.family == "btc_leads_eth_continuation":
            base = (
                complete
                & btc_z.abs().ge(policy.shock_abs_z)
                & underreaction.ge(policy.gap_abs_z)
                & btc_flow_confirm
            )
            direction = btc_direction
        elif policy.family == "btc_leads_beta_hedged_catchup":
            base = (
                complete
                & btc_z.abs().ge(policy.shock_abs_z)
                & underreaction.ge(policy.gap_abs_z)
                & btc_flow_confirm
            )
            direction = btc_direction
        elif policy.family == "eth_overshoot_beta_hedged_reversal":
            base = (
                complete
                & btc_z.abs().ge(policy.shock_abs_z)
                & overshoot.ge(policy.gap_abs_z)
                & btc_flow_confirm
                & eth_flow_confirm
            )
            direction = -btc_direction
        elif policy.family == "eth_leads_btc_negative_control":
            base = (
                complete
                & eth_z.abs().ge(policy.shock_abs_z)
                & btc_underreaction_to_eth.ge(policy.gap_abs_z)
                & eth_flow_confirm
            )
            direction = eth_direction
        else:  # pragma: no cover
            raise ValueError(policy.family)
        stable = _persistent(base, direction, policy.persistence_bars)
        masks[policy.name] = stable.to_numpy(bool)
        directions[policy.name] = direction.to_numpy(float)
    return PreparedMarket(index=index, frame=frame, masks=masks, directions=directions)


def _finite_prices(frame: pd.DataFrame, i: int, field: str) -> tuple[float, float] | None:
    if i < 0 or i >= len(frame):
        return None
    btc = frame[f"{field}_btc"].iloc[i]
    eth = frame[f"{field}_eth"].iloc[i]
    try:
        btc_value = float(btc)
        eth_value = float(eth)
    except (TypeError, ValueError):
        return None
    if not (np.isfinite(btc_value) and np.isfinite(eth_value)):
        return None
    if btc_value <= 0 or eth_value <= 0:
        return None
    return btc_value, eth_value


def simulate(
    prepared: PreparedMarket,
    policy: Policy,
    audit: Audit,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    index = prepared.index
    frame = prepared.frame
    n = len(index)
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

    pair_family = policy.family in {
        "btc_leads_beta_hedged_catchup",
        "eth_overshoot_beta_hedged_reversal",
    }
    single_asset = "BTC" if policy.family == "eth_leads_btc_negative_control" else "ETH"
    round_trip_bps = (
        audit.pair_round_trip_bps if pair_family else audit.single_round_trip_bps
    )
    half_cost_rate = round_trip_bps / 2.0 / 10_000.0

    def mark(position: dict[str, Any], i: int, field: str) -> tuple[float, float, float] | None:
        prices = _finite_prices(frame, i, field)
        if prices is None:
            return None
        btc_price, eth_price = prices
        direction = float(position["direction"])
        if position["pair"]:
            pnl = direction * position["q_eth"] * (eth_price - position["entry_eth"])
            pnl -= direction * position["q_btc"] * (btc_price - position["entry_btc"])
            current_gross = abs(position["q_eth"] * eth_price) + abs(
                position["q_btc"] * btc_price
            )
        else:
            if position["asset"] == "ETH":
                pnl = direction * position["q"] * (eth_price - position["entry_eth"])
                current_gross = abs(position["q"] * eth_price)
            else:
                pnl = direction * position["q"] * (btc_price - position["entry_btc"])
                current_gross = abs(position["q"] * btc_price)
        value = position["capital_after_entry"] + pnl
        return float(value), float(pnl), float(current_gross)

    def close(position: dict[str, Any], i: int, reason: str, forced: bool) -> float | None:
        nonlocal marked
        result = mark(position, i, "open")
        if result is None:
            return None
        value, pnl, current_gross = result
        exit_cost = max(0.0, current_gross) * half_cost_rate
        forced_cost = (
            max(0.0, current_gross) * audit.forced_exit_extra_bps / 10_000.0
            if forced
            else 0.0
        )
        after = max(0.0, value - exit_cost - forced_cost)
        costs[i] += exit_cost + forced_cost
        turnover[i] += current_gross / max(value, 1e-12)
        trade_events[i] += 1.0
        if forced:
            forced_exits[i] += 1.0
        prices = _finite_prices(frame, i, "open")
        btc_price, eth_price = prices if prices is not None else (np.nan, np.nan)
        capital_before = float(position["capital_before_entry"])
        trades.append(
            {
                "family": policy.family,
                "policy": policy.name,
                "pair": position["pair"],
                "asset": position["asset"],
                "direction": position["direction"],
                "signal_time": position["signal_time"],
                "entry_time": position["entry_time"],
                "exit_time": index[i],
                "entry_btc": position["entry_btc"],
                "entry_eth": position["entry_eth"],
                "exit_btc": btc_price,
                "exit_eth": eth_price,
                "beta": position["beta"],
                "holding_bars": i - position["entry_index"],
                "holding_minutes": (i - position["entry_index"]) * BAR_MINUTES,
                "exit_reason": reason,
                "price_pnl": pnl,
                "entry_cost": position["entry_cost"],
                "exit_and_forced_cost": exit_cost + forced_cost,
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
        prices = _finite_prices(frame, i, "open")
        if prices is None:
            return None
        btc_price, eth_price = prices
        capital_before = realized
        gross_notional = capital_before * TARGET_GROSS
        entry_cost = gross_notional * half_cost_rate
        capital_after = max(0.0, capital_before - entry_cost)
        beta = float(signal["beta"])
        if signal["pair"]:
            eth_notional = gross_notional / (1.0 + beta)
            btc_notional = gross_notional - eth_notional
            q_eth = eth_notional / eth_price
            q_btc = btc_notional / btc_price
            q = 0.0
            asset = "PAIR"
        else:
            asset = signal["asset"]
            q = gross_notional / (eth_price if asset == "ETH" else btc_price)
            q_eth = 0.0
            q_btc = 0.0
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
            "capital_before_entry": capital_before,
            "capital_after_entry": capital_after,
            "entry_cost": entry_cost,
            "entry_btc": btc_price,
            "entry_eth": eth_price,
            "q": q,
            "q_eth": q_eth,
            "q_btc": q_btc,
            "asset": asset,
            "exit_reason": "fixed_horizon",
            "forced": False,
        }

    mask = prepared.masks[policy.name]
    direction_values = prepared.directions[policy.name]

    for i, timestamp in enumerate(index):
        if active is not None and i >= active["exit_index"]:
            after = close(
                active,
                i,
                str(active.get("exit_reason", "fixed_horizon")),
                bool(active.get("forced", False)),
            )
            if after is not None:
                realized = after
                active = None
                marked = realized
            else:
                active["exit_index"] = i + 1
                active["exit_reason"] = "missing_price"
                active["forced"] = True

        if active is None and pending is not None and i >= pending["target_index"]:
            active = enter(pending, i)
            pending = None

        if active is not None:
            result = mark(active, i, "close")
            if result is None:
                active["exit_index"] = min(int(active["exit_index"]), i + 1)
                active["exit_reason"] = "missing_price"
                active["forced"] = True
                marked = max(0.0, marked)
            else:
                marked, _, current_gross = result
                gross[i] = current_gross / max(marked, 1e-12)
        else:
            marked = realized

        equity[i] = max(0.0, marked)

        if active is None and pending is None and mask[i]:
            direction = direction_values[i]
            beta = frame["beta"].iloc[i]
            if np.isfinite(direction) and np.isfinite(beta):
                pending = {
                    "target_index": i + 1 + audit.execution_delay_bars,
                    "signal_time": timestamp,
                    "direction": int(np.sign(direction)),
                    "beta": float(beta),
                    "pair": pair_family,
                    "asset": single_asset,
                }

    if active is not None and n:
        i = n - 1
        result = mark(active, i, "close")
        if result is not None:
            value, pnl, current_gross = result
            exit_cost = current_gross * half_cost_rate
            forced_cost = current_gross * audit.forced_exit_extra_bps / 10_000.0
            after = max(0.0, value - exit_cost - forced_cost)
            costs[i] += exit_cost + forced_cost
            forced_exits[i] += 1.0
            trade_events[i] += 1.0
            turnover[i] += current_gross / max(value, 1e-12)
            capital_before = float(active["capital_before_entry"])
            trades.append(
                {
                    "family": policy.family,
                    "policy": policy.name,
                    "pair": active["pair"],
                    "asset": active["asset"],
                    "direction": active["direction"],
                    "signal_time": active["signal_time"],
                    "entry_time": active["entry_time"],
                    "exit_time": index[i],
                    "entry_btc": active["entry_btc"],
                    "entry_eth": active["entry_eth"],
                    "exit_btc": float(frame["close_btc"].iloc[i]),
                    "exit_eth": float(frame["close_eth"].iloc[i]),
                    "beta": active["beta"],
                    "holding_bars": i - active["entry_index"],
                    "holding_minutes": (i - active["entry_index"]) * BAR_MINUTES,
                    "exit_reason": "end_of_sample",
                    "price_pnl": pnl,
                    "entry_cost": active["entry_cost"],
                    "exit_and_forced_cost": exit_cost + forced_cost,
                    "capital_before": capital_before,
                    "equity_after": after,
                    "net_pnl": after - capital_before,
                    "net_return": after / capital_before - 1.0 if capital_before > 0 else -1.0,
                }
            )
            equity[i] = after
            gross[i] = 0.0

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
        "costs": float(account.costs.sum()),
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
    selected["equity"] *= scale
    selected["costs"] *= scale
    return selected


def subset(panel: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    timestamps = pd.to_datetime(panel["timestamp"], utc=True)
    return panel[(timestamps >= start_ts) & (timestamps <= end_ts)].copy()


def yearly_returns(account: pd.DataFrame, name: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    previous = INITIAL_EQUITY
    for year, part in account.groupby(account.index.year):
        final = float(part.equity.iloc[-1])
        rows.append({"year": int(year), name: final / previous - 1.0})
        previous = final
    return pd.DataFrame(rows)


def quarterly_returns(account: pd.DataFrame) -> pd.DataFrame:
    if account.empty:
        return pd.DataFrame(columns=["quarter", "return"])
    rows = []
    previous = INITIAL_EQUITY
    for quarter, part in account.groupby(account.index.to_period("Q")):
        final = float(part.equity.iloc[-1])
        rows.append({"quarter": str(quarter), "return": final / previous - 1.0})
        previous = final
    return pd.DataFrame(rows)


def monthly_pnl_share(trades: pd.DataFrame) -> float:
    if trades.empty or "net_pnl" not in trades:
        return 1.0
    frame = trades.copy()
    frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True)
    monthly = frame.groupby(frame.exit_time.dt.to_period("M")).net_pnl.sum()
    positive = monthly[monthly > 0]
    if positive.empty or float(positive.sum()) <= 0:
        return 1.0
    return float(positive.max() / positive.sum())


def side_pnl(trades: pd.DataFrame) -> dict[str, float]:
    if trades.empty:
        return {"long": 0.0, "short": 0.0}
    pnl = pd.to_numeric(trades["net_pnl"], errors="coerce").fillna(0.0)
    direction = pd.to_numeric(trades["direction"], errors="coerce")
    return {
        "long": float(pnl[direction > 0].sum()),
        "short": float(pnl[direction < 0].sum()),
    }


def ensemble(accounts: list[pd.DataFrame]) -> pd.DataFrame:
    if not accounts:
        raise ValueError("empty ensemble")
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
