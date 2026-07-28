# V485–V492 — independent macro/crisis replay

Status: `rejected_after_frozen_oos`.

| Период / audit | CAGR | Total return | Max DD | Sharpe |
|---|---:|---:|---:|---:|
| Bridge 2021–2023 | +0.31% | +0.98% | -10.15% | 0.062 |
| Holdout 2024–2025 | +4.65% | +9.83% | -3.99% | 1.086 |
| Final 2026 H1 | -2.48% | -1.27% | -5.86% | -0.435 |
| Full stress | +1.97% | +45.27% | -11.39% | 0.365 |
| Full severe | +0.58% | +11.80% | -15.70% | 0.108 |
| Full extreme | -1.69% | -27.77% | -36.79% | -0.309 |

## Frozen gates

- [ ] `legacy_standalone_checks_all`
- [x] `holdout_2024_2025_return_positive`
- [ ] `final_2026h1_return_positive`
- [ ] `full_stress_cagr_min`
- [ ] `full_stress_sharpe_min`
- [x] `full_stress_max_drawdown_min`
- [ ] `extreme_full_cagr_positive`

ETF/FX histories remain proxy research data. No result authorizes integration, capital or live execution.
