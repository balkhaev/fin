# Active V333–V340 — corrected low-coskewness basket with BTC/ETH beta hedge

This is a single-policy follow-up to the corrected V325 replay. No new alpha family or hedge parameter is searched.

## Exact corrected alpha

```text
low_systematic_coskewness_l365_k3_r14_beta
```

In V325 this was the top-ranked corrected process. It had positive development returns in every year, CAGR 11.36%, Sharpe 0.921 and Max DD -12.39%, but failed the 1.0 Sharpe gate, maximum close gross 0.70x and cross-sectional short-leg profitability. V325 OOS remained closed.

## Frozen construction

The hedge specification is copied unchanged from V317, which was fixed before the corrected V325 result:

- three lowest corrected-coskewness assets;
- inverse-volatility long basket, fixed long gross 0.25x;
- rolling 90-day completed-data beta;
- inverse-volatility short BTCUSDT/ETHUSDT hedge;
- maximum hedge gross 0.35x;
- maximum total target gross 0.60x;
- maximum realized close gross 0.70x;
- 14-day scheduled rebalances only;
- no neighboring alpha, long-gross, beta-window, hedge-cap or rebalance variants.

Hedge-leg profit is not independently required. Portfolio realized beta, total return, drawdown, concentration and stress robustness are gated.

## Chronology

Development reproof uses 2021–2023. Validation 2024, holdout 2025 and final 2026 H1 open once only if every development gate passes. Program-level OOS is explicitly non-pristine. A historical pass can authorize only paper-forward monitoring from 27 July 2026, never live execution.

```text
live_ready = false
real_leverage_authorized = false
profitability_proven = false
integration_permitted = false
```
