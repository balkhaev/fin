# Active V147–V154 — dated VIX futures

Status: `rejected_or_needs_iteration`

V147 зафиксировал блокировку официального CME источника из публичного runner. V148 подтвердил доступ к официальным датированным Cboe VX-файлам. V149–V154 используют отдельные месячные контракты, явный roll, next-open execution, расходы и margin audit.

## Основные метрики

| Candidate | CAGR | Total return | Max DD | Sharpe | Turnover |
|---|---:|---:|---:|---:|---:|
| V75 original | 30.68% | 335.54% | -21.59% | 1.329 | — |
| V154 dated VX sleeve | 1.12% | 28.14% | -21.44% | 0.221 | 1.03x |

## Решение

- eligible policies before 2021: `2`;
- standalone passed: `False`;
- integration permitted: `False`;
- promoted candidates: `[]`;
- `live_ready=false`;
- `real_leverage_authorized=false`.

## Годовая доходность

| Год | V75 original | V154 dated VX sleeve |
|---:|---:|---:|
| 2004 | — | +0.00% |
| 2005 | — | -0.35% |
| 2006 | — | -0.01% |
| 2007 | — | +0.74% |
| 2008 | — | +19.72% |
| 2009 | — | +0.00% |
| 2010 | — | -0.79% |
| 2011 | — | +7.40% |
| 2012 | — | +0.00% |
| 2013 | — | +0.00% |
| 2014 | — | -2.67% |
| 2015 | — | -1.23% |
| 2016 | — | -3.48% |
| 2017 | — | +0.00% |
| 2018 | — | +13.84% |
| 2019 | — | -1.50% |
| 2020 | — | +17.32% |
| 2021 | +104.40% | -3.10% |
| 2022 | +1.08% | -3.84% |
| 2023 | +14.85% | +0.00% |
| 2024 | +41.96% | -10.28% |
| 2025 | +23.82% | +2.84% |
| 2026 | +4.43% | -4.64% |

## Evidence limits

- Official Cboe contract files are settlement/history data, not a broker fill feed.
- Bid/ask is modeled through explicit cost scenarios; historical order-book depth is unavailable.
- Pre-2020 small-account execution uses normalized VX economics; integer VXM feasibility is audited only after VXM launch.
- Program-level pristine holdout is absent. No live trading or real leverage is authorized.
