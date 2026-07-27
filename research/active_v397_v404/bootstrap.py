#!/usr/bin/env python3
from pathlib import Path

root = Path(__file__).resolve().parents[2]
source = root / "research" / "active_v341_v348" / "run_research.py"
target = Path(__file__).resolve().parent / "run_research.py"
text = source.read_text()


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"replacement count {count} for {old[:80]!r}")
    text = text.replace(old, new, 1)


replace_once(
    '''FAMILIES = (
    "low_residual_vol_ratio",
    "low_downside_vol_ratio",
    "low_range_vol_ratio",
    "reversed_vol_expansion_control",
)
PROMOTABLE_FAMILIES = FAMILIES[:-1]
WINDOWS = {14: (14, 90), 30: (30, 180), 60: (60, 240)}
''',
    '''FAMILIES = (
    "low_residual_dispersion_beta",
    "stable_residual_dispersion_beta",
    "low_high_dispersion_participation",
    "reversed_high_dispersion_beta_control",
)
PROMOTABLE_FAMILIES = FAMILIES[:-1]
WINDOWS = {60: (60, 60), 120: (120, 120), 240: (240, 240)}
''',
)

old_score = '''def rolling_rms(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    return frame.pow(2).rolling(window, min_periods=window).mean().pow(0.5)


def score_frame(market: Any, family: str, lookback_key: int) -> pd.DataFrame:
    short_window, long_window = WINDOWS[int(lookback_key)]
    beta = market.beta(90).shift(1)
    residual = market.logret - beta.mul(market.market, axis=0)
    short_residual_vol = residual.rolling(
        short_window, min_periods=short_window
    ).std(ddof=1)
    long_residual_vol = residual.rolling(
        long_window, min_periods=long_window
    ).std(ddof=1)
    residual_ratio = short_residual_vol / long_residual_vol.replace(0.0, np.nan)

    if family == "low_residual_vol_ratio":
        score = -residual_ratio
    elif family == "reversed_vol_expansion_control":
        score = residual_ratio
    elif family == "low_downside_vol_ratio":
        downside = residual.clip(upper=0.0).abs()
        short_downside = rolling_rms(downside, short_window)
        long_downside = rolling_rms(downside, long_window)
        score = -(short_downside / long_downside.replace(0.0, np.nan))
    elif family == "low_range_vol_ratio":
        valid_high = market.high.where(market.high > 0.0)
        valid_low = market.low.where(market.low > 0.0)
        log_range = np.log(valid_high / valid_low).replace([np.inf, -np.inf], np.nan)
        short_range = rolling_rms(log_range, short_window)
        long_range = rolling_rms(log_range, long_window)
        score = -(short_range / long_range.replace(0.0, np.nan))
    else:
        raise ValueError(family)
    return v.winsorize_cross_section(score)
'''

new_score = '''def dispersion_beta(
    absolute_residual: pd.DataFrame,
    dispersion: pd.Series,
    window: int,
) -> pd.DataFrame:
    variance = dispersion.rolling(window, min_periods=window).var()
    output = pd.DataFrame(
        index=absolute_residual.index,
        columns=absolute_residual.columns,
        dtype=float,
    )
    for symbol in absolute_residual.columns:
        covariance = absolute_residual[symbol].rolling(
            window, min_periods=window
        ).cov(dispersion)
        output[symbol] = covariance / variance.replace(0.0, np.nan)
    return output


def score_frame(market: Any, family: str, lookback_key: int) -> pd.DataFrame:
    lookback = int(lookback_key)
    beta = market.beta(90).shift(1)
    residual = market.logret - beta.mul(market.market, axis=0)
    absolute_residual = residual.abs()
    dispersion = residual.std(axis=1, ddof=1)

    if family in {
        "low_residual_dispersion_beta",
        "reversed_high_dispersion_beta_control",
    }:
        sensitivity = dispersion_beta(absolute_residual, dispersion, lookback)
        score = (
            -sensitivity
            if family == "low_residual_dispersion_beta"
            else sensitivity
        )
    elif family == "stable_residual_dispersion_beta":
        inner = dispersion_beta(absolute_residual, dispersion, 30)
        score = -inner.rolling(lookback, min_periods=lookback).std(ddof=1)
    elif family == "low_high_dispersion_participation":
        threshold = dispersion.rolling(
            lookback, min_periods=lookback
        ).quantile(0.80)
        high_mask = dispersion >= threshold
        minimum_high = max(8, lookback // 8)
        high_mean = absolute_residual.where(high_mask, axis=0).rolling(
            lookback, min_periods=minimum_high
        ).mean()
        overall_mean = absolute_residual.rolling(
            lookback, min_periods=lookback
        ).mean()
        participation = high_mean / overall_mean.replace(0.0, np.nan)
        score = -participation
    else:
        raise ValueError(family)
    return v.winsorize_cross_section(score)
'''
replace_once(old_score, new_score)

replace_once('CANDIDATE = "ACTIVE_V341_CRYPTO_VOLATILITY_TERM_STRUCTURE"', 'CANDIDATE = "ACTIVE_V397_CRYPTO_DISPERSION_SENSITIVITY"')
replace_once('            (7, 14, 28),', '            (14, 28, 56),')
replace_once('policy = v.Policy("low_residual_vol_ratio", 30, 3, 14, "dollar")', 'policy = v.Policy("low_residual_dispersion_beta", 60, 3, 14, "dollar")')
replace_once('print("V341-V348 causal volatility-term-structure self-test passed")', 'print("V397-V404 causal dispersion-sensitivity self-test passed")')
replace_once('proof["window_pairs"] = {str(key): list(value) for key, value in WINDOWS.items()}', 'proof["lookbacks"] = list(WINDOWS)')
if text.count('V341_V348_DESIGN.json') != 2:
    raise SystemExit('unexpected design-path count')
text = text.replace('V341_V348_DESIGN.json', 'V397_V404_DESIGN.json')
if text.count('V341_FIXED_UNIVERSE_DATA_COVERAGE') != 2:
    raise SystemExit('unexpected coverage-name count')
text = text.replace('V341_FIXED_UNIVERSE_DATA_COVERAGE', 'V397_FIXED_UNIVERSE_DATA_COVERAGE')
text = text.replace('V341–V348 — volatility term structure', 'V397–V404 — dispersion sensitivity')
replace_once('Volatility compression is a statistical ranking, not an issuer-quality claim.', 'Dispersion sensitivity is a statistical market-linkage ranking, not an issuer-quality claim.')

target.write_text(text)
print(f"materialized {target} ({len(text)} bytes)")
