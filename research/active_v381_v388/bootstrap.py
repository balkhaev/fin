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
    "low_residual_sign_entropy",
    "low_residual_state_entropy",
    "low_residual_transition_entropy",
    "reversed_high_entropy_control",
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

new_score = '''def normalized_state_entropy(
    states: pd.DataFrame,
    window: int,
    categories: tuple[float, ...],
) -> pd.DataFrame:
    entropy = pd.DataFrame(0.0, index=states.index, columns=states.columns)
    for category in categories:
        indicator = states.eq(category).where(states.notna()).astype(float)
        probability = indicator.rolling(window, min_periods=window).mean()
        term = -(probability * np.log(probability.where(probability > 0.0)))
        entropy = entropy.add(term.fillna(0.0), fill_value=0.0)
    complete = (
        states.notna().astype(float).rolling(window, min_periods=window).sum()
        >= float(window)
    )
    return (entropy / np.log(float(len(categories)))).where(complete)


def score_frame(market: Any, family: str, lookback_key: int) -> pd.DataFrame:
    lookback = int(lookback_key)
    beta = market.beta(90).shift(1)
    residual = market.logret - beta.mul(market.market, axis=0)

    sign_state = residual.gt(0.0).astype(float).where(residual.notna())
    sign_entropy = normalized_state_entropy(sign_state, lookback, (0.0, 1.0))

    scale = residual.rolling(60, min_periods=60).std(ddof=1).shift(1)
    standardized = residual / scale.replace(0.0, np.nan)
    state = pd.DataFrame(np.nan, index=residual.index, columns=residual.columns)
    state = state.mask(standardized < -0.5, -1.0)
    state = state.mask(standardized.abs() <= 0.5, 0.0)
    state = state.mask(standardized > 0.5, 1.0)
    state_entropy = normalized_state_entropy(state, lookback, (-1.0, 0.0, 1.0))

    previous_sign = sign_state.shift(1)
    transition = (2.0 * previous_sign + sign_state).where(
        previous_sign.notna() & sign_state.notna()
    )
    transition_entropy = normalized_state_entropy(
        transition, lookback, (0.0, 1.0, 2.0, 3.0)
    )

    if family == "low_residual_sign_entropy":
        score = -sign_entropy
    elif family == "low_residual_state_entropy":
        score = -state_entropy
    elif family == "low_residual_transition_entropy":
        score = -transition_entropy
    elif family == "reversed_high_entropy_control":
        score = state_entropy
    else:
        raise ValueError(family)
    return v.winsorize_cross_section(score)
'''
replace_once(old_score, new_score)

replace_once('CANDIDATE = "ACTIVE_V341_CRYPTO_VOLATILITY_TERM_STRUCTURE"', 'CANDIDATE = "ACTIVE_V381_CRYPTO_RESIDUAL_ENTROPY"')
replace_once('            (7, 14, 28),', '            (14, 28, 56),')
replace_once('policy = v.Policy("low_residual_vol_ratio", 30, 3, 14, "dollar")', 'policy = v.Policy("low_residual_sign_entropy", 60, 3, 14, "dollar")')
replace_once('print("V341-V348 causal volatility-term-structure self-test passed")', 'print("V381-V388 causal residual-entropy self-test passed")')
replace_once('proof["window_pairs"] = {str(key): list(value) for key, value in WINDOWS.items()}', 'proof["lookbacks"] = list(WINDOWS)')
if text.count('V341_V348_DESIGN.json') != 2:
    raise SystemExit('unexpected design-path count')
text = text.replace('V341_V348_DESIGN.json', 'V381_V388_DESIGN.json')
if text.count('V341_FIXED_UNIVERSE_DATA_COVERAGE') != 2:
    raise SystemExit('unexpected coverage-name count')
text = text.replace('V341_FIXED_UNIVERSE_DATA_COVERAGE', 'V381_FIXED_UNIVERSE_DATA_COVERAGE')
text = text.replace('V341–V348 — volatility term structure', 'V381–V388 — residual entropy')
replace_once('Volatility compression is a statistical ranking, not an issuer-quality claim.', 'Residual entropy is a statistical path-complexity ranking, not an issuer-quality claim.')

target.write_text(text)
print(f"materialized {target} ({len(text)} bytes)")
