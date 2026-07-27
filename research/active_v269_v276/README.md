# Active V269–V276 — fixed-universe crypto lottery quality

This cycle tests a new cross-sectional distribution-shape factor in the fixed January-2021 Binance USD-M universe. It does **not** use momentum direction, downside beta, liquidity ranking, funding thresholds, breakouts, or post-hoc inversion of V253/V261 controls.

## Frozen universe

BTC, ETH, BNB, XRP, ADA, LTC, BCH, EOS, DOGE, LINK, DOT, TRX and SOL. Delisted and weak assets remain in the panel. Survivor replacement is forbidden.

## Frozen families

1. low idiosyncratic skewness;
2. low maximum daily residual return;
3. low normalized upside residual tail;
4. reversed high-lottery control.

The first three families are promotable. The fourth is a predeclared negative control.

## Grid

- lookback: 90 / 180 / 365 completed days;
- long and short baskets: 3 or 4 assets each;
- scheduled rebalance: 14 / 28 / 56 days;
- dollar-neutral or beta-neutral;
- 144 policies total, 108 promotable.

## Chronology and execution

- warmup: 2020;
- development: 2021–2023;
- validation: 2024;
- holdout: 2025;
- final: 2026 H1;
- signal at completed daily close;
- execution at next UTC open;
- target gross 0.50x;
- maximum realized close gross gate 0.70x;
- actual archived funding;
- 30 / 60 / 100 bps per-side cost audits;
- +1 completed-day latency audit;
- forced-delisting penalty 100 bps.

2024–2026 remain unopened unless a policy passes the frozen development gates.

## Safety

```text
live_ready = false
real_leverage_authorized = false
profitability_proven = false
integration_permitted = false
```
