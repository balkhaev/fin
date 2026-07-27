# Active V317–V324 — exact low-coskewness long basket with BTC/ETH beta hedge

This is a single-policy development follow-up to V301. It does not search another alpha family or neighboring parameters.

## Exact alpha source

```text
low_systematic_coskewness_l365_k4_r14_beta
```

In V301 this was the unique promotable policy that passed CAGR, Sharpe, drawdown, turnover, rebalance count, gross, breadth, concentration and long-leg gates, while failing only all-years-positive and cross-sectional short-leg profitability. V301 OOS remained closed.

## Frozen construction

- select the same four lowest-coskewness assets;
- inverse-volatility long basket, fixed long gross 0.25x;
- estimate rolling 90-day beta using completed data;
- short BTCUSDT and ETHUSDT as an inverse-volatility beta hedge;
- maximum hedge gross 0.35x;
- maximum target gross 0.60x;
- rebalance every 14 completed daily bars only;
- no neighboring long-gross, hedge-cap or rebalance variants.

The hedge is risk control, not a profit sleeve. Its standalone P&L is not required to be positive; realized portfolio beta and total risk-adjusted return are gated instead.

## Chronology

Development reproof uses 2021–2023 only. Validation 2024, holdout 2025 and final 2026 H1 open once, and only if every development gate passes. Program-level OOS is explicitly non-pristine. A historical pass means paper-forward observation beginning no earlier than 27 July 2026, never live authorization.

## Safety

```text
live_ready = false
real_leverage_authorized = false
profitability_proven = false
integration_permitted = false
```
