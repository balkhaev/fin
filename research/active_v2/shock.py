from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from config import Costs, ResearchConfig, ShockParams
from data import aggregate, build_base_features
from metrics import equity_metrics


@dataclass
class ShockTrade:
    symbol: str
    signal_time: pd.Timestamp
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    stop_distance: float
    net_return: float
    r_multiple: float
    exit_reason: str
    bars_held: int


class ShockCache:
    def __init__(self, raw: dict[str, pd.DataFrame]):
        self.symbols = tuple(sorted(raw))
        self.frames: dict[str, pd.DataFrame] = {}
        self.trend_close: dict[str, pd.Series] = {}
        self._trend_ema: dict[tuple[str, int], pd.Series] = {}
        index: pd.DatetimeIndex | None = None
        for symbol in self.symbols:
            base = build_base_features(raw[symbol])
            four_hour = aggregate(raw[symbol], "4h")
            trend_close = four_hour["close"]
            mapped = trend_close.reindex(base.index, method="ffill")
            base["trend_close"] = mapped
            self.frames[symbol] = base
            self.trend_close[symbol] = trend_close
            index = base.index if index is None else index.intersection(base.index)
        if index is None or len(index) < 20_000:
            raise ValueError("insufficient common 15m history")
        self.index = index.sort_values()
        for symbol in self.symbols:
            self.frames[symbol] = self.frames[symbol].reindex(self.index)

    def trend_ema(self, symbol: str, days: int) -> pd.Series:
        key = (symbol, days)
        if key not in self._trend_ema:
            bars = days * 6
            ema_4h = self.trend_close[symbol].ewm(span=bars, adjust=False, min_periods=bars).mean()
            self._trend_ema[key] = ema_4h.reindex(self.index, method="ffill")
        return self._trend_ema[key]

    def signal_mask(self, symbol: str, params: ShockParams) -> pd.Series:
        frame = self.frames[symbol]
        ema = self.trend_ema(symbol, params.trend_ema_days)
        return (
            (frame[f"shock_z_{params.shock_bars}"] <= params.z_threshold)
            & (frame["trend_close"] > ema)
            & (frame["bar_location"] >= params.bar_location)
            & (frame["close"] > frame["open"])
            & (frame["taker_ratio"] >= params.taker_ratio)
            & (frame["volume_ratio"] >= params.volume_ratio)
        ).fillna(False)


def simulate_symbol_trades(
    cache: ShockCache,
    symbol: str,
    params: ShockParams,
    costs: Costs,
    config: ResearchConfig,
    start: str,
    end: str,
) -> list[ShockTrade]:
    frame = cache.frames[symbol]
    mask = cache.signal_mask(symbol, params).to_numpy(dtype=bool)
    index = cache.index
    start_ts, end_ts = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    start_i = int(index.searchsorted(start_ts, side="left"))
    end_i = int(index.searchsorted(end_ts, side="left"))
    signal_indices = np.flatnonzero(mask[start_i:end_i]) + start_i
    open_ = frame["open"].to_numpy(float)
    high = frame["high"].to_numpy(float)
    low = frame["low"].to_numpy(float)
    close = frame["close"].to_numpy(float)
    atr = frame["atr14"].to_numpy(float)
    recent_low = frame[f"low_{params.shock_bars}"].to_numpy(float)
    trades: list[ShockTrade] = []
    blocked_until = -1
    for signal_i in signal_indices:
        if signal_i <= blocked_until or signal_i + 1 >= end_i:
            continue
        if not np.isfinite(atr[signal_i]) or atr[signal_i] <= 0:
            continue
        entry_i = signal_i + 1
        entry = open_[entry_i] * (1 + costs.rate)
        stop_distance = max(
            params.stop_atr * atr[signal_i],
            close[signal_i] - recent_low[signal_i] + 0.10 * atr[signal_i],
        )
        if not np.isfinite(stop_distance) or stop_distance <= 0 or stop_distance >= entry:
            continue
        stop = entry - stop_distance
        target = entry + params.target_r * stop_distance
        final_i = min(end_i - 1, entry_i + params.max_hold_bars - 1)
        raw_exit = close[final_i]
        reason = "time"
        exit_i = final_i
        for j in range(entry_i, final_i + 1):
            if open_[j] <= stop:
                raw_exit, reason, exit_i = open_[j], "gap_stop", j
                break
            if low[j] <= stop:
                raw_exit, reason, exit_i = stop, "stop", j
                break
            if open_[j] >= target:
                raw_exit, reason, exit_i = target, "gap_target_conservative", j
                break
            if high[j] >= target:
                raw_exit, reason, exit_i = target, "target", j
                break
        exit_price = raw_exit * (1 - costs.rate)
        quantity_per_equity = min(config.risk_per_trade / stop_distance, 0.50 / entry)
        net_return = quantity_per_equity * (exit_price - entry)
        r_multiple = (exit_price - entry) / stop_distance
        trades.append(
            ShockTrade(
                symbol=symbol,
                signal_time=index[signal_i],
                entry_time=index[entry_i],
                exit_time=index[exit_i],
                entry_price=float(entry),
                exit_price=float(exit_price),
                stop_distance=float(stop_distance),
                net_return=float(net_return),
                r_multiple=float(r_multiple),
                exit_reason=reason,
                bars_held=int(exit_i - entry_i + 1),
            )
        )
        blocked_until = exit_i
    return trades


def trade_equity(trades: list[ShockTrade], config: ResearchConfig, start: str, end: str) -> pd.Series:
    start_ts, end_ts = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    equity = config.starting_equity
    times = [start_ts]
    values = [equity]
    for trade in sorted(trades, key=lambda item: (item.exit_time, item.symbol)):
        equity *= max(0.0, 1 + trade.net_return)
        times.append(trade.exit_time)
        values.append(equity)
    times.append(end_ts - pd.Timedelta(minutes=15))
    values.append(equity)
    sparse = pd.Series(values, index=pd.DatetimeIndex(times)).groupby(level=0).last().sort_index()
    daily_index = pd.date_range(start_ts.normalize(), (end_ts - pd.Timedelta(days=1)).normalize(), freq="1D", tz="UTC")
    union = sparse.index.union(daily_index).sort_values()
    return sparse.reindex(union).ffill().reindex(daily_index)


def compact_trade_metrics(trades: list[ShockTrade]) -> dict[str, float]:
    if not trades:
        return {"trades": 0, "profit_factor": np.nan, "win_rate": np.nan, "average_r": np.nan}
    returns = np.array([trade.net_return for trade in trades], dtype=float)
    gains, losses = returns[returns > 0].sum(), returns[returns < 0].sum()
    pf = gains / abs(losses) if losses < 0 else (np.inf if gains > 0 else np.nan)
    return {
        "trades": int(len(trades)),
        "profit_factor": float(pf),
        "win_rate": float((returns > 0).mean()),
        "average_r": float(np.mean([trade.r_multiple for trade in trades])),
    }


def shock_score(dev: dict[str, float], val: dict[str, float], dev_trades: int, val_trades: int) -> float:
    if dev_trades < 30 or val_trades < 15:
        return -1e9
    if dev["total_return"] <= 0 or val["total_return"] <= 0:
        return -1e9
    if dev["max_drawdown"] < -0.20 or val["max_drawdown"] < -0.20:
        return -1e9
    weak_calmar = min(dev["calmar"], val["calmar"])
    weak_sharpe = min(dev["sharpe"], val["sharpe"])
    weak_return = min(dev["annualized_return"], val["annualized_return"])
    if not np.isfinite(weak_calmar) or not np.isfinite(weak_sharpe):
        return -1e9
    return float(0.55 * weak_calmar + 0.25 * weak_sharpe + 0.20 * weak_return)


def evaluate_shock_grid(
    cache: ShockCache,
    params_grid: Iterable[ShockParams],
    costs: Costs,
    config: ResearchConfig,
) -> pd.DataFrame:
    periods = {
        "development": (config.start, config.development_end),
        "validation": (config.development_end, config.validation_end),
    }
    rows: list[dict[str, object]] = []
    for number, params in enumerate(params_grid, start=1):
        row: dict[str, object] = {"key": params.key, **asdict(params)}
        period_values: dict[str, dict[str, float]] = {}
        trade_counts: dict[str, int] = {}
        for period, (start, end) in periods.items():
            trades: list[ShockTrade] = []
            for symbol in cache.symbols:
                trades.extend(simulate_symbol_trades(cache, symbol, params, costs, config, start, end))
            equity = trade_equity(trades, config, start, end)
            values = equity_metrics(equity)
            compact = compact_trade_metrics(trades)
            period_values[period] = values
            trade_counts[period] = compact["trades"]
            for key, value in values.items():
                row[f"{period}_{key}"] = value
            for key, value in compact.items():
                row[f"{period}_{key}"] = value
        row["robust_score"] = shock_score(
            period_values["development"],
            period_values["validation"],
            trade_counts["development"],
            trade_counts["validation"],
        )
        rows.append(row)
        if number % 100 == 0:
            print(f"shock candidates evaluated: {number}")
    return pd.DataFrame(rows).sort_values("robust_score", ascending=False).reset_index(drop=True)


def neighbor_count(results: pd.DataFrame) -> pd.Series:
    columns = [
        "shock_bars", "z_threshold", "trend_ema_days", "bar_location", "taker_ratio",
        "volume_ratio", "stop_atr", "target_r", "max_hold_bars",
    ]
    viable = results[results["robust_score"] > -1e8]
    counts: list[int] = []
    for _, row in results.iterrows():
        count = 0
        for _, other in viable.iterrows():
            if sum(row[column] != other[column] for column in columns) <= 1:
                count += 1
        counts.append(count)
    return pd.Series(counts, index=results.index, dtype=int)


def portfolio_backtest(
    cache: ShockCache,
    params: ShockParams,
    costs: Costs,
    config: ResearchConfig,
    start: str,
    end: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    begin, finish = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    mask = (cache.index >= begin) & (cache.index < finish)
    index = cache.index[mask]
    if len(index) < 1_000:
        raise ValueError("insufficient shock test period")
    signal_masks = {symbol: cache.signal_mask(symbol, params).reindex(index).fillna(False) for symbol in cache.symbols}
    equity = config.starting_equity
    cash = equity
    high_water = equity
    pending: dict[str, int] = {}
    positions: dict[str, dict[str, float | int | pd.Timestamp]] = {}
    trade_rows: list[dict[str, object]] = []
    equity_rows: list[dict[str, object]] = []
    hard_stop = False
    for local_i, timestamp in enumerate(index):
        if not hard_stop:
            for symbol in list(pending):
                if symbol in positions:
                    pending.pop(symbol, None)
                    continue
                frame = cache.frames[symbol]
                entry = float(frame.at[timestamp, "open"]) * (1 + costs.rate)
                signal_global_i = pending.pop(symbol)
                signal_time = cache.index[signal_global_i]
                signal_row = frame.iloc[signal_global_i]
                stop_distance = max(
                    params.stop_atr * float(signal_row["atr14"]),
                    float(signal_row["close"] - signal_row[f"low_{params.shock_bars}"] + 0.10 * signal_row["atr14"]),
                )
                if not np.isfinite(stop_distance) or stop_distance <= 0 or stop_distance >= entry:
                    continue
                marked = cash + sum(
                    float(position["quantity"]) * float(cache.frames[s].at[timestamp, "open"])
                    for s, position in positions.items()
                )
                risk_quantity = marked * config.risk_per_trade / stop_distance
                symbol_cap = marked * 0.50 / entry
                total_notional = sum(
                    float(position["quantity"]) * float(cache.frames[s].at[timestamp, "open"])
                    for s, position in positions.items()
                )
                total_cap = max(0.0, marked - total_notional) / entry
                cash_cap = cash / entry
                quantity = min(risk_quantity, symbol_cap, total_cap, cash_cap)
                if quantity <= 0:
                    continue
                notional = quantity * entry
                cash -= notional
                positions[symbol] = {
                    "signal_time": signal_time, "entry_time": timestamp, "entry": entry,
                    "quantity": quantity, "notional": notional, "stop": entry - stop_distance,
                    "target": entry + params.target_r * stop_distance,
                    "stop_distance": stop_distance, "bars": 0,
                }
        for symbol in list(positions):
            frame = cache.frames[symbol]
            row = frame.loc[timestamp]
            position = positions[symbol]
            position["bars"] = int(position["bars"]) + 1
            stop, target = float(position["stop"]), float(position["target"])
            raw_exit: float | None = None
            reason = ""
            if float(row["open"]) <= stop:
                raw_exit, reason = float(row["open"]), "gap_stop"
            elif float(row["low"]) <= stop:
                raw_exit, reason = stop, "stop"
            elif float(row["open"]) >= target:
                raw_exit, reason = target, "gap_target_conservative"
            elif float(row["high"]) >= target:
                raw_exit, reason = target, "target"
            elif int(position["bars"]) >= params.max_hold_bars:
                raw_exit, reason = float(row["close"]), "time"
            if raw_exit is not None:
                exit_price = raw_exit * (1 - costs.rate)
                quantity = float(position["quantity"])
                proceeds = quantity * exit_price
                cash += proceeds
                net_pnl = proceeds - float(position["notional"])
                trade_rows.append({
                    "symbol": symbol, "signal_time": position["signal_time"],
                    "entry_time": position["entry_time"], "exit_time": timestamp,
                    "entry_price": position["entry"], "exit_price": exit_price,
                    "quantity": quantity, "net_pnl": net_pnl,
                    "net_return": net_pnl / float(position["notional"]),
                    "r_multiple": (exit_price - float(position["entry"])) / float(position["stop_distance"]),
                    "bars_held": position["bars"], "exit_reason": reason,
                })
                del positions[symbol]
        marked = cash + sum(
            float(position["quantity"]) * float(cache.frames[symbol].at[timestamp, "close"])
            for symbol, position in positions.items()
        )
        equity = marked
        high_water = max(high_water, equity)
        drawdown = equity / high_water - 1
        if drawdown <= -config.hard_drawdown_stop and not hard_stop:
            hard_stop = True
            pending.clear()
            for symbol in list(positions):
                position = positions.pop(symbol)
                exit_price = float(cache.frames[symbol].at[timestamp, "close"]) * (1 - costs.rate)
                quantity = float(position["quantity"])
                proceeds = quantity * exit_price
                cash += proceeds
                net_pnl = proceeds - float(position["notional"])
                trade_rows.append({
                    "symbol": symbol, "signal_time": position["signal_time"],
                    "entry_time": position["entry_time"], "exit_time": timestamp,
                    "entry_price": position["entry"], "exit_price": exit_price,
                    "quantity": quantity, "net_pnl": net_pnl,
                    "net_return": net_pnl / float(position["notional"]),
                    "r_multiple": (exit_price - float(position["entry"])) / float(position["stop_distance"]),
                    "bars_held": position["bars"], "exit_reason": "hard_drawdown_stop",
                })
            equity = cash
            high_water = max(high_water, equity)
            drawdown = equity / high_water - 1
        exposure = 0.0 if equity <= 0 else sum(
            float(position["quantity"]) * float(cache.frames[symbol].at[timestamp, "close"])
            for symbol, position in positions.items()
        ) / equity
        equity_rows.append({
            "time": timestamp, "equity": equity, "drawdown": drawdown,
            "exposure": exposure, "turnover": 0.0,
        })
        if not hard_stop and local_i + 1 < len(index):
            global_i = cache.index.get_loc(timestamp)
            for symbol in cache.symbols:
                if symbol not in positions and bool(signal_masks[symbol].at[timestamp]):
                    pending[symbol] = global_i
    if positions:
        timestamp = index[-1]
        for symbol, position in list(positions.items()):
            exit_price = float(cache.frames[symbol].at[timestamp, "close"]) * (1 - costs.rate)
            proceeds = float(position["quantity"]) * exit_price
            cash += proceeds
            trade_rows.append({
                "symbol": symbol, "signal_time": position["signal_time"],
                "entry_time": position["entry_time"], "exit_time": timestamp,
                "entry_price": position["entry"], "exit_price": exit_price,
                "quantity": position["quantity"],
                "net_pnl": proceeds - float(position["notional"]),
                "net_return": (proceeds - float(position["notional"])) / float(position["notional"]),
                "r_multiple": (exit_price - float(position["entry"])) / float(position["stop_distance"]),
                "bars_held": position["bars"], "exit_reason": "end_of_period",
            })
    if equity_rows:
        equity_rows[-1]["equity"] = cash
        equity_rows[-1]["exposure"] = 0.0
    return pd.DataFrame(equity_rows).set_index("time"), pd.DataFrame(trade_rows)
