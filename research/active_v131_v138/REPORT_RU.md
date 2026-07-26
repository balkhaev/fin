# Active V131–V138: regime overlays and execution layer

## Objective

Improve the frozen V75 ATLAS-NX control without changing its underlying V27/V4/V67 signals. The cycle tests signed breadth overlays, risk-on hysteresis and a risk-first no-trade execution layer. Selection uses 2021–2025; January–June 2026 is diagnostic only.

## Original control

- Full CAGR: **+30.68%**;
- Full total return: **+335.54%**;
- Max drawdown: **-21.59%**;
- Sharpe: **1.329**;
- Annual turnover: 10.64x.

## Candidate comparison

| Candidate | Full CAGR | Max DD | Sharpe | Turnover | 2026 H1 | Decision |
|---|---:|---:|---:|---:|---:|---|
| V132 Signed breadth | +29.65% | -24.76% | 1.241 | 12.72x | +5.61% | `rejected_or_needs_iteration` |
| V134 Risk-on hysteresis | +30.52% | -22.44% | 1.262 | 11.23x | +4.62% | `rejected_or_needs_iteration` |
| V136 Execution plateau | +30.77% | -21.82% | 1.335 | 9.95x | +4.87% | `rejected_or_needs_iteration` |

## Findings

### V132 signed breadth plateau

- Improved 2022, 2024 and 2026 H1, but reduced 2021/2023/2025 returns.
- Prefinal CAGR fell below V75 and Max DD worsened. Rejected.

### V134 risk-on hysteresis

- Confirmation and minimum-hold logic reduced signal churn.
- Prefinal CAGR still failed to exceed V75, while drawdown was slightly worse. Rejected.

### V136 execution plateau

- Best near-miss of this cycle.
- Full CAGR +30.77% versus V75 +30.68%.
- Prefinal CAGR +32.96% versus V75 +32.92%.
- Turnover 10.34x versus V75 11.05x.
- It missed the frozen +0.5pp CAGR uplift and 10% turnover-reduction gates. It is not promoted.

## Annual returns

|        year |   V75_original |     V132 |     V134 |     V136 |
|------------:|---------------:|---------:|---------:|---------:|
| 2021.000000 |       1.044016 | 0.979806 | 1.028246 | 1.073946 |
| 2022.000000 |       0.010812 | 0.025080 | 0.010812 | 0.013304 |
| 2023.000000 |       0.148519 | 0.123294 | 0.139815 | 0.148404 |
| 2024.000000 |       0.419552 | 0.430239 | 0.454781 | 0.420020 |
| 2025.000000 |       0.238165 | 0.214021 | 0.218682 | 0.218501 |
| 2026.000000 |       0.044256 | 0.053464 | 0.044256 | 0.046903 |

## Decision

```text
promoted_candidates = []
primary_control = V75_ATLAS_NX
best_near_miss = V136_execution_plateau
live_ready = false
real_leverage_authorized = false
```

The cycle demonstrates that modest execution improvements are possible, but the measured uplift is too small relative to model-selection risk. The next research should seek a genuinely independent P&L source or execution-grade forward data rather than relaxing these gates.
