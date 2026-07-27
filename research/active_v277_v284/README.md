# Active V277–V284 — exact low-skew policy, risk-normalized OOS validation

V269 produced one development near-miss that passed every frozen economic gate except maximum realized close gross. This cycle does **not** reopen the 144-policy family search.

## Exact frozen policy

```text
family          low_idiosyncratic_skewness
lookback        180 completed days
long/short      3 assets per leg
rebalance       every 28 days
neutralization  beta
```

The source policy is `low_idiosyncratic_skewness_l180_k3_r28_beta` from V269 development evidence.

## Single risk normalization

- target gross is reduced once from 0.50x to **0.45x**;
- maximum realized close gross remains **0.70x**;
- no neighboring gross scale is evaluated;
- signal, lookback, baskets, rebalance schedule and neutralization remain unchanged.

## Chronology

1. Reprove development 2021–2023 under the single frozen scale.
2. Write the immutable pre-OOS proof.
3. Open validation 2024, holdout 2025 and final 2026 H1 exactly once only if development passes.

Program-level holdout is explicitly non-pristine. A historical pass may create only a paper-forward candidate beginning after 27 July 2026.

## Execution and audits

- completed daily close → next UTC open;
- actual archived funding;
- 30 / 60 / 100 bps per-side costs;
- +1 completed-day latency;
- 100 bps forced-delisting penalty;
- fixed January-2021 universe, no survivor replacement.

## Safety

```text
live_ready = false
real_leverage_authorized = false
profitability_proven = false
integration_permitted = false
```
