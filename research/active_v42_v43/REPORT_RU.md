# Active V42–V43: BTC open interest и positioning/crowding

## Решение

Обе независимые гипотезы отклонены. Ни один из 15 OI-процессов и ни один из 15 crowding-процессов не прошёл заранее зафиксированные prefinal, cost и tail-risk gates. Итоговый статус:

```text
rejected_or_needs_iteration
promoted_leaders = []
```

Active V28 остаётся frozen growth benchmark. V42/V43 не добавляются к нему даже малым весом: отрицательное самостоятельное ожидание нельзя исправлять диверсификацией.

## Методология

- BTCUSDT USD-M perpetual;
- официальные ежедневные Binance metrics archives с нативной частотой 5 минут;
- 519 687 валидных нативных observations, причинно агрегированных в завершённые 8-часовые snapshots;
- exact 8h price bars и архивные funding timestamps;
- сигнал только по завершённому 8h close, исполнение на следующем 8h open;
- target gross cap 0,35;
- расходы 40/80/120/160/200 б.п. на сторону;
- forced-exit penalty 100 б.п.;
- selection: 2021–2025; январь–июнь 2026 открыт после записи selection proof;
- V42 и V43 ранжировались независимо.

Критерии допуска включали CAGR не ниже 8%, Sharpe не ниже 0,70, prefinal drawdown лучше −20%, turnover не выше 18×/год, положительные stress-сегменты, ограниченные severe/extreme потери и положительный final при 40 и 80 б.п.

## Data quality

| Проверка | Результат |
|---|---:|
| Exact 8h price rows | 6 387 |
| Native 5m metrics rows | 519 687 |
| Complete 8h metrics snapshots | 5 429 |
| Missing/unavailable 8h snapshots | 958 |
| Archives с доступным checksum | 2 269 |
| Checksum failures после обязательного redownload | 0 |
| Неположительные нативные rows | 323 |
| Неположительные metric cells | 634 |

Нулевые технические observations не forward-fill’ились и не превращались в торговый сигнал. Snapshot признавался недоступным, если финальное требуемое значение было неположительным. Повреждённый cached ZIP обязательно перезагружался; после повторного несовпадения исследование завершилось бы ошибкой.

## V42 — open-interest regimes

Выбранный диагностический процесс: `oi:oi_breakout`.

| Метрика | Результат |
|---|---:|
| Prefinal CAGR | **−1,13%** |
| Prefinal Sharpe | **−1,02** |
| Prefinal Max DD | −5,74% |
| Prefinal turnover | 3,94×/год |
| Stress full CAGR | **−1,45%** |
| Stress full return | **−7,71%** |
| Stress full Max DD | −7,85% |
| 2026 H1 stress return | **−2,33%** |
| Severe full return | −16,19% |
| Extreme full return | −23,89% |

Все 15 процессов имели отрицательный prefinal CAGR. OI confirmation не компенсировал ложные breakout-сигналы, funding и торговые расходы.

Block-bootstrap выбранного процесса также отрицателен: медианная доходность трёхлетних перестановок около −1,1%, вероятность положительной траектории около 12–13%; для шестилетних перестановок вероятность положительного результата около 4–5%.

## V43 — positioning and crowding

Выбранный диагностический процесс: `crowding:crowding_reversal`.

| Метрика | Результат |
|---|---:|
| Prefinal CAGR | **−0,03%** |
| Prefinal Sharpe | −0,09 |
| Prefinal Max DD | −0,89% |
| Prefinal turnover | 0,86×/год |
| Stress full return | **−0,24%** |
| Stress full Max DD | −0,89% |
| Средняя gross-экспозиция | **0,046%** |
| 2026 H1 stress return | **−0,09%** |
| Severe full return | −2,09% |
| Extreme full return | −3,91% |

Маленькая просадка не означает стабильный edge: стратегия почти всегда находилась вне рынка. Bootstrap колеблется около нуля, а вероятность результата выше +10% равна нулю во всех проверенных горизонтах.

## Воспроизводимость

Public workflow полностью прошёл:

- sealed source reconstruction и SHA-256;
- Python compilation;
- deterministic causal/no-look-ahead self-test;
- checksum-aware data loading;
- полный prefinal ranking;
- запись selection proof до final;
- sealed-final backtest.

```text
compute repo:       balkhaev/trader
compute PR:         #25
compute head:       3d3e1321f356816b9e27c2213b0e861281654e74
workflow run:       30110715901
artifact ID:        8603441176
artifact digest:    sha256:bf750812874db3e2db6b1f0986deb55c84fa0339491eee6c70825eae3a4f7195
selection proof:    838c3a311302f1a932aee20bcdf7c96f348f3af50241e865c51d5b09c74648fa
```

## Следствие для программы

OI и account-ratio metrics не дают надёжного standalone edge в протестированной causal конструкции. Повторно оптимизировать те же lookbacks, z-scores и thresholds по 2021–2026 нельзя. Следующее исследование должно менять экономический источник P&L, а не тонко настраивать V42/V43.
