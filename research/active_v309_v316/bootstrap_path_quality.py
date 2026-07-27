#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

root = Path(__file__).resolve().parents[2]
source = root / "research" / "active_v293_v300" / "run_research.py"
target = Path(__file__).resolve().parent / "run_research.py"
text = source.read_text()

settings = '''CANDIDATE = "ACTIVE_V309_CRYPTO_RESIDUAL_PATH_QUALITY"
FAMILIES = (
    "high_absolute_residual_efficiency",
    "low_residual_sign_change_rate",
    "high_residual_variance_ratio",
    "reversed_low_efficiency_control",
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
'''
text, count = re.subn(
    r'CANDIDATE = "ACTIVE_V293_CRYPTO_RESIDUAL_REVERSAL".*?POST_SELECTION_GATES = \{.*?\n\}\n',
    settings,
    text,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit(f"settings replacement count={count}")

scores = '''def score_frame(market: Any, family: str, lookback: int) -> pd.DataFrame:
    residual = market.logret - market.beta(60).shift(1).mul(market.market, axis=0)
    rolling_sum = residual.rolling(lookback, min_periods=lookback).sum()
    path_length = residual.abs().rolling(lookback, min_periods=lookback).sum()
    efficiency = rolling_sum.abs() / path_length.replace(0.0, np.nan)

    previous = residual.shift(1)
    valid_pair = residual.notna() & previous.notna()
    sign_change = (np.sign(residual) != np.sign(previous)).where(valid_pair)
    sign_change_rate = sign_change.astype(float).rolling(
        lookback, min_periods=lookback
    ).mean()

    horizon = 5
    aggregate = residual.rolling(horizon, min_periods=horizon).sum()
    numerator = aggregate.rolling(lookback, min_periods=lookback).var(ddof=1)
    denominator = horizon * residual.rolling(
        lookback, min_periods=lookback
    ).var(ddof=1)
    variance_ratio = numerator / denominator.replace(0.0, np.nan)

    if family == "high_absolute_residual_efficiency":
        score = efficiency
    elif family == "low_residual_sign_change_rate":
        score = -sign_change_rate
    elif family == "high_residual_variance_ratio":
        score = variance_ratio
    elif family == "reversed_low_efficiency_control":
        score = -efficiency
    else:
        raise ValueError(family)
    return v.winsorize_cross_section(score)
'''
text, count = re.subn(
    r'def residualize\(.*?\n\ndef configure_engine\(\) -> None:',
    scores + '\n\ndef configure_engine() -> None:',
    text,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit(f"score replacement count={count}")

replacements = {
    '            (3, 5, 10),\n            (2, 3),\n            (1, 3, 7),\n':
        '            (60, 120, 240),\n            (3, 4),\n            (14, 28, 56),\n',
    '    policy = v.Policy("close_residual_reversal", 5, 2, 3, "dollar")\n':
        '    policy = v.Policy("high_absolute_residual_efficiency", 60, 3, 14, "dollar")\n',
    '    assert diagnostics["rebalance_events"] >= 60\n':
        '    assert diagnostics["rebalance_events"] >= 18\n',
    '    print("V293-V300 causal residual-reversal self-test passed")\n':
        '    print("V309-V316 causal residual path-quality self-test passed")\n',
    '        "Residual-reversal ranking is not an issuer-quality or fundamental-value claim.",\n':
        '        "Residual path-quality ranking is not an issuer-quality or fundamental-value claim.",\n',
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise SystemExit(f"replacement missing: {old[:50]!r}")
    text = text.replace(old, new, 1)

text = text.replace("V293", "V309").replace("V300", "V316")
text = text.replace("crypto residual reversal", "crypto residual path quality")
text = text.replace("residual-reversal", "residual-path-quality")
text = text.replace("v269_residual_reversal_base", "v269_path_quality_base")
target.write_text(text)
print(f"materialized {target} ({len(text)} bytes)")
