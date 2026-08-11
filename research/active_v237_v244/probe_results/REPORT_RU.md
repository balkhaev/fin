# Active V237–V244 — funding-settlement premium compression

Status: `data_access_insufficient`.

- BTC and ETH: 7/8 sampled months for every required dataset (87.5%).
- SOL: 6/8 sampled months for every required dataset (75.0%).
- Available archives have stable schemas: 12 columns for price/premium klines and 3 columns for funding.
- June 2026 is absent for all five datasets on BTC, ETH and SOL.
- January 2021 is additionally absent for SOL.

The immutable 90% availability threshold and mandatory June 2026 observation therefore failed. No policy selection or strategy P&L was computed. Missing archives are not treated as zero observations.

```text
selection_run = false
full_backtest_run = false
integration_permitted = false
live_ready = false
real_leverage_authorized = false
profitability_proven = false
```
