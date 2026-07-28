from __future__ import annotations

import sys
from collections import OrderedDict
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "research" / "active_v103_v110"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import signals as legacy_signals  # noqa: E402

LOOKBACKS = ((63, 126, 252), (126, 252), (21, 63, 126, 252))
TOP_K = (2, 3, 4)
SHORT_CAPS = (0.0, 0.15, 0.25)
FAMILIES = ("sector", "country", "combined", "defensive")


def _name(family: str, lookbacks: tuple[int, ...], top_k: int, short_cap: float) -> str:
    return (
        f"{family}_l{'-'.join(map(str, lookbacks))}"
        f"_k{top_k}_s{int(short_cap * 100):02d}"
    )


def process_targets_cached(
    prices: pd.DataFrame,
    groups: dict[str, str],
) -> dict[str, pd.DataFrame]:
    """Exact V103 signal book with shared rankings computed once per row.

    The function preserves the legacy family/grid insertion order and uses the
    same pandas ``sort_values`` operations as ``signals.make_target``. It only
    reuses rankings across neighboring ``top_k`` and ``short_cap`` variants.
    """

    sectors = [column for column in prices if groups.get(column) == "sector"]
    countries = [column for column in prices if groups.get(column) == "country"]
    defensive = [column for column in prices if groups.get(column) == "defensive"]
    absolute_all = legacy_signals.absolute_filter(prices)
    generated: dict[str, pd.DataFrame] = {}

    for lookbacks in LOOKBACKS:
        score = legacy_signals.momentum_score(prices, lookbacks)
        defensive_score = score[defensive]

        for family in ("sector", "country", "combined"):
            universe = sectors if family == "sector" else countries if family == "country" else sectors + countries
            filter_frame = absolute_all[universe]
            arrays = {
                (top_k, short_cap): np.zeros(prices.shape, dtype=float)
                for top_k in TOP_K
                for short_cap in SHORT_CAPS
            }
            column_positions = {column: prices.columns.get_loc(column) for column in prices}
            defensive_positions = {
                column: prices.columns.get_loc(column) for column in defensive
            }

            for i, timestamp in enumerate(prices.index):
                row_score = score.loc[timestamp, universe]
                row_filter = filter_frame.iloc[i]
                longs_ranked = (
                    row_score.where(row_filter).dropna().sort_values(ascending=False)
                )
                shorts_ranked = (
                    row_score.where(~row_filter).dropna().sort_values()
                )
                defensive_selected = pd.Series(dtype=float)
                if family == "combined" and float(row_filter.mean()) < 0.45:
                    defensive_selected = (
                        defensive_score.iloc[i]
                        .dropna()
                        .sort_values(ascending=False)
                        .head(1)
                    )

                for top_k in TOP_K:
                    longs = longs_ranked.head(min(top_k, len(longs_ranked)))
                    long_positions = [column_positions[column] for column in longs.index]
                    shorts = shorts_ranked.head(top_k)
                    short_positions = [column_positions[column] for column in shorts.index]
                    for short_cap in SHORT_CAPS:
                        output = arrays[(top_k, short_cap)]
                        if long_positions:
                            output[i, long_positions] = 1.0 / len(long_positions)
                        if short_cap > 0.0 and short_positions:
                            output[i, short_positions] -= short_cap / len(short_positions)
                        if len(defensive_selected):
                            output[i] *= 0.80
                            column = str(defensive_selected.index[0])
                            output[i, defensive_positions[column]] = 0.20

            for top_k in TOP_K:
                for short_cap in SHORT_CAPS:
                    generated[_name(family, lookbacks, top_k, short_cap)] = pd.DataFrame(
                        arrays[(top_k, short_cap)],
                        index=prices.index,
                        columns=prices.columns,
                    )

        risk_off = absolute_all[sectors + countries].mean(axis=1) < 0.40
        arrays = {top_k: np.zeros(prices.shape, dtype=float) for top_k in TOP_K}
        defensive_positions = {
            column: prices.columns.get_loc(column) for column in defensive
        }
        for i, timestamp in enumerate(prices.index):
            if not bool(risk_off.iloc[i]):
                continue
            ranked = (
                defensive_score.iloc[i]
                .dropna()
                .sort_values(ascending=False)
            )
            for top_k in TOP_K:
                selected = ranked.head(min(top_k, len(defensive)))
                positions = [defensive_positions[column] for column in selected.index]
                if positions:
                    arrays[top_k][i, positions] = 1.0 / len(positions)
        for top_k in TOP_K:
            generated[_name("defensive", lookbacks, top_k, 0.0)] = pd.DataFrame(
                arrays[top_k], index=prices.index, columns=prices.columns
            )

    ordered: OrderedDict[str, pd.DataFrame] = OrderedDict()
    for family in FAMILIES:
        for lookbacks in LOOKBACKS:
            for top_k in TOP_K:
                for short_cap in SHORT_CAPS:
                    if family == "defensive" and short_cap > 0.0:
                        continue
                    name = _name(family, lookbacks, top_k, short_cap)
                    ordered[name] = generated[name]
    return dict(ordered)
