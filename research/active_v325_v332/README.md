# Active V325–V332 — corrected systematic coskewness replay

This cycle corrects one pre-OOS arithmetic defect in V301/V317 and repeats the original V301 grid without changing any economic parameter.

## Defect

The original estimator first subtracted rolling means and then applied another rolling mean to the centered product. A 365-day policy therefore needed roughly 730 observations before producing a signal. In V317 this made the 2021 return exactly zero by construction.

V301 and V317 never opened their OOS windows, so a correction is still chronologically admissible.

## Corrected moment

For asset return `X` and BTC/ETH market return `Y`, the rolling third co-moment is computed from single-window raw moments:

```text
E[(X-μx)(Y-μy)^2]
= E[XY^2] - 2 μy E[XY] - μx E[Y^2] + 2 μx μy^2
```

It is normalized by `σx × σy²`. The corrected estimator becomes available after one lookback, not two.

## Frozen replay

The following remain exactly as in V301:

- all 144 policies and 108 promotable policies;
- families, lookbacks, basket sizes and rebalance schedules;
- target gross 0.40x and maximum realized gross 0.70x;
- fixed 13-symbol universe and delisting treatment;
- 30/60/100 bps per-side costs and +1 day latency;
- development and OOS boundaries;
- all development and post-selection gates.

No neighboring formula, lookback or threshold is tested.

```text
live_ready = false
real_leverage_authorized = false
profitability_proven = false
integration_permitted = false
```
