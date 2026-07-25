from __future__ import annotations

import numpy as np
import pandas as pd


def _vol(prices: pd.DataFrame, lookback: int = 63) -> pd.DataFrame:
    return prices.pct_change(fill_method=None).rolling(
        lookback, min_periods=max(20, lookback // 2)
    ).std() * np.sqrt(252.0)


def _safe_div(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
    return left.divide(right.replace(0.0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(0.0)


def tsmom(prices: pd.DataFrame) -> pd.DataFrame:
    score = sum(np.sign(prices.pct_change(n, fill_method=None)) for n in (63, 126, 252)) / 3.0
    return _safe_div(score, _vol(prices))


def ma_stack(prices: pd.DataFrame) -> pd.DataFrame:
    score = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    for n in (63, 126, 252):
        ma = prices.rolling(n, min_periods=max(30, n // 2)).mean()
        score += np.sign(prices / ma - 1.0)
    return _safe_div(score / 3.0, _vol(prices))


def breakout(prices: pd.DataFrame) -> pd.DataFrame:
    pieces = []
    for n in (126, 252):
        high = prices.rolling(n, min_periods=n // 2).max().shift(1)
        low = prices.rolling(n, min_periods=n // 2).min().shift(1)
        signal = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
        signal = signal.mask(prices > high, 1.0).mask(prices < low, -1.0)
        pieces.append(signal)
    return _safe_div(sum(pieces) / len(pieces), _vol(prices))


def relative_momentum(prices: pd.DataFrame, groups: dict[str, str]) -> pd.DataFrame:
    score = _safe_div(prices.pct_change(126, fill_method=None), _vol(prices))
    output = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    for group in sorted(set(groups.values())):
        columns = [c for c in prices if groups.get(c) == group]
        if len(columns) < 2:
            continue
        ranks = score[columns].rank(axis=1, pct=True)
        output[columns] = ((ranks >= 0.67).astype(float) - (ranks <= 0.33).astype(float))
    return _safe_div(output, _vol(prices))


def crisis_defensive(prices: pd.DataFrame) -> pd.DataFrame:
    output = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    if "SPY" not in prices:
        return output
    risk = (prices["SPY"] < prices["SPY"].rolling(200, min_periods=120).mean()).astype(float)
    if "HYG" in prices and "IEF" in prices:
        credit = prices["HYG"] / prices["IEF"]
        risk = np.maximum(risk, (credit < credit.rolling(126, min_periods=63).mean()).astype(float))
    for column in ("TLT", "IEF", "GLD", "UUP", "FX_JPY", "FX_CHF"):
        if column in prices:
            output[column] = risk
    for column in ("SPY", "QQQ", "IWM", "EEM", "HYG", "DBC"):
        if column in prices:
            output[column] = -risk
    return _safe_div(output, _vol(prices))


def family_book(prices: pd.DataFrame, groups: dict[str, str]) -> dict[str, pd.DataFrame]:
    return {
        "tsmom": tsmom(prices),
        "ma": ma_stack(prices),
        "breakout": breakout(prices),
        "relative": relative_momentum(prices, groups),
        "crisis": crisis_defensive(prices),
    }


def process_book(families: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    subsets = [(name,) for name in families]
    subsets.extend([
        ("tsmom", "ma"),
        ("tsmom", "breakout"),
        ("tsmom", "ma", "breakout"),
        ("tsmom", "relative"),
        ("tsmom", "crisis"),
        ("tsmom", "ma", "crisis"),
        ("tsmom", "ma", "breakout", "crisis"),
        tuple(families),
    ])
    return {
        "+".join(subset): sum(families[name] for name in subset) / len(subset)
        for subset in subsets
    }
