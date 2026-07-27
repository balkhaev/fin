# Active V301–V308 — fixed-universe systematic-tail factor

This cycle tests a new cross-sectional market-neutral hypothesis in the fixed January-2021 Binance USD-M universe. It does not change or revive the rejected V269/V285 low-skew policy and does not use residual-reversal direction.

## Frozen families

- low systematic coskewness;
- low crash beta on the rolling worst 20% market days;
- high upside-minus-downside beta spread;
- reversed high-coskewness controls.

The 144-policy grid is frozen before profit results: three lookbacks, two basket sizes, three rebalance frequencies and dollar/beta neutralization. Only 108 policies are promotable; reversed controls are diagnostic only.

## Chronology

- warmup: 2020;
- development: 2021–2023;
- validation: 2024;
- holdout: 2025;
- final: 2026 H1.

Selection uses development only. Program-level OOS is explicitly non-pristine. A historical pass can create only a paper-forward candidate beginning no earlier than 27 July 2026.

## Execution

- fixed 13-symbol universe including EOS until delisting;
- completed daily close information, next UTC open execution;
- actual archived funding;
- scheduled-only target changes;
- target gross 0.40x;
- immutable maximum realized close gross 0.70x;
- 30/60/100 bps per-side cost audits;
- +1 completed-day latency audit;
- 100 bps forced-delisting penalty.

## Safety

```text
live_ready = false
real_leverage_authorized = false
profitability_proven = false
integration_permitted = false
```
