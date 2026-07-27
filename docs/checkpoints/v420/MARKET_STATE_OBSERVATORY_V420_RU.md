# V413–V420 — market-state observatory

Status: `observatory_ready_for_forward_attribution`.

The model is a causal description and attribution layer, not a trading signal.

## Frozen representation

- 14 completed-data features;
- 6 interpretable axes: trend, breadth, stress, rotation, liquidity, leverage;
- robust scalers fitted only on 2021–2023;
- deterministic 6-state codebook fitted only on 2021–2023;
- 2024, 2025 and 2026 H1 used only for state stability and novelty diagnostics;
- no V75/V136 returns, thresholds or allocations used to fit the state model.

## Technical quality

```text
passed                         True
development assignment days    1095
OOS assignment days            912
min development occupancy      3.93%
max development occupancy      25.94%
minimum centroid distance      1.396
OOS novelty rate               9.76%
OOS mean confidence            0.354
```

## Authorized use

The observatory may join future paper telemetry for V75, V136 and V28 to explain:

- return and drawdown by market state;
- turnover and slippage by market state;
- reconciliation failures and stale-data exposure;
- state transitions preceding execution stress.

It may not change strategy parameters or capital allocation from historical diagnostics.

```text
strategy_parameter_changes_permitted = false
allocation_changes_permitted         = false
live_ready                           = false
real_leverage_authorized             = false
```
