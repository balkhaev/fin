# Active V44–V46: on-chain cycle and execution research

## Решение

V44–V46 сохранены как важное отрицательное/промежуточное доказательство. Ни один процесс не прошёл заранее зафиксированные standalone gates, поэтому ни один не интегрируется с frozen V28.

## V44 — on-chain cycle

Выбранный диагностический сигнал: `exchange_pressure`.

- prefinal CAGR: **+13,28%**;
- Sharpe: **0,56**;
- Max DD: **−48,67%**;
- turnover: **36,67×/год**;
- stress full CAGR: **+12,46%**;
- 2026 YTD stress: **−0,47%**;
- 2026 YTD severe: **−1,53%**.

Сигнал содержал валовую предсказуемость, но не выдержал turnover, drawdown и segment gates.

## V45 — miner and flow regime

Выбранный процесс: `hash_ribbon+exchange_flow`.

- prefinal CAGR: **+0,25%**;
- Sharpe: **0,07**;
- Max DD: **−21,32%**;
- stress full CAGR: около **+0,04%**;
- 2026 YTD stress: **−1,51%**.

Самостоятельного edge не найдено.

## V46 — execution-aware V44

Исходный V44 `exchange_pressure` был оставлен неизменным. Проверялись только causal execution families: phase-averaged schedules, hysteresis, no-trade bands и persistence confirmation.

Выбранный диагностический процесс: `confirmation`.

- prefinal CAGR: **+21,44%**;
- Sharpe: **0,80**;
- turnover: **3,44×/год**;
- Max DD: **−59,91%**;
- stress full CAGR: **+20,19%**;
- severe full CAGR: **+18,63%**;
- extreme full CAGR: **+17,09%**;
- 2026 YTD: нулевая экспозиция.

Execution filtering снизил оборот и повысил CAGR, но сконцентрировал редкие позиции в режимах с неприемлемым хвостовым риском. Поэтому V46 также отклонён.

## Методология и данные

- Coin Metrics Community BTC daily CSV закреплён на commit `f1a36afb962731c387bb03982758ab0103063da5` и Git blob `5e50f336d268e1f3a38e9885b5aaef36de529700`;
- on-chain metric date `t` не может влиять на target раньше open `t+2`;
- Binance spot/perpetual/funding inputs проверялись по доступным checksum;
- overnight P&L и return base проверены отдельными self-tests;
- selection выполнен только на 2019–2025;
- 2026 YTD открыт после записи selection proof;
- costs: 40/80/120/160/200 б.п. на сторону.

## Статус

```text
V44: rejected_or_needs_iteration
V45: rejected_or_needs_iteration
V46: rejected_or_needs_iteration
V28: frozen growth benchmark
```

Следующая допустимая итерация может менять только отдельный causal risk/execution layer поверх неизменного on-chain сигнала. Ослаблять задним числом standalone gates нельзя.
