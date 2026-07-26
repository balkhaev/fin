# Active V155–V162 — VIX carry / convexity switch

Status: `rejected_or_needs_iteration`

## Результат

| Candidate | CAGR | Total return | Max DD | Sharpe | Turnover |
|---|---:|---:|---:|---:|---:|
| V75 original | 30.68% | 335.54% | -21.59% | 1.329 | 10.64x |
| V160 carry/convex sleeve | -0.71% | -14.66% | -27.10% | -0.075 | 1.77x |

## Frozen decision

- policies: `116`;
- eligible before 2021: `0`;
- standalone passed: `False`;
- integration permitted: `False`;
- promoted candidates: `[]`;
- `live_ready=false`;
- `real_leverage_authorized=false`.

## Годовая доходность

| Год | V75 original | V160 carry/convex | Integrated |
|---:|---:|---:|---:|
| 2004 | — | -4.46% | — |
| 2005 | — | -5.24% | — |
| 2006 | — | -0.10% | — |
| 2007 | — | +25.93% | — |
| 2008 | — | +1.29% | — |
| 2009 | — | -2.33% | — |
| 2010 | — | -1.21% | — |
| 2011 | — | +1.73% | — |
| 2012 | — | -2.92% | — |
| 2013 | — | -0.86% | — |
| 2014 | — | -0.90% | — |
| 2015 | — | -1.40% | — |
| 2016 | — | -2.92% | — |
| 2017 | — | -4.86% | — |
| 2018 | — | +1.77% | — |
| 2019 | — | -3.55% | — |
| 2020 | — | +0.96% | — |
| 2021 | +104.40% | -4.89% | — |
| 2022 | +1.08% | +0.11% | — |
| 2023 | +14.85% | -3.24% | — |
| 2024 | +41.96% | -3.13% | — |
| 2025 | +23.82% | -1.64% | — |
| 2026 | +4.43% | -0.27% | — |

## Evidence limits

- Гипотеза создана после просмотра провала V154 после 2020 года; program-level holdout не pristine.
- Official Cboe settlements не являются broker fill feed.
- Calendar spread уменьшает outright vega, но не устраняет gap и margin risk.
- Ни live trading, ни реальное плечо не разрешены.
