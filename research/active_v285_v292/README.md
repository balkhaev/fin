# Active V285–V292 — exact low-skew alpha with hourly gross controller

V269 found one development near-miss whose only failed gate was daily close gross. V277 showed that a fixed 10% notional reduction did not solve the problem. This cycle keeps the **original exact alpha-policy** and replaces repeated scale search with one preregistered intraday risk mechanism.

## Exact alpha-policy

```text
low_idiosyncratic_skewness_l180_k3_r28_beta
```

Daily ranking, lookback, baskets, rebalance schedule and beta-neutralization are unchanged from V269.

## Frozen hourly controller

- daily target gross: 0.50x;
- daily target enters at the next UTC daily open;
- at every completed hourly open, if current gross is above 0.60x, positions are reduced pro rata back to 0.50x;
- hard hourly close gross gate: 0.70x;
- simultaneous adverse long-low / short-high intrahour stress-gross gate: 0.85x in development;
- no alternative trigger or reset levels are tested.

## Data

- checksum-aware Binance USD-M daily bars for the signal;
- checksum-aware Binance USD-M 1h bars for execution and risk monitoring;
- exact archived funding events;
- fixed January-2021 universe, including EOS until delisting;
- no survivor replacement.

## Chronology

1. Reprove development 2021–2023.
2. Write the immutable proof.
3. Open validation 2024, holdout 2025 and final 2026 H1 only after a full development pass.

Program-level holdout is non-pristine. A historical pass permits only paper-forward monitoring after 27 July 2026.

## Costs and audits

- 30 / 60 / 100 bps per-side costs on daily and risk-reduction trades;
- +1 completed-day alpha latency;
- 100 bps forced-delisting penalty.

## Safety

```text
live_ready = false
real_leverage_authorized = false
profitability_proven = false
integration_permitted = false
```
