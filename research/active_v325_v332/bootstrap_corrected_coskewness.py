#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

root = Path(__file__).resolve().parents[2]
source = root / "research" / "active_v301_v308" / "run_research.py"
target = Path(__file__).resolve().parent / "run_research.py"
text = source.read_text()

text = text.replace(
    'CANDIDATE = "ACTIVE_V301_CRYPTO_SYSTEMATIC_TAIL"',
    'CANDIDATE = "ACTIVE_V325_CORRECTED_SYSTEMATIC_TAIL"',
    1,
)

corrected = '''def systematic_coskewness(market: Any, lookback: int) -> pd.DataFrame:
    market_return = market.market
    mu_y = market_return.rolling(lookback, min_periods=lookback).mean()
    e_y2 = market_return.pow(2).rolling(lookback, min_periods=lookback).mean()
    sigma_y = market_return.rolling(lookback, min_periods=lookback).std(ddof=1)
    output = pd.DataFrame(index=market.index, columns=market.symbols, dtype=float)
    for symbol in market.symbols:
        asset = market.logret[symbol]
        mu_x = asset.rolling(lookback, min_periods=lookback).mean()
        e_xy = (asset * market_return).rolling(
            lookback, min_periods=lookback
        ).mean()
        e_xy2 = (asset * market_return.pow(2)).rolling(
            lookback, min_periods=lookback
        ).mean()
        sigma_x = asset.rolling(lookback, min_periods=lookback).std(ddof=1)
        numerator = e_xy2 - 2.0 * mu_y * e_xy - mu_x * e_y2 + 2.0 * mu_x * mu_y.pow(2)
        denominator = sigma_x * sigma_y.pow(2)
        output[symbol] = numerator / denominator.replace(0.0, np.nan)
    return output
'''
text, count = re.subn(
    r'def systematic_coskewness\(.*?\n\ndef score_frame\(',
    corrected + '\n\ndef score_frame(',
    text,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit(f"coskewness replacement count={count}")

needle = '''    market = synthetic_market()
    policy = v.Policy("low_systematic_coskewness", 90, 3, 14, "dollar")
'''
insert = '''    market = synthetic_market()
    corrected_score = systematic_coskewness(market, 90)
    first_valid = np.flatnonzero(corrected_score.notna().any(axis=1).to_numpy())
    assert len(first_valid) and first_valid[0] <= 95
    position = 220
    symbol = market.symbols[0]
    x = market.logret[symbol].iloc[position - 89 : position + 1]
    y = market.market.iloc[position - 89 : position + 1]
    expected = float(((x - x.mean()) * (y - y.mean()).pow(2)).mean() / (x.std(ddof=1) * y.std(ddof=1) ** 2))
    actual = float(corrected_score[symbol].iloc[position])
    assert np.isclose(actual, expected, rtol=1e-10, atol=1e-10)
    policy = v.Policy("low_systematic_coskewness", 90, 3, 14, "dollar")
'''
if text.count(needle) != 1:
    raise SystemExit("self-test insertion point missing")
text = text.replace(needle, insert, 1)

proof_needle = '''    proof["candidate"] = CANDIDATE
    proof["development_gates"] = DEVELOPMENT_GATES
'''
proof_insert = '''    proof["candidate"] = CANDIDATE
    proof["coskewness_estimator_corrected"] = True
    proof["v301_oos_opened"] = False
    proof["v317_oos_opened"] = False
    proof["neighboring_formulas_tested"] = 0
    proof["development_gates"] = DEVELOPMENT_GATES
'''
if text.count(proof_needle) != 1:
    raise SystemExit("proof insertion point missing")
text = text.replace(proof_needle, proof_insert, 1)

limitation = '        "The program-level holdout is not pristine.",\n'
replacement = limitation + '        "V325 corrects a double-warmup arithmetic defect before any V301/V317 OOS was opened.",\n'
if text.count(limitation) != 1:
    raise SystemExit("limitation insertion point missing")
text = text.replace(limitation, replacement, 1)

text = text.replace("V301", "V325").replace("V308", "V332")
text = text.replace("crypto systematic tail", "corrected crypto systematic tail")
text = text.replace("v269_systematic_tail_base", "v269_corrected_systematic_tail_base")
target.write_text(text)
print(f"materialized {target} ({len(text)} bytes)")
