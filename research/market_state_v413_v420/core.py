from __future__ import annotations

import hashlib
import json
import math
import sys
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
V9_ROOT = REPO_ROOT / "research" / "active_v9"
sys.path.insert(0, str(V9_ROOT))

from config import Config as V9Config  # noqa: E402
from data import load as load_v9  # noqa: E402
from market import Market  # noqa: E402

PROGRAM = "V413_V420_MARKET_STATE_OBSERVATORY"
SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "LTCUSDT",
    "BCHUSDT", "EOSUSDT", "DOGEUSDT", "LINKUSDT", "DOTUSDT", "TRXUSDT",
    "SOLUSDT",
)
START = "2021-01-01"
DEVELOPMENT_END_EXCLUSIVE = "2024-01-01"
VALIDATION_END_EXCLUSIVE = "2025-01-01"
HOLDOUT_END_EXCLUSIVE = "2026-01-01"
END_EXCLUSIVE = "2026-07-01"
STATE_COUNT = 6
KMEANS_SEED = 413

RAW_FEATURES = (
    "market_trend_63",
    "breadth_positive_63",
    "breadth_above_100d",
    "market_vol_30",
    "downside_vol_ratio_14_90",
    "average_pairwise_corr_60",
    "cross_sectional_dispersion_20",
    "liquidity_level_20",
    "liquidity_breadth_90",
    "taker_buy_pressure_20",
    "funding_level_14",
    "funding_dispersion_14",
    "drawdown_breadth_90",
    "downside_jump_breadth_5",
)
AXIS_COMPONENTS: dict[str, dict[str, float]] = {
    "trend": {
        "market_trend_63": 0.45,
        "breadth_positive_63": 0.30,
        "breadth_above_100d": 0.25,
    },
    "breadth": {
        "breadth_positive_63": 0.50,
        "breadth_above_100d": 0.50,
    },
    "stress": {
        "market_vol_30": 0.25,
        "downside_vol_ratio_14_90": 0.25,
        "average_pairwise_corr_60": 0.15,
        "drawdown_breadth_90": 0.20,
        "downside_jump_breadth_5": 0.15,
    },
    "rotation": {
        "cross_sectional_dispersion_20": 0.55,
        "average_pairwise_corr_60": -0.45,
    },
    "liquidity": {
        "liquidity_level_20": 0.45,
        "liquidity_breadth_90": 0.35,
        "taker_buy_pressure_20": 0.20,
    },
    "leverage": {
        "funding_level_14": 0.55,
        "funding_dispersion_14": 0.45,
    },
}
AXES = tuple(AXIS_COMPONENTS)
SEGMENTS = {
    "development_2021_2023": (START, DEVELOPMENT_END_EXCLUSIVE),
    "validation_2024": (DEVELOPMENT_END_EXCLUSIVE, VALIDATION_END_EXCLUSIVE),
    "holdout_2025": (VALIDATION_END_EXCLUSIVE, HOLDOUT_END_EXCLUSIVE),
    "final_2026h1": (HOLDOUT_END_EXCLUSIVE, END_EXCLUSIVE),
    "full": (START, END_EXCLUSIVE),
}
TECHNICAL_GATES = {
    "development_assignment_days_min": 800,
    "development_feature_row_coverage_min": 0.90,
    "oos_feature_row_coverage_min": 0.85,
    "development_min_state_occupancy": 0.01,
    "development_max_state_occupancy": 0.70,
    "minimum_centroid_distance": 0.60,
    "oos_novelty_rate_max": 0.35,
    "mean_assignment_confidence_min": 0.05,
}


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if np.isfinite(number) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        clean(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def segment_mask(index: pd.DatetimeIndex, start: str, end: str) -> pd.Series:
    return pd.Series(
        (index >= pd.Timestamp(start, tz="UTC"))
        & (index < pd.Timestamp(end, tz="UTC")),
        index=index,
    )


def month_range(first: str, last: str) -> set[str]:
    return {str(value) for value in pd.period_range(first, last, freq="M")}


def data_gate(
    klines: dict[str, pd.DataFrame],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    details: dict[str, Any] = {}
    passed = True
    for symbol in SYMBOLS:
        prices = [r for r in records if r["symbol"] == symbol and r["kind"] == "klines"]
        funding = [r for r in records if r["symbol"] == symbol and r["kind"] == "fundingRate"]
        valid_price = sorted(
            r["month"] for r in prices
            if not r.get("missing") and int(r.get("rows", 0)) > 0
        )
        valid_funding = {
            r["month"] for r in funding
            if not r.get("missing") and int(r.get("rows", 0)) > 0
        }
        if valid_price:
            active = month_range(valid_price[0], valid_price[-1])
            price_share = len(set(valid_price) & active) / max(len(active), 1)
            funding_share = len(valid_funding & active) / max(len(active), 1)
        else:
            active = set()
            price_share = funding_share = 0.0
        dev_months = month_range("2021-01", "2023-12")
        dev_prices = len(set(valid_price) & dev_months)
        latest_required = symbol in {"BTCUSDT", "ETHUSDT"}
        latest_present = "2026-06" in set(valid_price)
        frame = klines.get(symbol, pd.DataFrame())
        symbol_passed = bool(
            symbol in klines
            and price_share >= 0.95
            and funding_share >= 0.90
            and dev_prices >= 34
            and (not latest_required or latest_present)
        )
        details[symbol] = {
            "first_price_month": valid_price[0] if valid_price else None,
            "last_price_month": valid_price[-1] if valid_price else None,
            "active_month_count": len(active),
            "price_active_month_share": price_share,
            "funding_active_month_share": funding_share,
            "development_price_months": dev_prices,
            "latest_required": latest_required,
            "latest_present": latest_present,
            "rows": len(frame),
            "passed": symbol_passed,
        }
        passed = passed and symbol_passed
    return {
        "program": PROGRAM,
        "fixed_universe_size": len(SYMBOLS),
        "survivor_replacement_permitted": False,
        "minimum_price_active_month_share": 0.95,
        "minimum_funding_active_month_share": 0.90,
        "minimum_development_price_months": 34,
        "details": details,
        "passed": bool(passed),
    }


def panel_from_klines(
    klines: dict[str, pd.DataFrame], market: Market, column: str,
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            symbol: klines[symbol][column].reindex(market.index)
            if symbol in klines else pd.Series(index=market.index, dtype=float)
            for symbol in market.symbols
        },
        index=market.index,
    )


def rolling_rms(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    return frame.pow(2).rolling(window, min_periods=window).mean().pow(0.5)


def average_pairwise_correlation(
    frame: pd.DataFrame, window: int, minimum: int,
) -> pd.Series:
    correlations = [
        frame[left].rolling(window, min_periods=minimum).corr(frame[right])
        for left, right in combinations(frame.columns, 2)
    ]
    return pd.concat(correlations, axis=1).mean(axis=1, skipna=True)


def build_features(
    market: Market, klines: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    quote = panel_from_klines(klines, market, "quote_volume")
    taker = panel_from_klines(klines, market, "taker_buy_quote")
    available = market.available

    market_vol_63 = market.market.rolling(63, min_periods=63).std(ddof=1)
    market_trend_63 = market.market.rolling(63, min_periods=63).sum() / (
        market_vol_63.replace(0.0, np.nan) * math.sqrt(63.0)
    )
    return_63 = market.close / market.close.shift(63) - 1.0
    breadth_positive_63 = (
        return_63.gt(0.0).where(return_63.notna() & available).mean(axis=1) * 2.0 - 1.0
    )
    average_100 = market.close.rolling(100, min_periods=100).mean()
    breadth_above_100d = (
        market.close.gt(average_100).where(average_100.notna() & available).mean(axis=1)
        * 2.0 - 1.0
    )

    market_vol_30 = market.market.rolling(30, min_periods=30).std(ddof=1) * math.sqrt(365.0)
    beta = market.beta(90).shift(1)
    residual = market.logret - beta.mul(market.market, axis=0)
    downside = residual.clip(upper=0.0).abs()
    downside_ratio = rolling_rms(downside, 14) / rolling_rms(downside, 90).replace(0.0, np.nan)

    average_pairwise_corr_60 = average_pairwise_correlation(market.logret, 60, 40)
    cross_sectional_dispersion_20 = market.logret.std(axis=1, ddof=1).rolling(20, min_periods=20).mean()

    log_quote = np.log(quote.where(quote > 0.0))
    liquidity_level_20 = log_quote.median(axis=1, skipna=True).rolling(20, min_periods=20).median()
    own_quote_median = quote.rolling(90, min_periods=60).median()
    liquidity_breadth_90 = quote.gt(own_quote_median).where(
        quote.notna() & own_quote_median.notna()
    ).mean(axis=1)
    taker_share = (taker / quote.replace(0.0, np.nan)).clip(0.0, 1.0)
    taker_buy_pressure_20 = taker_share.median(axis=1, skipna=True).rolling(
        20, min_periods=20
    ).mean() - 0.5

    funding_level_14 = market.funding.median(axis=1, skipna=True).rolling(
        14, min_periods=10
    ).mean()
    funding_dispersion_14 = market.funding.std(axis=1, ddof=1).rolling(
        14, min_periods=10
    ).mean()

    rolling_peak_90 = market.close.rolling(90, min_periods=60).max()
    drawdown_90 = market.close / rolling_peak_90 - 1.0
    drawdown_breadth_90 = drawdown_90.lt(-0.10).where(
        drawdown_90.notna() & available
    ).mean(axis=1)

    residual_scale = residual.rolling(60, min_periods=60).std(ddof=1).shift(1)
    downside_jump = residual.lt(-1.5 * residual_scale).where(
        residual.notna() & residual_scale.notna()
    )
    downside_jump_breadth_5 = downside_jump.mean(axis=1).rolling(5, min_periods=3).mean()

    features = pd.DataFrame(
        {
            "market_trend_63": market_trend_63,
            "breadth_positive_63": breadth_positive_63,
            "breadth_above_100d": breadth_above_100d,
            "market_vol_30": market_vol_30,
            "downside_vol_ratio_14_90": downside_ratio.median(axis=1, skipna=True),
            "average_pairwise_corr_60": average_pairwise_corr_60,
            "cross_sectional_dispersion_20": cross_sectional_dispersion_20,
            "liquidity_level_20": liquidity_level_20,
            "liquidity_breadth_90": liquidity_breadth_90,
            "taker_buy_pressure_20": taker_buy_pressure_20,
            "funding_level_14": funding_level_14,
            "funding_dispersion_14": funding_dispersion_14,
            "drawdown_breadth_90": drawdown_breadth_90,
            "downside_jump_breadth_5": downside_jump_breadth_5,
        },
        index=market.index,
    ).shift(1)
    mask = segment_mask(features.index, START, END_EXCLUSIVE)
    return features.loc[mask, RAW_FEATURES]


def fit_robust_scaler(
    frame: pd.DataFrame, fit_mask: pd.Series,
) -> dict[str, dict[str, float]]:
    stats: dict[str, dict[str, float]] = {}
    fit = frame.loc[fit_mask]
    for column in frame.columns:
        values = pd.to_numeric(fit[column], errors="coerce").dropna()
        if values.empty:
            raise ValueError(f"no development values for {column}")
        median = float(values.median())
        q25 = float(values.quantile(0.25))
        q75 = float(values.quantile(0.75))
        scale = max(q75 - q25, float(values.std(ddof=1)) * 0.25, 1e-12)
        stats[column] = {"median": median, "q25": q25, "q75": q75, "scale": scale}
    return stats


def apply_robust_scaler(
    frame: pd.DataFrame, stats: dict[str, dict[str, float]],
) -> pd.DataFrame:
    output = pd.DataFrame(index=frame.index, columns=frame.columns, dtype=float)
    for column in frame.columns:
        output[column] = ((frame[column] - stats[column]["median"]) / stats[column]["scale"]).clip(-4.0, 4.0)
    return output


def weighted_axis(
    standardized: pd.DataFrame, components: dict[str, float],
) -> pd.Series:
    numerator = pd.Series(0.0, index=standardized.index)
    denominator = pd.Series(0.0, index=standardized.index)
    for feature, weight in components.items():
        values = pd.to_numeric(standardized[feature], errors="coerce")
        valid = values.notna()
        numerator = numerator.add(values.fillna(0.0) * weight, fill_value=0.0)
        denominator = denominator.add(valid.astype(float) * abs(weight), fill_value=0.0)
    return (numerator / denominator.replace(0.0, np.nan)).where(denominator >= 0.60)


def build_axes(standardized_features: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            axis: weighted_axis(standardized_features, components)
            for axis, components in AXIS_COMPONENTS.items()
        },
        index=standardized_features.index,
    )


def write_manifest(root: Path) -> None:
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative = str(path.relative_to(root))
        if relative in {"MANIFEST.json", "run.log"}:
            continue
        files[relative] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
    write_json(root / "MANIFEST.json", {"program": PROGRAM, "files": files})
