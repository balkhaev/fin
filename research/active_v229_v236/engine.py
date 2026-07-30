from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from config import (
    ASSETS,
    CLOSE_BEFORE_EXPIRY_HOURS,
    DTE_MAX_DAYS,
    DTE_MIN_DAYS,
    EXIT_ABS_Z,
    FUNDING_MEDIAN_EVENTS,
    INITIAL_EQUITY,
    TARGET_GROSS,
    Audit,
    Policy,
)
from data import AssetData


@dataclass(slots=True)
class PreparedAsset:
    perp_open: np.ndarray
    perp_close: np.ndarray
    funding_paid: np.ndarray
    funding_forecast: np.ndarray
    contract_symbols: list[str]
    contract_expiry: np.ndarray
    contract_open: np.ndarray
    contract_close: np.ndarray
    front_index: np.ndarray
    next_index: np.ndarray
    front_basis_bps: np.ndarray
    curve_bps: np.ndarray
    zscores: dict[tuple[str, int], np.ndarray]
    centers: dict[tuple[str, int], np.ndarray]


@dataclass(slots=True)
class PreparedMarket:
    index: pd.DatetimeIndex
    assets: dict[str, PreparedAsset]


def _causal_mean_z(series: pd.Series, window: int) -> tuple[np.ndarray, np.ndarray]:
    history = series.shift(1)
    center = history.rolling(window, min_periods=window).median()
    residual = (history - center).abs()
    scale = 1.4826 * residual.rolling(window, min_periods=window).median()
    scale = scale.where(scale > 1e-10)
    z = ((series - center) / scale).replace([np.inf, -np.inf], np.nan)
    return center.to_numpy(float), z.to_numpy(float)


def _align_price(frame: pd.DataFrame, index: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
    if frame.empty:
        return np.full(len(index), np.nan), np.full(len(index), np.nan)
    values = frame.copy()
    values["timestamp"] = pd.to_datetime(values.timestamp, utc=True)
    aligned = values.set_index("timestamp").sort_index().reindex(index)
    return (
        pd.to_numeric(aligned.open, errors="coerce").to_numpy(float),
        pd.to_numeric(aligned.close, errors="coerce").to_numpy(float),
    )


def _funding_arrays(frame: pd.DataFrame, index: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
    paid = pd.Series(0.0, index=index, dtype=float)
    if not frame.empty:
        values = frame.copy()
        values["timestamp"] = pd.to_datetime(values.timestamp, utc=True)
        scheduled = values.timestamp.dt.round("h")
        jitter = (values.timestamp - scheduled).abs()
        values = values[jitter <= pd.Timedelta(minutes=30)].copy()
        values["scheduled"] = scheduled[jitter <= pd.Timedelta(minutes=30)]
        grouped = values.groupby("scheduled").rate.sum()
        common = grouped.index.intersection(index)
        paid.loc[common] = grouped.loc[common].to_numpy(float)
        events = grouped.sort_index()
        last = events.copy()
        median = events.rolling(FUNDING_MEDIAN_EVENTS, min_periods=3).median()
        conservative = pd.Series(0.0, index=events.index, dtype=float)
        same = np.sign(last) == np.sign(median)
        conservative.loc[same] = (
            np.sign(median.loc[same])
            * np.minimum(last.loc[same].abs(), median.loc[same].abs())
        )
        forecast = conservative.reindex(index).ffill().fillna(0.0)
    else:
        forecast = pd.Series(0.0, index=index, dtype=float)
    return paid.to_numpy(float), forecast.to_numpy(float)


def prepare(markets: dict[str, AssetData]) -> PreparedMarket:
    index = pd.date_range(
        pd.Timestamp("2021-01-01", tz="UTC"),
        pd.Timestamp("2026-06-30 23:00:00", tz="UTC"),
        freq="h",
    )
    assets: dict[str, PreparedAsset] = {}
    for asset in ASSETS:
        source = markets[asset]
        perp_open, perp_close = _align_price(source.perp, index)
        funding_paid, funding_forecast = _funding_arrays(source.funding, index)
        symbols = [contract.symbol for contract in source.contracts]
        expiry = np.array(
            [contract.expiry.value for contract in source.contracts], dtype=np.int64
        )
        opens = []
        closes = []
        for contract in source.contracts:
            op, cl = _align_price(contract.frame, index)
            opens.append(op)
            closes.append(cl)
        contract_open = np.vstack(opens) if opens else np.empty((0, len(index)))
        contract_close = np.vstack(closes) if closes else np.empty((0, len(index)))

        front_index = np.full(len(index), -1, dtype=int)
        next_index = np.full(len(index), -1, dtype=int)
        timestamp_ns = index.view("i8")
        min_ns = int(DTE_MIN_DAYS * 86400 * 1e9)
        max_ns = int(DTE_MAX_DAYS * 86400 * 1e9)
        buffer_ns = int(CLOSE_BEFORE_EXPIRY_HOURS * 3600 * 1e9)
        for i, ts in enumerate(timestamp_ns):
            dte = expiry - ts
            available = np.isfinite(contract_close[:, i]) if len(symbols) else np.array([], dtype=bool)
            candidates = np.where(available & (dte >= min_ns) & (dte <= max_ns))[0]
            if candidates.size:
                first = int(candidates[np.argmin(dte[candidates])])
                front_index[i] = first
                later = np.where(available & (expiry > expiry[first]) & (dte > buffer_ns))[0]
                if later.size:
                    next_index[i] = int(later[np.argmin(expiry[later])])

        front_basis = np.full(len(index), np.nan)
        curve = np.full(len(index), np.nan)
        for i in range(len(index)):
            f = front_index[i]
            if f >= 0 and np.isfinite(perp_close[i]) and perp_close[i] > 0:
                price = contract_close[f, i]
                if np.isfinite(price) and price > 0:
                    front_basis[i] = np.log(price / perp_close[i]) * 10_000.0
            n = next_index[i]
            if f >= 0 and n >= 0:
                fp = contract_close[f, i]
                np_ = contract_close[n, i]
                if np.isfinite(fp) and np.isfinite(np_) and fp > 0 and np_ > 0:
                    curve[i] = np.log(np_ / fp) * 10_000.0

        zscores: dict[tuple[str, int], np.ndarray] = {}
        centers: dict[tuple[str, int], np.ndarray] = {}
        for name, values in (("front_basis", front_basis), ("curve", curve)):
            series = pd.Series(values, index=index)
            for lookback in (168, 336):
                center, z = _causal_mean_z(series, lookback)
                centers[(name, lookback)] = center
                zscores[(name, lookback)] = z

        assets[asset] = PreparedAsset(
            perp_open=perp_open,
            perp_close=perp_close,
            funding_paid=funding_paid,
            funding_forecast=funding_forecast,
            contract_symbols=symbols,
            contract_expiry=expiry,
            contract_open=contract_open,
            contract_close=contract_close,
            front_index=front_index,
            next_index=next_index,
            front_basis_bps=front_basis,
            curve_bps=curve,
            zscores=zscores,
            centers=centers,
        )
    return PreparedMarket(index=index, assets=assets)


def subset(prepared: PreparedMarket, end: str) -> PreparedMarket:
    end_ts = pd.Timestamp(end, tz="UTC")
    keep = prepared.index <= end_ts
    return PreparedMarket(
        index=prepared.index[keep],
        assets={
            asset: PreparedAsset(
                perp_open=value.perp_open[keep],
                perp_close=value.perp_close[keep],
                funding_paid=value.funding_paid[keep],
                funding_forecast=value.funding_forecast[keep],
                contract_symbols=value.contract_symbols,
                contract_expiry=value.contract_expiry,
                contract_open=value.contract_open[:, keep],
                contract_close=value.contract_close[:, keep],
                front_index=value.front_index[keep],
                next_index=value.next_index[keep],
                front_basis_bps=value.front_basis_bps[keep],
                curve_bps=value.curve_bps[keep],
                zscores={key: arr[keep] for key, arr in value.zscores.items()},
                centers={key: arr[keep] for key, arr in value.centers.items()},
            )
            for asset, value in prepared.assets.items()
        },
    )


def signal_arrays(
    asset: PreparedAsset, policy: Policy
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    n = len(asset.perp_close)
    mask = np.zeros(n, dtype=bool)
    direction = np.zeros(n, dtype=float)
    score = np.full(n, np.nan)
    first_contract = np.full(n, -1, dtype=int)
    second_contract = np.full(n, -1, dtype=int)

    if policy.family == "front_next_curve_convergence":
        spread = asset.curve_bps
        center = asset.centers[("curve", policy.lookback_hours)]
        z = asset.zscores[("curve", policy.lookback_hours)]
        residual = spread - center
        expected = np.abs(residual)
        mask = (
            np.isfinite(z)
            & np.isfinite(expected)
            & (np.abs(z) >= policy.entry_abs_z)
            & (expected >= policy.minimum_expected_edge_bps)
            & (asset.front_index >= 0)
            & (asset.next_index >= 0)
        )
        # +1 means long next, short front. Positive residual should be faded.
        direction = -np.sign(residual)
        score = expected
        first_contract = asset.next_index.copy()
        second_contract = asset.front_index.copy()
    else:
        spread = asset.front_basis_bps
        center = asset.centers[("front_basis", policy.lookback_hours)]
        z = asset.zscores[("front_basis", policy.lookback_hours)]
        residual = spread - center
        if policy.family == "funding_adjusted_calendar_carry":
            funding_bps = (
                asset.funding_forecast
                * 10_000.0
                * max(1.0, policy.hold_hours / 8.0)
            )
            directional_value = -residual + funding_bps
            expected = np.abs(directional_value)
            direction = np.sign(directional_value)
        else:
            expected = np.abs(residual)
            direction = -np.sign(residual)
            if policy.family == "reversed_basis_control":
                direction *= -1.0
        mask = (
            np.isfinite(z)
            & np.isfinite(expected)
            & (np.abs(z) >= policy.entry_abs_z)
            & (expected >= policy.minimum_expected_edge_bps)
            & (asset.front_index >= 0)
        )
        score = expected
        first_contract = asset.front_index.copy()
        second_contract.fill(-2)  # sentinel for perpetual

    direction = np.where(mask, direction, 0.0)
    score = np.where(mask, score, np.nan)
    first_contract = np.where(mask, first_contract, -1)
    second_contract = np.where(mask, second_contract, -1)
    return mask, direction, score, first_contract, second_contract


def _price(
    asset: PreparedAsset, contract_index: int, i: int, field: str
) -> float | None:
    if contract_index == -2:
        value = asset.perp_open[i] if field == "open" else asset.perp_close[i]
    elif contract_index >= 0:
        matrix = asset.contract_open if field == "open" else asset.contract_close
        if contract_index >= matrix.shape[0]:
            return None
        value = matrix[contract_index, i]
    else:
        return None
    return float(value) if np.isfinite(value) and value > 0 else None


def simulate(
    prepared: PreparedMarket, policy: Policy, audit: Audit
) -> tuple[pd.DataFrame, pd.DataFrame]:
    index = prepared.index
    n = len(index)
    signals = {
        asset: signal_arrays(prepared.assets[asset], policy) for asset in ASSETS
    }
    equity = np.full(n, INITIAL_EQUITY)
    gross = np.zeros(n)
    turnover = np.zeros(n)
    costs = np.zeros(n)
    funding_pnl = np.zeros(n)
    trade_events = np.zeros(n)
    forced_exits = np.zeros(n)

    realized = INITIAL_EQUITY
    marked = INITIAL_EQUITY
    active: dict[str, Any] | None = None
    pending: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []

    def mark_position(position: dict[str, Any], i: int, field: str) -> tuple[float, float, float, float, float] | None:
        asset = prepared.assets[position["asset"]]
        p1 = _price(asset, position["first_contract"], i, field)
        p2 = _price(asset, position["second_contract"], i, field)
        if p1 is None or p2 is None:
            return None
        direction = float(position["direction"])
        pnl = direction * position["q1"] * (p1 - position["entry_p1"])
        pnl -= direction * position["q2"] * (p2 - position["entry_p2"])
        gross_notional = abs(position["q1"] * p1) + abs(position["q2"] * p2)
        value = position["capital_after_entry"] + pnl + position["cumulative_funding"]
        return value, pnl, gross_notional, p1, p2

    def close_position(position: dict[str, Any], i: int, reason: str, forced: bool) -> float:
        nonlocal marked
        result = mark_position(position, i, "open")
        if result is None:
            p1 = position["last_p1"]
            p2 = position["last_p2"]
            pnl = position["last_price_pnl"]
            gross_notional = abs(position["q1"] * p1) + abs(position["q2"] * p2)
            value = marked
            forced = True
        else:
            value, pnl, gross_notional, p1, p2 = result
        exit_cost = gross_notional * audit.pair_round_trip_bps / 20_000.0
        extra = gross_notional * audit.forced_exit_extra_bps / 10_000.0 if forced else 0.0
        after = max(0.0, value - exit_cost - extra)
        costs[i] += exit_cost + extra
        turnover[i] += gross_notional / max(value, 1e-12)
        trade_events[i] += 1.0
        if forced:
            forced_exits[i] += 1.0
        capital_before = position["capital_before"]
        trades.append(
            {
                "asset": position["asset"],
                "family": policy.family,
                "policy": policy.name,
                "direction_first": position["direction"],
                "first_symbol": position["first_symbol"],
                "second_symbol": position["second_symbol"],
                "signal_time": position["signal_time"],
                "entry_time": position["entry_time"],
                "exit_time": index[i],
                "exit_reason": reason,
                "holding_hours": i - position["entry_index"],
                "signal_score_bps": position["signal_score"],
                "entry_cost": position["entry_cost"],
                "exit_cost": exit_cost,
                "forced_cost": extra,
                "price_pnl": pnl,
                "funding_pnl": position["cumulative_funding"],
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
        asset = prepared.assets[signal["asset"]]
        p1 = _price(asset, signal["first_contract"], i, "open")
        p2 = _price(asset, signal["second_contract"], i, "open")
        if p1 is None or p2 is None:
            return None
        capital_before = realized
        q = capital_before * TARGET_GROSS / (p1 + p2)
        gross_notional = q * (p1 + p2)
        entry_cost = gross_notional * audit.pair_round_trip_bps / 20_000.0
        capital_after = max(0.0, capital_before - entry_cost)
        costs[i] += entry_cost
        turnover[i] += gross_notional / max(capital_before, 1e-12)
        trade_events[i] += 1.0
        realized = capital_after
        marked = capital_after
        first_symbol = (
            "PERPETUAL"
            if signal["first_contract"] == -2
            else asset.contract_symbols[signal["first_contract"]]
        )
        second_symbol = (
            "PERPETUAL"
            if signal["second_contract"] == -2
            else asset.contract_symbols[signal["second_contract"]]
        )
        first_expiry = (
            np.iinfo(np.int64).max
            if signal["first_contract"] == -2
            else int(asset.contract_expiry[signal["first_contract"]])
        )
        second_expiry = (
            np.iinfo(np.int64).max
            if signal["second_contract"] == -2
            else int(asset.contract_expiry[signal["second_contract"]])
        )
        latest_exit_ns = min(first_expiry, second_expiry) - int(CLOSE_BEFORE_EXPIRY_HOURS * 3600 * 1e9)
        latest_exit_index = int(np.searchsorted(index.view("i8"), latest_exit_ns, side="left"))
        return {
            **signal,
            "entry_index": i,
            "entry_time": index[i],
            "exit_index": min(i + policy.hold_hours, latest_exit_index),
            "capital_before": capital_before,
            "capital_after_entry": capital_after,
            "entry_cost": entry_cost,
            "entry_p1": p1,
            "entry_p2": p2,
            "q1": q,
            "q2": q,
            "first_symbol": first_symbol,
            "second_symbol": second_symbol,
            "cumulative_funding": 0.0,
            "last_p1": p1,
            "last_p2": p2,
            "last_price_pnl": 0.0,
            "planned_expiry_exit": latest_exit_index <= i + policy.hold_hours,
        }

    for i, timestamp in enumerate(index):
        # Funding stamped at this boundary belongs to the interval just completed.
        if active is not None and active["second_contract"] == -2:
            asset = prepared.assets[active["asset"]]
            rate = float(asset.funding_paid[i])
            if rate != 0.0 and np.isfinite(rate):
                perp_price = asset.perp_open[i]
                if np.isfinite(perp_price) and perp_price > 0:
                    # second leg direction is -direction; positive funding pays shorts.
                    flow = active["direction"] * active["q2"] * perp_price * rate
                    active["cumulative_funding"] += flow
                    funding_pnl[i] += flow

        if active is not None and i >= active["exit_index"]:
            reason = "expiry_buffer" if active["planned_expiry_exit"] else "fixed_horizon"
            realized = close_position(active, i, reason, forced=False)
            active = None
            marked = realized

        if active is None and pending is not None and i >= pending["target_index"]:
            active = enter(pending, i)
            pending = None

        if active is not None:
            result = mark_position(active, i, "close")
            if result is None:
                realized = close_position(active, i, "missing_price", forced=True)
                active = None
                marked = realized
            else:
                marked, pnl, gross_notional, p1, p2 = result
                active["last_p1"] = p1
                active["last_p2"] = p2
                active["last_price_pnl"] = pnl
                gross[i] = gross_notional / max(marked, 1e-12)
        else:
            marked = realized
        equity[i] = max(0.0, marked)

        if active is None and pending is None:
            choices: list[tuple[float, str, float, int, int]] = []
            for asset_name in ASSETS:
                mask, direction, score, first, second = signals[asset_name]
                if not mask[i] or direction[i] == 0 or not np.isfinite(score[i]):
                    continue
                choices.append(
                    (
                        float(score[i]),
                        asset_name,
                        float(direction[i]),
                        int(first[i]),
                        int(second[i]),
                    )
                )
            if choices:
                score_value, asset_name, direction_value, first_value, second_value = max(
                    choices, key=lambda item: (item[0], item[1])
                )
                pending = {
                    "target_index": i + 1 + audit.execution_delay_hours,
                    "asset": asset_name,
                    "direction": direction_value,
                    "first_contract": first_value,
                    "second_contract": second_value,
                    "signal_time": timestamp,
                    "signal_score": score_value,
                }

    if active is not None and n:
        i = n - 1
        realized = close_position(active, i, "end_of_sample", forced=False)
        equity[i] = realized
        gross[i] = 0.0

    account = pd.DataFrame(
        {
            "equity": equity,
            "gross": gross,
            "turnover": turnover,
            "costs": costs,
            "funding_pnl": funding_pnl,
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
    obs = len(returns) / years if years > 0 else 0.0
    std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    sharpe = float(returns.mean() / std * np.sqrt(obs)) if std > 0 else 0.0
    dd = account.equity / account.equity.cummax() - 1.0
    return {
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": float(dd.min()),
        "sharpe": sharpe,
        "annual_turnover": float(account.turnover.sum() / years) if years > 0 else 0.0,
        "average_gross": float(account.gross.mean()),
        "max_gross": float(account.gross.max()),
        "costs": float(account.costs.sum()),
        "funding_pnl": float(account.funding_pnl.sum()),
        "trade_count": int(account.trade_events.sum() // 2),
        "forced_exits": int(account.forced_exits.sum()),
        "observations_per_year": obs,
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
    selected["funding_pnl"] *= scale
    return selected


def yearly_returns(account: pd.DataFrame, name: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    previous = INITIAL_EQUITY
    for year, frame in account.groupby(account.index.year):
        end = float(frame.equity.iloc[-1])
        rows.append({"year": int(year), name: end / previous - 1.0})
        previous = end
    return pd.DataFrame(rows)


def ensemble(accounts: list[pd.DataFrame]) -> pd.DataFrame:
    if not accounts:
        raise ValueError("empty ensemble")
    index = accounts[0].index
    returns = []
    for account in accounts:
        values = account.equity.pct_change()
        values.iloc[0] = account.equity.iloc[0] / INITIAL_EQUITY - 1.0
        returns.append(values.to_numpy(float))
    mean = np.nanmean(np.vstack(returns), axis=0)
    result = pd.DataFrame(index=index)
    result["equity"] = INITIAL_EQUITY * np.cumprod(1.0 + mean)
    for column in (
        "gross",
        "turnover",
        "costs",
        "funding_pnl",
        "trade_events",
        "forced_exits",
    ):
        result[column] = np.mean(
            np.vstack([account[column].to_numpy(float) for account in accounts]), axis=0
        )
    return result


def policy_dict(policy: Policy) -> dict[str, Any]:
    return asdict(policy) | {"name": policy.name}
