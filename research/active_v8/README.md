# Active V8 — relative trend overlay

Active V8 is a research-only extension of the frozen V7 process.

## Candidate

`V8_V7_RATCHET_SCALE_0_4_CAP_0_85`

The process combines:

1. the frozen V6 cross-sectional spot-momentum sleeve;
2. the frozen V7 BTC/ETH defensive hedge;
3. a new market-neutral BTC–ETH relative-trend sleeve;
4. a one-way risk ratchet.

The base target gross exposure is capped at 0.85 before market drift. No
borrowed gross above 1.0 is intentionally requested.

## New independent sleeve

The V8 sleeve is long one of BTC/ETH perpetuals and short the other. Direction
comes from the completed ETH/BTC ratio trend. It is approximately
market-neutral rather than a new directional crypto bet.

Three neighboring rules are averaged:

- 126-day ratio momentum, 5% hysteresis, 60-day volatility, 20% target,
  75% maximum sleeve gross, weekly review;
- 90-day ratio momentum, 15% hysteresis, 30-day volatility, 10% target,
  100% maximum sleeve gross, 28-day review;
- 90-day ratio momentum, 15% hysteresis, 60-day volatility, 10% target,
  100% maximum sleeve gross, 28-day review.

Only 40% of that ensemble is added to V7.

## Execution

- completed daily close signal;
- execution at the next UTC-day open;
- existing positions receive the overnight close-to-open move;
- actual historical Binance funding is included;
- spot delistings receive a 100 bps forced-liquidation penalty;
- nominal costs: 40 bps spot / 20 bps perpetual per side;
- stress costs: 40 / 40 bps;
- severe costs: 80 / 80 bps.

## Reproduce

```bash
python -m pip install -r research/active_v8/canonical/requirements.txt
python research/active_v8/canonical/run_candidate.py --self-test
python research/active_v8/canonical/run_candidate.py \
  --v6 /path/to/active-research-v6-compute \
  --v5 /path/to/active-research-v5-compute \
  --output artifacts/active_v8
```

The V5/V6 artifact directories provide the validated Binance archives and the
frozen spot-sleeve construction. Their hashes are recorded by the prior
research stages.

## Status

`frozen_paper_forward_candidate`

This status permits only an unchanged shadow/paper-forward process. It is not
permission to trade real capital or use leverage.
