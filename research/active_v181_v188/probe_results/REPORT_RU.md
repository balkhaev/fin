# V181 COIN-M liquidation archive probe

Status: `data_access_insufficient`.

| Symbol | Dataset | Valid / attempted | Availability | Schema | Gate |
|---|---|---:|---:|---:|---|
| BTCUSD_PERP | liquidations | 5 / 22 | 22.7% | 1 | FAIL |
| BTCUSD_PERP | metrics | 19 / 22 | 86.4% | 1 | FAIL |
| BTCUSD_PERP | mark_price_1m | 22 / 22 | 100.0% | 2 | FAIL |
| ETHUSD_PERP | liquidations | 5 / 22 | 22.7% | 1 | FAIL |
| ETHUSD_PERP | metrics | 19 / 22 | 86.4% | 1 | FAIL |
| ETHUSD_PERP | mark_price_1m | 22 / 22 | 100.0% | 2 | FAIL |

This is a data/schema gate only. No P&L was calculated and no candidate was promoted.

`live_ready=false`; `real_leverage_authorized=false`; `profitability_proven=false`.
