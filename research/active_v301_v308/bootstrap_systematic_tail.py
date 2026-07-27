#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "research" / "active_v293_v300" / "run_research.py"
TARGET = Path(__file__).resolve().parent / "run_research.py"
text = SOURCE.read_text()


def replace_once(old: str, new: str, label: str) -> None:
    global text
    if text.count(old) != 1:
        raise SystemExit(f"{label}: expected exactly one match, got {text.count(old)}")
    text = text.replace(old, new, 1)


replace_once(
    '''CANDIDATE = "ACTIVE_V293_CRYPTO_RESIDUAL_REVERSAL"
FAMILIES = (
    "close_residual_reversal",
    "overnight_gap_residual_reversal",
    "intraday_residual_reversal",
    "residual_continuation_control",
)
PROMOTABLE_FAMILIES = FAMILIES[:-1]
TARGET_GROSS = 0.40
MAX_REALIZED_GROSS = 0.70
DEVELOPMENT_GATES = {
    "cagr_min": 0.05,
    "sharpe_min": 1.00,
    "max_drawdown_min": -0.12,
    "rebalance_events_min": 60,
    "annual_turnover_max": 35.0,
    "max_realized_gross": MAX_REALIZED_GROSS,
    "all_development_years_positive": True,
    "net_long_leg_pnl_positive": True,
    "net_short_leg_pnl_positive": True,
    "symbols_traded_min": 10,
    "top_positive_asset_pnl_share_max": 0.35,
}
POST_SELECTION_GATES = {
    "validation_return_positive": True,
    "holdout_return_positive": True,
    "final_return_positive": True,
    "full_cagr_min": 0.06,
    "full_sharpe_min": 0.80,
    "full_max_drawdown_min": -0.15,
    "severe_full_cagr_positive": True,
    "extreme_full_cagr_positive": True,
    "latency_full_cagr_positive": True,
    "worst_calendar_year_min": -0.10,
    "full_long_leg_pnl_positive": True,
    "full_short_leg_pnl_positive": True,
    "top_positive_asset_pnl_share_max": 0.35,
    "forced_exit_count_max": 4,
}
''',
    '''CANDIDATE = "ACTIVE_V301_CRYPTO_SYSTEMATIC_TAIL"
FAMILIES = (
    "low_systematic_coskewness",
    "low_crash_beta",
    "high_up_down_beta_spread",
    "reversed_high_coskewness_control",
)
PROMOTABLE_FAMILIES = FAMILIES[:-1]
TARGET_GROSS = 0.40
MAX_REALIZED_GROSS = 0.70
DEVELOPMENT_GATES = {
    "cagr_min": 0.05,
    "sharpe_min": 1.00,
    "max_drawdown_min": -0.15,
    "rebalance_events_min": 18,
    "annual_turnover_max": 15.0,
    "max_realized_gross": MAX_REALIZED_GROSS,
    "all_development_years_positive": True,
    "net_long_leg_pnl_positive": True,
    "net_short_leg_pnl_positive": True,
    "symbols_traded_min": 10,
    "top_positive_asset_pnl_share_max": 0.35,
}
POST_SELECTION_GATES = {
    "validation_return_positive": True,
    "holdout_return_positive": True,
    "final_return_positive": True,
    "full_cagr_min": 0.06,
    "full_sharpe_min": 0.80,
    "full_max_drawdown_min": -0.15,
    "severe_full_cagr_positive": True,
    "extreme_full_cagr_positive": True,
    "latency_full_cagr_positive": True,
    "worst_calendar_year_min": -0.10,
    "full_long_leg_pnl_positive": True,
    "full_short_leg_pnl_positive": True,
    "top_positive_asset_pnl_share_max": 0.35,
    "forced_exit_count_max": 4,
}
''',
    "candidate and gates",
)

replace_once(
    '''def residualize(frame: pd.DataFrame, market_frame: pd.Series, window: int = 60) -> pd.DataFrame:
    variance = market_frame.rolling(window, min_periods=window).var().shift(1)
    output = pd.DataFrame(index=frame.index, columns=frame.columns, dtype=float)
    for symbol in frame.columns:
        beta = (
            frame[symbol]
            .rolling(window, min_periods=window)
            .cov(market_frame)
            .shift(1)
            / variance.replace(0.0, np.nan)
        )
        output[symbol] = frame[symbol] - beta * market_frame
    return output


def score_frame(market: Any, family: str, lookback: int) -> pd.DataFrame:
    close_residual = market.logret - market.beta(60).shift(1).mul(market.market, axis=0)
    if family in {"close_residual_reversal", "residual_continuation_control"}:
        aggregate = close_residual.rolling(lookback, min_periods=lookback).sum()
        score = -aggregate if family == "close_residual_reversal" else aggregate
    elif family == "overnight_gap_residual_reversal":
        overnight = np.log(market.open / market.close.shift(1))
        overnight_market = overnight[["BTCUSDT", "ETHUSDT"]].mean(axis=1)
        residual = residualize(overnight, overnight_market)
        score = -residual.rolling(lookback, min_periods=lookback).sum()
    elif family == "intraday_residual_reversal":
        intraday = np.log(market.close / market.open)
        intraday_market = intraday[["BTCUSDT", "ETHUSDT"]].mean(axis=1)
        residual = residualize(intraday, intraday_market)
        score = -residual.rolling(lookback, min_periods=lookback).sum()
    else:
        raise ValueError(family)
    return v.winsorize_cross_section(score)
''',
    '''def conditional_beta(
    frame: pd.DataFrame,
    market_return: pd.Series,
    mask: pd.Series,
    lookback: int,
) -> pd.DataFrame:
    minimum = max(20, lookback // 5)
    masked_market = market_return.where(mask)
    variance = masked_market.rolling(lookback, min_periods=minimum).var()
    output = pd.DataFrame(index=frame.index, columns=frame.columns, dtype=float)
    for symbol in frame.columns:
        covariance = frame[symbol].where(mask).rolling(
            lookback, min_periods=minimum
        ).cov(masked_market)
        output[symbol] = covariance / variance.replace(0.0, np.nan)
    return output


def systematic_coskewness(market: Any, lookback: int) -> pd.DataFrame:
    market_return = market.market
    market_mean = market_return.rolling(lookback, min_periods=lookback).mean()
    market_center = market_return - market_mean
    market_std = market_return.rolling(lookback, min_periods=lookback).std(ddof=1)
    output = pd.DataFrame(index=market.index, columns=market.symbols, dtype=float)
    for symbol in market.symbols:
        asset = market.logret[symbol]
        asset_mean = asset.rolling(lookback, min_periods=lookback).mean()
        asset_center = asset - asset_mean
        asset_std = asset.rolling(lookback, min_periods=lookback).std(ddof=1)
        numerator = (asset_center * market_center.pow(2)).rolling(
            lookback, min_periods=lookback
        ).mean()
        denominator = asset_std * market_std.pow(2)
        output[symbol] = numerator / denominator.replace(0.0, np.nan)
    return output


def score_frame(market: Any, family: str, lookback: int) -> pd.DataFrame:
    market_return = market.market
    coskewness = systematic_coskewness(market, lookback)
    if family == "low_systematic_coskewness":
        score = -coskewness
    elif family == "reversed_high_coskewness_control":
        score = coskewness
    elif family == "low_crash_beta":
        threshold = market_return.rolling(lookback, min_periods=lookback).quantile(0.20)
        score = -conditional_beta(
            market.logret, market_return, market_return <= threshold, lookback
        )
    elif family == "high_up_down_beta_spread":
        upside = conditional_beta(
            market.logret, market_return, market_return > 0.0, lookback
        )
        downside = conditional_beta(
            market.logret, market_return, market_return < 0.0, lookback
        )
        score = upside - downside
    else:
        raise ValueError(family)
    return v.winsorize_cross_section(score)
''',
    "systematic tail score functions",
)

replace_once(
    '''            (3, 5, 10),
            (2, 3),
            (1, 3, 7),
''',
    '''            (90, 180, 365),
            (3, 4),
            (14, 28, 56),
''',
    "policy grid",
)

replace_once(
    '''    policy = v.Policy("close_residual_reversal", 5, 2, 3, "dollar")
''',
    '''    policy = v.Policy("low_systematic_coskewness", 90, 3, 14, "dollar")
''',
    "self-test policy",
)
replace_once(
    '''    assert diagnostics["rebalance_events"] >= 60
''',
    '''    assert diagnostics["rebalance_events"] >= 18
''',
    "self-test rebalance floor",
)
replace_once(
    '''    print("V293-V300 causal residual-reversal self-test passed")
''',
    '''    print("V301-V308 causal systematic-tail self-test passed")
''',
    "self-test message",
)
replace_once(
    '''        "Residual-reversal ranking is not an issuer-quality or fundamental-value claim.",
''',
    '''        "Systematic-tail ranking is not an issuer-quality or fundamental-value claim.",
''',
    "limitation label",
)

text = text.replace("V293", "V301").replace("V300", "V308")
text = text.replace("crypto residual reversal", "crypto systematic tail")
text = text.replace("residual-reversal", "systematic-tail")
text = text.replace("v269_residual_reversal_base", "v269_systematic_tail_base")

TARGET.write_text(text)
print(f"materialized {TARGET} ({len(text)} bytes)")
