# Active V171–V178 — Binance/Hyperliquid funding sleeve

Status: `rejected_or_needs_iteration`.

Policies: 54; eligible on development: 0.
Selected diagnostics/components: `fund_l3_e32_h3_b40, fund_l12_e16_h3_b40, fund_l6_e24_h3_b40`.

| Metric | Full | Development 2023-2024 | Holdout 2025 | Final 2026 H1 |
|---|---:|---:|---:|---:|
| CAGR | +0.19% | +0.36% | +0.00% | +0.00% |
| Total return | +0.59% | +0.59% | +0.00% | +0.00% |
| Max DD | -0.10% | -0.10% | +0.00% | +0.00% |
| Sharpe | 1.031 | 1.420 | 0.000 | 0.000 |

Severe full CAGR: +0.05%; 8h-delay full CAGR: +0.06%.
OOS block-bootstrap probability of positive total return: 0.0%.

## Promotion checks

- [ ] `eligible_development`
- [ ] `development_cagr`
- [x] `development_sharpe`
- [x] `development_max_drawdown`
- [x] `all_development_years_positive`
- [ ] `development_trade_count`
- [x] `annual_turnover`
- [ ] `holdout_return_positive`
- [ ] `final_return_positive`
- [x] `severe_full_cagr_positive`
- [x] `delay_full_cagr_positive`
- [x] `worst_year`
- [ ] `full_trade_count`
- [x] `zero_forced_exits`
- [x] `data_coverage`

V75 remains the mandatory first control column in `ANNUAL_RETURNS.csv`.
No live trading or real leverage is authorized.
