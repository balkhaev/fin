from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from config import Policy, ROLL_DAYS_BEFORE_EXPIRY


@dataclass
class MarketData:
    index: pd.DatetimeIndex
    expiries: tuple[pd.Timestamp, ...]
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    settle: pd.DataFrame
    volume: pd.DataFrame
    open_interest: pd.DataFrame
    front: pd.Series
    second: pd.Series
    features: pd.DataFrame


def pivot(contracts: pd.DataFrame, column: str, index: pd.DatetimeIndex) -> pd.DataFrame:
    frame = contracts.pivot(index="Trade Date", columns="Expiry", values=column)
    frame.index = pd.to_datetime(frame.index)
    frame.columns = pd.to_datetime(frame.columns)
    return frame.reindex(index).sort_index(axis=1)


def select_contracts(
    index: pd.DatetimeIndex,
    settle: pd.DataFrame,
    volume: pd.DataFrame,
) -> tuple[pd.Series, pd.Series]:
    front: list[pd.Timestamp | pd.NaT] = []
    second: list[pd.Timestamp | pd.NaT] = []
    expiries = list(settle.columns)
    for day in index:
        candidates = [
            expiry
            for expiry in expiries
            if (expiry - day).days > ROLL_DAYS_BEFORE_EXPIRY
            and pd.notna(settle.at[day, expiry])
        ]
        # A missing volume is allowed for early files, but zero-volume rows are
        # avoided when a later liquid monthly contract is available.
        liquid = [
            expiry
            for expiry in candidates
            if pd.isna(volume.at[day, expiry]) or float(volume.at[day, expiry]) > 0
        ]
        chosen = liquid if len(liquid) >= 2 else candidates
        front.append(chosen[0] if chosen else pd.NaT)
        second.append(chosen[1] if len(chosen) > 1 else pd.NaT)
    return (
        pd.Series(front, index=index, name="front_expiry", dtype="datetime64[ns]"),
        pd.Series(second, index=index, name="second_expiry", dtype="datetime64[ns]"),
    )


def select_value(frame: pd.DataFrame, contracts: pd.Series) -> pd.Series:
    result = pd.Series(index=contracts.index, dtype=float)
    for day, expiry in contracts.items():
        if pd.isna(expiry) or expiry not in frame.columns:
            continue
        result.at[day] = frame.at[day, expiry]
    return result


def back_adjusted_front_return(settle: pd.DataFrame, front: pd.Series) -> pd.Series:
    result = pd.Series(0.0, index=front.index)
    previous_day = None
    for day in front.index:
        expiry = front.at[day]
        if previous_day is None or pd.isna(expiry):
            previous_day = day
            continue
        current = settle.at[day, expiry] if expiry in settle.columns else np.nan
        previous = settle.at[previous_day, expiry] if expiry in settle.columns else np.nan
        if pd.notna(current) and pd.notna(previous) and previous > 0:
            result.at[day] = float(current / previous - 1.0)
        previous_day = day
    return result.clip(lower=-0.95, upper=5.0)


def build_market(contracts: pd.DataFrame, spot: pd.DataFrame) -> MarketData:
    contracts = contracts.copy()
    contracts["Trade Date"] = pd.to_datetime(contracts["Trade Date"])
    contracts["Expiry"] = pd.to_datetime(contracts["Expiry"])
    start = max(pd.Timestamp("2004-01-01"), contracts["Trade Date"].min())
    end = contracts["Trade Date"].max()
    index = pd.DatetimeIndex(sorted(contracts.loc[
        (contracts["Trade Date"] >= start) & (contracts["Trade Date"] <= end), "Trade Date"
    ].unique()))
    panels = {
        column: pivot(contracts, column, index)
        for column in ("Open", "High", "Low", "Settle", "Total Volume", "Open Interest")
    }
    front, second = select_contracts(index, panels["Settle"], panels["Total Volume"])
    front_settle = select_value(panels["Settle"], front)
    second_settle = select_value(panels["Settle"], second)
    front_return = back_adjusted_front_return(panels["Settle"], front)
    front_signal = 100.0 * (1.0 + front_return).cumprod()

    spot_frame = spot.copy()
    spot_frame["DATE"] = pd.to_datetime(spot_frame["DATE"])
    spot_close = spot_frame.set_index("DATE")["CLOSE"].reindex(index).ffill(limit=3)
    features = pd.DataFrame(index=index)
    features["front_settle"] = front_settle
    features["second_settle"] = second_settle
    features["curve"] = front_settle / second_settle - 1.0
    features["front_mom5"] = front_signal.pct_change(5, fill_method=None)
    features["front_mom20"] = front_signal.pct_change(20, fill_method=None)
    features["spot"] = spot_close
    features["spot_ret5"] = spot_close.pct_change(5, fill_method=None)
    features["spot_ret20"] = spot_close.pct_change(20, fill_method=None)
    features["spot_ema10"] = spot_close.ewm(span=10, adjust=False, min_periods=10).mean()
    features["spot_ema20"] = spot_close.ewm(span=20, adjust=False, min_periods=20).mean()
    features["spot_ema60"] = spot_close.ewm(span=60, adjust=False, min_periods=60).mean()
    features["spot_percentile_252"] = spot_close.rolling(252, min_periods=126).rank(pct=True)
    features["front_expiry"] = front
    features["second_expiry"] = second
    return MarketData(
        index=index,
        expiries=tuple(panels["Settle"].columns),
        open=panels["Open"],
        high=panels["High"],
        low=panels["Low"],
        settle=panels["Settle"],
        volume=panels["Total Volume"],
        open_interest=panels["Open Interest"],
        front=front,
        second=second,
        features=features,
    )


def policy_target(policy: Policy, market: MarketData) -> pd.DataFrame:
    result = pd.DataFrame(0.0, index=market.index, columns=market.expiries)
    active = False
    age = 0
    for day in market.index:
        row = market.features.loc[day]
        front = market.front.at[day]
        second = market.second.at[day]
        valid_front = pd.notna(front) and front in result.columns
        valid_second = pd.notna(second) and second in result.columns
        if not valid_front:
            active = False
            age = 0
            continue

        if policy.family == "backwardation_long":
            enter = bool(
                row.curve > policy.threshold
                and row.spot > row.spot_ema20
                and row.front_mom5 > 0
            )
            exit_ = bool(row.curve < 0 and row.spot < row.spot_ema10)
        elif policy.family == "curve_spread":
            enter = bool(valid_second and row.curve > policy.threshold and row.front_mom5 > 0)
            exit_ = bool(row.curve <= 0 or not valid_second)
        elif policy.family == "spot_spike_long":
            enter = bool(row.spot_ret5 > policy.threshold and row.spot > row.spot_ema20)
            exit_ = bool(row.spot_ret5 < 0 or row.spot < row.spot_ema10)
        elif policy.family == "tail_long":
            enter = bool(
                row.spot_percentile_252 >= policy.threshold
                and row.front_mom5 > 0
                and row.spot > row.spot_ema20
            )
            exit_ = bool(row.spot_percentile_252 < 0.70 or row.front_mom5 < 0)
        else:
            raise ValueError(policy.family)

        if active:
            age += 1
            if age >= policy.hold_days and exit_:
                active = False
                age = 0
        elif enter:
            active = True
            age = 1

        if not active:
            continue
        if policy.family == "curve_spread" and valid_second:
            result.at[day, front] = policy.budget / 2.0
            result.at[day, second] = -policy.budget / 2.0
        else:
            result.at[day, front] = policy.budget
    return result


def self_test() -> None:
    index = pd.date_range("2020-01-01", periods=300, freq="B")
    expiries = pd.to_datetime(["2020-06-17", "2020-07-22", "2020-08-19"])
    base = pd.DataFrame(index=index, columns=expiries, dtype=float)
    for i, expiry in enumerate(expiries):
        base[expiry] = 15 + i + np.arange(len(index)) * 0.01
        base.loc[index > expiry, expiry] = np.nan
    contracts = []
    for expiry in expiries:
        for day, value in base[expiry].dropna().items():
            contracts.append(
                {
                    "Trade Date": day,
                    "Expiry": expiry,
                    "Open": value,
                    "High": value * 1.01,
                    "Low": value * 0.99,
                    "Settle": value,
                    "Total Volume": 100,
                    "Open Interest": 1000,
                }
            )
    spot = pd.DataFrame({"DATE": index, "CLOSE": np.linspace(15, 30, len(index))})
    market = build_market(pd.DataFrame(contracts), spot)
    assert len(market.index) == len(index)
    assert market.features["curve"].notna().any()
