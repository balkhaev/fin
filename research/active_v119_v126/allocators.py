from __future__ import annotations

from dataclasses import dataclass
import itertools

import numpy as np
import pandas as pd

from config import WEIGHT_GRID

COLUMNS = ("atlas", "crisis", "rotation")


@dataclass(frozen=True)
class AllocationSpec:
    family: str
    lookback: int
    rebalance: int
    transfer_cost_bps: float
    parameter_a: float = 0.0
    parameter_b: float = 0.0
    leverage_cap: float = 1.0
    target_vol: float = 0.20
    base_weights: tuple[float, float, float] = (0.75, 0.125, 0.125)


def spec_id(spec: AllocationSpec) -> str:
    return (
        f"{spec.family}__l{spec.lookback}__r{spec.rebalance}"
        f"__tc{int(spec.transfer_cost_bps)}__a{int(spec.parameter_a * 100)}"
        f"__b{int(spec.parameter_b * 100)}__lev{int(spec.leverage_cap * 100)}"
        f"__tv{int(spec.target_vol * 100)}"
        f"__w{'-'.join(str(int(x * 100)) for x in spec.base_weights)}"
    )


def candidate_specs() -> list[AllocationSpec]:
    specs: list[AllocationSpec] = []
    for weights in WEIGHT_GRID:
        for rebalance, transfer in itertools.product((20, 40), (10.0, 25.0)):
            specs.append(AllocationSpec("static", 63, rebalance, transfer, base_weights=weights))
    for lookback, rebalance, transfer in itertools.product((63, 126), (20, 40), (10.0, 25.0)):
        specs.append(AllocationSpec("invvol", lookback, rebalance, transfer))
        specs.append(AllocationSpec("erc", lookback, rebalance, transfer))
        for shrinkage in (0.50, 0.75):
            specs.append(AllocationSpec("minvar", lookback, rebalance, transfer, parameter_a=shrinkage))
        for threshold in (0.35, 0.50):
            specs.append(AllocationSpec("tail_gate", lookback, rebalance, transfer, parameter_a=threshold))
        for drawdown in (-0.05, -0.10):
            specs.append(AllocationSpec("crisis_budget", lookback, rebalance, transfer, parameter_a=drawdown))
        for corr, leverage, target_vol in itertools.product((0.30, 0.45), (1.10, 1.15), (0.18, 0.20)):
            specs.append(
                AllocationSpec(
                    "lowcorr_leverage",
                    lookback,
                    rebalance,
                    transfer,
                    parameter_a=corr,
                    leverage_cap=leverage,
                    target_vol=target_vol,
                )
            )
    return specs


def bounded_normalize(weights: pd.DataFrame) -> pd.DataFrame:
    output = weights.copy().fillna(0.0).clip(lower=0.0)
    floors = pd.Series((0.50, 0.05, 0.05), index=COLUMNS)
    caps = pd.Series((0.90, 0.30, 0.30), index=COLUMNS)
    output = output.clip(lower=floors, upper=caps, axis=1)
    return output.div(output.sum(axis=1), axis=0).fillna(pd.Series((0.75, 0.125, 0.125), index=COLUMNS))


def inverse_vol(returns: pd.DataFrame, lookback: int) -> pd.DataFrame:
    vol = returns.rolling(lookback, min_periods=max(30, lookback // 2)).std()
    raw = 1.0 / vol.replace(0.0, np.nan)
    return bounded_normalize(raw)


def minvar_grid(returns: pd.DataFrame, lookback: int, shrinkage: float) -> pd.DataFrame:
    grid = np.array(WEIGHT_GRID, dtype=float)
    output = np.tile(np.array((0.75, 0.125, 0.125)), (len(returns), 1))
    values = returns.to_numpy(float)
    for i in range(lookback, len(returns)):
        window = values[i - lookback : i]
        window = window[np.isfinite(window).all(axis=1)]
        if len(window) < max(30, lookback // 2):
            continue
        covariance = np.cov(window, rowvar=False)
        diagonal = np.diag(np.diag(covariance))
        covariance = (1.0 - shrinkage) * covariance + shrinkage * diagonal
        variances = np.einsum("ij,jk,ik->i", grid, covariance, grid)
        output[i] = grid[int(np.nanargmin(variances))]
    return pd.DataFrame(output, index=returns.index, columns=COLUMNS)


def erc_weights(returns: pd.DataFrame, lookback: int) -> pd.DataFrame:
    output = np.tile(np.array((0.75, 0.125, 0.125)), (len(returns), 1))
    values = returns.to_numpy(float)
    for i in range(lookback, len(returns)):
        window = values[i - lookback : i]
        window = window[np.isfinite(window).all(axis=1)]
        if len(window) < max(30, lookback // 2):
            continue
        covariance = np.cov(window, rowvar=False)
        weights = np.array((0.75, 0.125, 0.125), dtype=float)
        for _ in range(100):
            portfolio_vol = float(np.sqrt(max(weights @ covariance @ weights, 1e-16)))
            marginal = covariance @ weights / portfolio_vol
            contribution = weights * marginal
            target = portfolio_vol / 3.0
            update = target / np.maximum(contribution, 1e-12)
            weights *= np.sqrt(np.clip(update, 0.25, 4.0))
            weights = np.clip(weights, (0.50, 0.05, 0.05), (0.90, 0.30, 0.30))
            weights /= weights.sum()
        output[i] = weights
    return pd.DataFrame(output, index=returns.index, columns=COLUMNS)


def downside_correlation(returns: pd.DataFrame, lookback: int) -> pd.Series:
    output = pd.Series(1.0, index=returns.index)
    for i in range(lookback, len(returns)):
        window = returns.iloc[i - lookback : i]
        downside = window[(window.atlas < 0.0) | (window.crisis < 0.0) | (window.rotation < 0.0)]
        if len(downside) < 20:
            continue
        matrix = downside.corr().to_numpy()
        values = matrix[np.triu_indices(3, 1)]
        output.iloc[i] = float(np.nanmean(values)) if np.isfinite(values).any() else 1.0
    return output


def average_correlation(returns: pd.DataFrame, lookback: int) -> pd.Series:
    output = pd.Series(1.0, index=returns.index)
    rolling = returns.rolling(lookback, min_periods=max(30, lookback // 2)).corr()
    for timestamp in returns.index:
        try:
            matrix = rolling.loc[timestamp].to_numpy(float)
            values = matrix[np.triu_indices(3, 1)]
            output.loc[timestamp] = float(np.nanmean(values)) if np.isfinite(values).any() else 1.0
        except Exception:
            pass
    return output


def atlas_drawdown(atlas_equity: pd.Series) -> pd.Series:
    return atlas_equity / atlas_equity.cummax() - 1.0


def build_allocation(
    returns: pd.DataFrame,
    atlas_equity: pd.Series,
    spec: AllocationSpec,
) -> tuple[pd.DataFrame, pd.Series]:
    if spec.family == "static":
        weights = pd.DataFrame(
            np.tile(np.asarray(spec.base_weights), (len(returns), 1)),
            index=returns.index,
            columns=COLUMNS,
        )
    elif spec.family == "invvol":
        weights = inverse_vol(returns, spec.lookback)
    elif spec.family == "erc":
        weights = erc_weights(returns, spec.lookback)
    elif spec.family == "minvar":
        weights = minvar_grid(returns, spec.lookback, spec.parameter_a)
    elif spec.family in ("tail_gate", "crisis_budget", "lowcorr_leverage"):
        weights = inverse_vol(returns, spec.lookback)
    else:
        raise ValueError(spec.family)

    scale = pd.Series(1.0, index=returns.index)
    if spec.family == "tail_gate":
        correlation = downside_correlation(returns, spec.lookback).shift(1).fillna(1.0)
        gated = correlation > spec.parameter_a
        weights.loc[gated, ["crisis", "rotation"]] *= 0.50
        weights.loc[gated, "atlas"] = 1.0 - weights.loc[gated, ["crisis", "rotation"]].sum(axis=1)
    elif spec.family == "crisis_budget":
        drawdown = atlas_drawdown(atlas_equity).shift(1).fillna(0.0)
        crisis = drawdown <= spec.parameter_a
        weights.loc[crisis, ["crisis", "rotation"]] *= 1.50
        weights.loc[crisis, "atlas"] = 1.0 - weights.loc[crisis, ["crisis", "rotation"]].sum(axis=1)
        weights = bounded_normalize(weights)
    elif spec.family == "lowcorr_leverage":
        raw_return = (weights.shift(1) * returns).sum(axis=1).fillna(0.0)
        realised = raw_return.rolling(spec.lookback, min_periods=max(30, spec.lookback // 2)).std() * np.sqrt(252.0)
        requested = (spec.target_vol / realised.replace(0.0, np.nan)).shift(1).clip(0.75, spec.leverage_cap).fillna(1.0)
        correlation = average_correlation(returns, spec.lookback).shift(1).fillna(1.0)
        scale = requested.where(correlation < spec.parameter_a, np.minimum(requested, 1.0))

    weights = bounded_normalize(weights)
    return weights, scale
