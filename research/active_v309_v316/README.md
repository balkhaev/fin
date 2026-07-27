# Active V309–V316 — fixed-universe residual path quality

This cycle tests whether assets with more persistent and internally consistent market-residual return paths outperform noisy residual paths. It does not use the sign of cumulative return, momentum direction, low-skew results, liquidity ranking or funding thresholds.

## Frozen families

- high absolute residual efficiency ratio;
- low residual sign-change rate;
- high residual variance ratio;
- reversed low-efficiency controls.

The fixed 144-policy grid uses three lookbacks, two basket sizes, three rebalance intervals and dollar/beta neutralization. Reversed controls are diagnostic only.

## Chronology and execution

Warmup starts in 2020. Development is 2021–2023; validation is 2024; holdout is 2025; final is 2026 H1. Completed daily information executes at the next UTC open. The fixed January-2021 13-symbol universe is preserved, archived funding is included, delistings receive a 100 bps penalty and survivor replacement is forbidden.

Target gross is 0.40x; the immutable maximum realized close gross is 0.70x. Cost audits are 30/60/100 bps per side plus a one-day latency replay.

No OOS segment opens without a complete development pass. Program-level OOS is non-pristine; a historical pass could create only a paper-forward candidate beginning no earlier than 27 July 2026.

```text
live_ready = false
real_leverage_authorized = false
profitability_proven = false
integration_permitted = false
```
