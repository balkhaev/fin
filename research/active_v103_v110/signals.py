from __future__ import annotations

import numpy as np
import pandas as pd


def volatility(prices: pd.DataFrame, lookback: int = 63) -> pd.DataFrame:
    return prices.pct_change(fill_method=None).rolling(lookback, min_periods=30).std() * np.sqrt(252.0)


def momentum_score(prices: pd.DataFrame, lookbacks: tuple[int, ...]) -> pd.DataFrame:
    vol = volatility(prices)
    pieces = [prices.pct_change(n, fill_method=None) / vol.replace(0.0, np.nan) for n in lookbacks]
    return sum(pieces) / len(pieces)


def absolute_filter(prices: pd.DataFrame) -> pd.DataFrame:
    moving_average = prices.rolling(200, min_periods=120).mean()
    return (prices > moving_average) & (prices.pct_change(126, fill_method=None) > 0.0)


def make_target(
    prices: pd.DataFrame,
    groups: dict[str, str],
    lookbacks: tuple[int, ...],
    top_k: int,
    short_cap: float,
    family: str,
) -> pd.DataFrame:
    score = momentum_score(prices, lookbacks)
    output = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    sectors = [c for c in prices if groups.get(c) == "sector"]
    countries = [c for c in prices if groups.get(c) == "country"]
    defensive = [c for c in prices if groups.get(c) == "defensive"]
    universe = sectors if family == "sector" else countries if family == "country" else sectors + countries

    if family == "defensive":
        breadth = absolute_filter(prices[sectors + countries]).mean(axis=1)
        risk_off = breadth < 0.40
        defensive_score = score[defensive]
        for i, timestamp in enumerate(prices.index):
            if not bool(risk_off.iloc[i]):
                continue
            selected = defensive_score.iloc[i].dropna().sort_values(ascending=False).head(min(top_k, len(defensive)))
            if len(selected):
                output.loc[timestamp, selected.index] = 1.0 / len(selected)
        return output

    filter_frame = absolute_filter(prices[universe])
    for i, timestamp in enumerate(prices.index):
        long_candidates = score.loc[timestamp, universe].where(filter_frame.iloc[i]).dropna().sort_values(ascending=False)
        longs = long_candidates.head(min(top_k, len(long_candidates)))
        if len(longs):
            output.loc[timestamp, longs.index] = 1.0 / len(longs)
        if short_cap > 0.0:
            short_candidates = score.loc[timestamp, universe].where(~filter_frame.iloc[i]).dropna().sort_values().head(top_k)
            if len(short_candidates):
                output.loc[timestamp, short_candidates.index] -= short_cap / len(short_candidates)

    if family == "combined":
        breadth = filter_frame.mean(axis=1)
        defensive_score = score[defensive]
        for i, timestamp in enumerate(prices.index):
            if breadth.iloc[i] < 0.45:
                selected = defensive_score.iloc[i].dropna().sort_values(ascending=False).head(1)
                if len(selected):
                    output.iloc[i] *= 0.80
                    output.loc[timestamp, selected.index] = 0.20
    return output


def process_targets(prices: pd.DataFrame, groups: dict[str, str]) -> dict[str, pd.DataFrame]:
    output: dict[str, pd.DataFrame] = {}
    for family in ("sector", "country", "combined", "defensive"):
        for lookbacks in ((63, 126, 252), (126, 252), (21, 63, 126, 252)):
            for top_k in (2, 3, 4):
                for short_cap in (0.0, 0.15, 0.25):
                    if family == "defensive" and short_cap > 0.0:
                        continue
                    name = f"{family}_l{'-'.join(map(str, lookbacks))}_k{top_k}_s{int(short_cap * 100):02d}"
                    output[name] = make_target(prices, groups, lookbacks, top_k, short_cap, family)
    return output
