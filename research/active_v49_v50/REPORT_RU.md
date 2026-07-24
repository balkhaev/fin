# Active V49–V50: пониженный vol-budget и multiplicative crash gate

## Решение

Обе конструкции прошли неизменённые prefinal gates на 2019–2025, но не получили статус frozen paper-forward candidate: в 2026 YTD исходный frozen V46 `confirmation` не открывал позицию. Нулевой final не подтверждает edge.

Официальный статус обоих процессов:

```text
historical_risk_candidate_needs_nonzero_forward
```

V28 остаётся frozen growth benchmark.

## Immutable base

V49 и V50 используют точный frozen V46 `confirmation`. Определение on-chain сигнала V44 и execution parameters V46 не менялись.

## V49 — lower volatility budget

Проверены четыре соседних budget-множителя исходного target-vol ансамбля 20/30/40%:

```text
80%, 85%, 90%, 95%
```

Из них сформированы все 15 непустых equal-family ensembles. Выбор выполнен только по 2019–2025.

Победитель:

```text
v49_vol_budget:vol_budget_95
```

Prefinal:

- CAGR: **12,08%**;
- total return: **+122,21%**;
- Sharpe: **0,783**;
- Max DD: **−34,08%**;
- turnover: **2,84×/год**;
- average gross: 19,02%;
- все stress-сегменты положительны;
- worst severe segment: **−1,87%**;
- worst extreme segment: **−4,66%**.

Полный stress-путь:

- CAGR: **11,40%**;
- total return: **+122,21%**;
- Max DD: **−34,08%**;
- Sharpe: **0,762**;
- turnover: 2,69×/год.

Строгие расходы на полном пути:

| Расходы на сторону | CAGR | Max DD |
|---:|---:|---:|
| 40 б.п. | 11,40% | −34,08% |
| 80 б.п. | 10,21% | −35,50% |
| 120 б.п. | 9,02% | −36,89% |
| 160 б.п. | 7,85% | −38,25% |
| 200 б.п. | 6,70% | −39,74% |

## V50 — multiplicative volatility × crash/recovery

V47 усреднял разные risk transforms, что частично разбавляло защиту. V50 применяет защиту мультипликативно:

```text
frozen V46 position × volatility scale × crash/recovery gate
```

Проверены соседние 90% и 95% vol-budgets и те же causal crash/recovery / price-drawdown families. Из четырёх базовых families сформированы 15 ансамблей.

Победитель:

```text
v50_multiplicative:vol95_x_recovery
```

Prefinal:

- CAGR: **13,38%**;
- total return: **+140,93%**;
- Sharpe: **0,909**;
- Max DD: **−23,97%**;
- turnover: **2,80×/год**;
- average gross: 17,18%;
- все stress-сегменты положительны;
- worst severe segment: **−0,31%**;
- worst extreme segment: **−3,22%**.

Полный stress-путь:

- CAGR: **12,63%**;
- total return: **+140,93%**;
- Max DD: **−23,97%**;
- Sharpe: **0,885**;
- turnover: 2,65×/год.

Строгие расходы на полном пути:

| Расходы на сторону | CAGR | Max DD |
|---:|---:|---:|
| 40 б.п. | **12,63%** | **−23,97%** |
| 80 б.п. | 11,44% | −25,50% |
| 120 б.п. | 10,26% | −27,00% |
| 160 б.п. | 9,09% | −28,52% |
| 200 б.п. | 7,93% | −30,18% |

## Календарная устойчивость V50

Stress-результаты:

| Год | Return | Max DD |
|---:|---:|---:|
| 2019 | +1,35% | −0,91% |
| 2020 | +89,81% | −11,48% |
| 2021 | +6,73% | −16,99% |
| 2022 | −0,63% | −1,82% |
| 2023 | +15,06% | −7,83% |
| 2024 | +4,13% | −6,35% |
| 2025 | −1,44% | −15,65% |
| 2026 YTD | 0,00% | 0,00% |

V50 всё ещё зависит от сильного 2020 года и имеет два слегка отрицательных календарных периода. Это не отменяет прохождение сегментных gates, но снижает уверенность в переносимости.

## Block bootstrap V50

По 5 000 перестановок prefinal returns:

- однолетний горизонт: вероятность положительного результата **84,4–88,6%**;
- однолетняя медиана: **+41,6–44,2%**;
- однолетний 5-й процентиль: примерно **−10,9…−14,7%**;
- двухлетний горизонт: вероятность положительного результата **93,0–96,4%**;
- двухлетняя медиана: **+108,5–113,7%**;
- медианная двухлетняя Max DD: примерно **−20,7…−21,6%**.

Bootstrap переставляет уже наблюдавшиеся режимы и не является прогнозом.

## Почему V50 не повышается до live/paper-forward лидера

2026 YTD дал:

```text
gross exposure = 0
return = 0
```

Это произошло потому, что frozen V46 base не активировался. Следовательно, final не проверил ни исполнение, ни риск, ни доходность V50. Положительный historical result недостаточен для замены V28 или использования реального капитала.

Следующий допустимый этап:

1. заморозить точный V50;
2. вести его параллельно V28/V26 на ненулевом forward-периоде;
3. не менять vol multiplier 0,95 и crash/recovery family после появления первой позиции;
4. требовать фактические spread/slippage и совпадение target/filled exposure.

## Provenance

- public compute commit: `123c8a8715d3631700f43d071576d3078686bb74`;
- workflow run: `30120322889`, success;
- artifact ID: `8607140186`;
- artifact digest: `sha256:525b22c6dfa31fb3a16fdd406b5ed9d6724b83dea03847dc4b7e52c00e702c9e`;
- exact source SHA-256: `3e0e1693133b153fd4b7903b0f3cf2bc14a48f45b86cf7583647c2101300c8e3`;
- V49 selection proof: `2c99c836e10b8f16dad11723a161a245c66cee93557f128c41ee46269530b804`;
- V50 selection proof: `07afe5ec69b5b3e9db957ad4680c75016b96ea68a799abd9d857beb13267c8f9`.
