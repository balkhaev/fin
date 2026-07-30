from __future__ import annotations

from dataclasses import asdict
from typing import Any

import numpy as np
import pandas as pd

from config import INITIAL_EQUITY, TARGET_GROSS, Audit, Policy


def _persistent(
    base: pd.Series,
    direction: pd.Series,
    observations: int,
) -> pd.Series:
    valid = base.fillna(False).astype(bool)
    if observations <= 1:
        return valid
    for offset in range(1, observations):
        valid &= base.shift(offset, fill_value=False)
        valid &= direction.eq(direction.shift(offset))
    return valid.fillna(False)


def policy_signal(panel: pd.DataFrame, policy: Policy) -> pd.DataFrame:
    index = panel.index
    direction = pd.Series(0, index=index, dtype=np.int8)
    score = pd.Series(np.nan, index=index, dtype=float)
    quality = panel.quality.fillna(False).astype(bool)

    if policy.family in {"imbalance_continuation", "false_pressure_control"}:
        z = pd.to_numeric(panel.pressure_z, errors="coerce")
        raw_direction = np.sign(z).fillna(0).astype(np.int8)
        base = quality & z.abs().ge(policy.threshold) & raw_direction.ne(0)
        valid = _persistent(base, raw_direction, policy.persistence)
        direction.loc[valid] = raw_direction.loc[valid]
        if policy.family == "false_pressure_control":
            direction.loc[valid] *= -1
        score.loc[valid] = z.loc[valid].abs()

    elif policy.family == "liquidity_vacuum_continuation":
        z = pd.to_numeric(panel.pressure_z, errors="coerce")
        depth_z = pd.to_numeric(panel.depth_z, errors="coerce")
        raw_direction = np.sign(z).fillna(0).astype(np.int8)
        base = (
            quality
            & z.abs().ge(policy.threshold)
            & depth_z.le(policy.secondary)
            & raw_direction.ne(0)
        )
        valid = _persistent(base, raw_direction, policy.persistence)
        direction.loc[valid] = raw_direction.loc[valid]
        score.loc[valid] = z.loc[valid].abs() + (-depth_z.loc[valid]).clip(lower=0)

    elif policy.family == "replenishment_reversal":
        move = pd.to_numeric(panel.price_move, errors="coerce")
        pressure = pd.to_numeric(panel.pressure_z, errors="coerce")
        bid_replenishment = pd.to_numeric(panel.bid_replenishment, errors="coerce")
        ask_replenishment = pd.to_numeric(panel.ask_replenishment, errors="coerce")
        move_threshold = policy.threshold / 10_000.0
        long_signal = (
            quality
            & move.le(-move_threshold)
            & bid_replenishment.ge(policy.secondary)
            & pressure.ge(policy.confirmation)
        )
        short_signal = (
            quality
            & move.ge(move_threshold)
            & ask_replenishment.ge(policy.secondary)
            & pressure.le(-policy.confirmation)
        )
        direction.loc[long_signal] = 1
        direction.loc[short_signal] = -1
        score.loc[long_signal] = (
            (-move.loc[long_signal]) * 10_000.0
            + bid_replenishment.loc[long_signal].clip(lower=0) * 10.0
            + pressure.loc[long_signal].abs()
        )
        score.loc[short_signal] = (
            move.loc[short_signal] * 10_000.0
            + ask_replenishment.loc[short_signal].clip(lower=0) * 10.0
            + pressure.loc[short_signal].abs()
        )
    else:
        raise ValueError(f"unknown policy family: {policy.family}")

    return pd.DataFrame({"direction": direction, "score": score}, index=index)


def _candidate_events(
    panels: dict[str, pd.DataFrame],
    policy: Policy,
) -> pd.DataFrame:
    events: list[pd.DataFrame] = []
    for asset, panel in panels.items():
        signal = policy_signal(panel, policy)
        valid = signal.direction.ne(0) & signal.score.notna()
        if not valid.any():
            continue
        positions = np.flatnonzero(valid.to_numpy())
        events.append(
            pd.DataFrame(
                {
                    "i": positions,
                    "asset": asset,
                    "direction": signal.direction.to_numpy()[positions].astype(int),
                    "score": signal.score.to_numpy()[positions].astype(float),
                }
            )
        )
    if not events:
        return pd.DataFrame(columns=["i", "asset", "direction", "score"])
    output = pd.concat(events, ignore_index=True)
    return output.sort_values(["i", "score", "asset"], ascending=[True, False, True])


def simulate(
    panels: dict[str, pd.DataFrame],
    policy: Policy,
    audit: Audit,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    nonempty = [panel for panel in panels.values() if not panel.empty]
    if not nonempty:
        raise ValueError("no panels")
    index = nonempty[0].index
    for panel in nonempty[1:]:
        if not panel.index.equals(index):
            raise ValueError("panel indices must be identical")

    events = _candidate_events(panels, policy)
    equity = INITIAL_EQUITY
    next_signal_index = 0
    half_cost_rate = audit.round_trip_cost_bps / 20_000.0
    records: list[dict[str, Any]] = [
        {
            "timestamp": index[0],
            "equity": equity,
            "turnover": 0.0,
            "costs": 0.0,
            "trade_events": 0,
            "unexplained_events": 0,
            "gross": 0.0,
        }
    ]
    trades: list[dict[str, Any]] = []

    grouped = events.groupby("i", sort=True) if not events.empty else []
    for signal_i, choices in grouped:
        signal_i = int(signal_i)
        if signal_i < next_signal_index:
            continue
        choice = choices.iloc[0]
        asset = str(choice.asset)
        direction = int(choice.direction)
        panel = panels[asset]
        entry_i = signal_i + 1 + audit.execution_delay_minutes
        exit_i = entry_i + policy.hold_minutes
        if exit_i >= len(index):
            continue
        entry_price = float(panel.open.iloc[entry_i]) if np.isfinite(panel.open.iloc[entry_i]) else np.nan
        exit_price = float(panel.open.iloc[exit_i]) if np.isfinite(panel.open.iloc[exit_i]) else np.nan
        if not np.isfinite(entry_price) or entry_price <= 0 or not np.isfinite(exit_price) or exit_price <= 0:
            records.append(
                {
                    "timestamp": index[min(entry_i, len(index) - 1)],
                    "equity": equity,
                    "turnover": 0.0,
                    "costs": 0.0,
                    "trade_events": 0,
                    "unexplained_events": 1,
                    "gross": 0.0,
                }
            )
            next_signal_index = min(exit_i + 1, len(index))
            continue

        capital_before = equity
        notional = capital_before * TARGET_GROSS
        entry_cost = notional * half_cost_rate
        capital_after_entry = max(0.0, capital_before - entry_cost)
        records.append(
            {
                "timestamp": index[entry_i],
                "equity": capital_after_entry,
                "turnover": TARGET_GROSS,
                "costs": entry_cost,
                "trade_events": 0,
                "unexplained_events": 0,
                "gross": TARGET_GROSS,
            }
        )

        forced = False
        last_price = entry_price
        last_i = entry_i
        for mark_i in range(entry_i + 1, exit_i + 1):
            price_value = panel.open.iloc[mark_i]
            if not np.isfinite(price_value) or float(price_value) <= 0:
                forced = True
                break
            last_price = float(price_value)
            last_i = mark_i
            marked = capital_after_entry + direction * notional * (
                last_price / entry_price - 1.0
            )
            records.append(
                {
                    "timestamp": index[mark_i],
                    "equity": max(0.0, marked),
                    "turnover": 0.0,
                    "costs": 0.0,
                    "trade_events": 0,
                    "unexplained_events": 0,
                    "gross": TARGET_GROSS,
                }
            )

        price_pnl = direction * notional * (last_price / entry_price - 1.0)
        exit_cost = notional * half_cost_rate
        forced_cost = (
            notional * audit.forced_exit_extra_bps / 10_000.0 if forced else 0.0
        )
        equity = max(0.0, capital_after_entry + price_pnl - exit_cost - forced_cost)
        records.append(
            {
                "timestamp": index[last_i],
                "equity": equity,
                "turnover": TARGET_GROSS,
                "costs": exit_cost + forced_cost,
                "trade_events": 1,
                "unexplained_events": int(forced),
                "gross": 0.0,
            }
        )
        trades.append(
            {
                "policy": policy.name,
                "family": policy.family,
                "asset": asset,
                "signal_time": index[signal_i],
                "entry_time": index[entry_i],
                "exit_time": index[last_i],
                "direction": direction,
                "score": float(choice.score),
                "entry_price": entry_price,
                "exit_price": last_price,
                "holding_minutes": int(last_i - entry_i),
                "capital_before": capital_before,
                "entry_cost": entry_cost,
                "exit_cost": exit_cost,
                "forced_cost": forced_cost,
                "price_pnl": price_pnl,
                "equity_after": equity,
                "net_return": equity / capital_before - 1.0
                if capital_before > 0
                else -1.0,
                "forced": forced,
            }
        )
        next_signal_index = max(exit_i + 1, last_i + 1)
        if equity <= 0:
            break

    records.append(
        {
            "timestamp": index[-1],
            "equity": equity,
            "turnover": 0.0,
            "costs": 0.0,
            "trade_events": 0,
            "unexplained_events": 0,
            "gross": 0.0,
        }
    )
    account = pd.DataFrame(records)
    account["timestamp"] = pd.to_datetime(account.timestamp, utc=True)
    account = account.sort_values("timestamp")
    # When entry, mark and exit share a timestamp, equity after the final event is
    # authoritative while flows remain additive.
    account = account.groupby("timestamp", as_index=True).agg(
        equity=("equity", "last"),
        turnover=("turnover", "sum"),
        costs=("costs", "sum"),
        trade_events=("trade_events", "sum"),
        unexplained_events=("unexplained_events", "sum"),
        gross=("gross", "last"),
    )
    return account, pd.DataFrame(trades)


def elapsed_years(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 0.0
    return max(
        (index[-1] - index[0]).total_seconds() / (365.2425 * 86_400.0),
        1.0 / 365.2425,
    )


def metrics(
    account: pd.DataFrame,
    trades: pd.DataFrame | None = None,
) -> dict[str, float | int]:
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
    daily = account.equity.resample("1D").last().ffill()
    daily_returns = daily.pct_change().dropna()
    std = float(daily_returns.std(ddof=1)) if len(daily_returns) > 1 else 0.0
    sharpe = (
        float(daily_returns.mean() / std * np.sqrt(365.2425)) if std > 0 else 0.0
    )
    drawdown = account.equity / account.equity.cummax() - 1.0
    if trades is not None and not trades.empty:
        holding = float(pd.to_numeric(trades.holding_minutes, errors="coerce").fillna(0).sum())
        total_minutes = max(
            (account.index[-1] - account.index[0]).total_seconds() / 60.0,
            1.0,
        )
        average_gross = TARGET_GROSS * holding / total_minutes
        max_gross = TARGET_GROSS
        trade_count = len(trades)
    else:
        average_gross = 0.0
        max_gross = 0.0
        trade_count = int(account.trade_events.sum())
    return {
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": float(drawdown.min()),
        "sharpe": sharpe,
        "annual_turnover": float(account.turnover.sum() / years) if years > 0 else 0.0,
        "average_gross": average_gross,
        "max_gross": max_gross,
        "costs": float(account.costs.sum()),
        "trade_count": int(trade_count),
        "unexplained_events": int(account.unexplained_events.sum()),
    }


def slice_account(
    account: pd.DataFrame,
    start: str,
    end: str,
) -> pd.DataFrame:
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    before = account[account.index < start_ts]
    base = float(before.equity.iloc[-1]) if not before.empty else INITIAL_EQUITY
    selected = account[(account.index >= start_ts) & (account.index <= end_ts)].copy()
    boundary = pd.DataFrame(
        {
            "equity": [base, float(selected.equity.iloc[-1]) if not selected.empty else base],
            "turnover": [0.0, 0.0],
            "costs": [0.0, 0.0],
            "trade_events": [0, 0],
            "unexplained_events": [0, 0],
            "gross": [0.0, 0.0],
        },
        index=pd.DatetimeIndex([start_ts, end_ts]),
    )
    selected = pd.concat([boundary.iloc[[0]], selected, boundary.iloc[[1]]])
    selected = selected[~selected.index.duplicated(keep="last")].sort_index()
    scale = INITIAL_EQUITY / base if base > 0 else 1.0
    selected["equity"] *= scale
    selected["costs"] *= scale
    return selected


def slice_trades(trades: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    if trades.empty:
        return trades.copy()
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    times = pd.to_datetime(trades.exit_time, utc=True)
    return trades[(times >= start_ts) & (times <= end_ts)].copy()


def period_metrics(
    account: pd.DataFrame,
    trades: pd.DataFrame,
    start: str,
    end: str,
) -> dict[str, float | int]:
    return metrics(slice_account(account, start, end), slice_trades(trades, start, end))


def calendar_returns(account: pd.DataFrame, frequency: str) -> pd.DataFrame:
    equity = account.equity.resample("1D").last().ffill()
    if frequency == "year":
        grouped = equity.groupby(equity.index.year)
        labels = [int(value) for value in grouped.groups]
    elif frequency == "quarter":
        grouped = equity.groupby(equity.index.to_period("Q"))
        labels = [str(value) for value in grouped.groups]
    elif frequency == "month":
        grouped = equity.groupby(equity.index.to_period("M"))
        labels = [str(value) for value in grouped.groups]
    else:
        raise ValueError(frequency)
    rows: list[dict[str, Any]] = []
    previous = INITIAL_EQUITY
    for label, (_, values) in zip(labels, grouped):
        end_value = float(values.iloc[-1])
        rows.append({frequency: label, "return": end_value / previous - 1.0})
        previous = end_value
    return pd.DataFrame(rows)


def concentration_metrics(account: pd.DataFrame) -> dict[str, float]:
    monthly = calendar_returns(account, "month")
    quarterly = calendar_returns(account, "quarter")
    positive = np.log1p(monthly.loc[monthly["return"] > 0, "return"].to_numpy(float))
    top_share = float(positive.max() / positive.sum()) if len(positive) and positive.sum() > 0 else 1.0
    worst_quarter = float(quarterly["return"].min()) if not quarterly.empty else 0.0
    return {
        "top_month_positive_pnl_share": top_share,
        "worst_calendar_quarter": worst_quarter,
    }


def trade_bootstrap(
    trades: pd.DataFrame,
    *,
    samples: int = 5_000,
    seed: int = 204,
) -> dict[str, float | int]:
    if trades.empty:
        return {
            "trade_count": 0,
            "mean": 0.0,
            "ci_05": 0.0,
            "ci_95": 0.0,
            "probability_positive_mean": 0.0,
        }
    values = pd.to_numeric(trades.net_return, errors="coerce").dropna().to_numpy(float)
    if not len(values):
        return {
            "trade_count": 0,
            "mean": 0.0,
            "ci_05": 0.0,
            "ci_95": 0.0,
            "probability_positive_mean": 0.0,
        }
    rng = np.random.default_rng(seed)
    means = rng.choice(values, size=(samples, len(values)), replace=True).mean(axis=1)
    return {
        "trade_count": int(len(values)),
        "mean": float(values.mean()),
        "ci_05": float(np.quantile(means, 0.05)),
        "ci_95": float(np.quantile(means, 0.95)),
        "probability_positive_mean": float(np.mean(means > 0)),
    }


def policy_dict(policy: Policy) -> dict[str, Any]:
    return asdict(policy) | {"name": policy.name}
