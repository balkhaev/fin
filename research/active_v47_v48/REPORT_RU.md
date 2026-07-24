# Active V47–V48: causal risk layer для on-chain сигнала

## Решение

V47 и V48 отклонены по неизменённым standalone gates. Ни один кандидат не интегрируется с V28.

## Frozen bases

- **V47:** raw V44 `exchange_pressure`;
- **V48:** frozen V46 `confirmation` execution transform.

On-chain signal definitions и V46 execution parameters не изменялись.

## Проверенный risk layer

Все price features были сдвинуты на один день: завершённый close дня `t` мог повлиять только на target следующего open.

Проверены четыре семейства:

1. trend gates по SMA 100/150/200/250;
2. volatility scaling по окнам 30/60/90 дней и target vol 20/30/40%;
3. price-drawdown throttle по окнам 180/252/365 дней и порогам 15/20/25/30%;
4. crash/recovery state machines с fast MA 50/100, slow MA 150/200/250 и exit-return −10/−15%.

Для каждого immutable base сформированы 15 соседних ансамблей. Selection использовал только 2019–2025. 2026 YTD был открыт после записи отдельных selection proofs.

## V47 — raw V44 base

Выбранный диагностический процесс: `volatility+crash_recovery`.

- prefinal CAGR: **13,66%**;
- Sharpe: **0,677**;
- Max DD: **−35,27%**;
- turnover: **27,71×/год**;
- stress full CAGR: **12,83%**;
- severe full CAGR: **1,43%**;
- extreme full CAGR: **−8,83%**;
- 2026 YTD stress: **−0,39%**;
- 2026 YTD severe: **−1,31%**.

Risk layer сократил просадку сырого V44, но не решил оборот, Sharpe и cost sensitivity.

## V48 — frozen V46 base

Выбранный диагностический процесс: `crash_recovery`.

- prefinal CAGR: **24,63%**;
- Sharpe: **0,935**;
- Max DD: **−46,69%**;
- turnover: **3,64×/год**;
- stress full CAGR: **23,18%**;
- severe full CAGR: **21,48%**;
- extreme full CAGR: **19,81%**;
- 2026 YTD: нулевая экспозиция и нулевой результат.

Crash/recovery улучшил CAGR, Sharpe и просадку относительно V46, но DD осталась значительно хуже лимита −35%.

## Лучший near-miss

`v46_confirmation:volatility`:

- prefinal CAGR: **12,61%**;
- Sharpe: **0,784**;
- Max DD: **−35,45%**;
- turnover: **2,93×/год**;
- все stress-сегменты положительны;
- worst severe segment: **−2,08%**;
- worst extreme segment: **−4,97%**.

Кандидат не прошёл immutable DD floor всего примерно на 0,45 процентного пункта. Порог не изменяется задним числом. Следующая допустимая итерация может только снизить заранее заданный volatility budget и/или применить multiplicative crash gate; исходные V44/V46 signals должны оставаться неизменными.

## Provenance

- public compute repo: `balkhaev/trader`;
- public compute commit: `e4231ca36b08b43d78d5d56e9b55013defec501e`;
- workflow run: `30119137266`, success;
- artifact ID: `8606689835`;
- artifact digest: `sha256:ab7dc6196ac983c467b5fff0ab3edeb5cb302711735bfc4fac29b3bfafc116d5`;
- V47 selection proof: `c9b2f65009c4b630098fecc7b2c745159f945a6e3d80db97d75ef77751d6b78c`;
- V48 selection proof: `3972c8f3d128fefe632d5058d5215b0289e8ccad0beb3a205b64b069b6ae30bd`;
- exact V47 source SHA-256: `2684796bd541c53de3a4d72a1567f4e43dc7d7033c5b87469cd3f81c9d6c0cd2`.

## Статус

```text
V47: rejected_or_needs_iteration
V48: rejected_or_needs_iteration
promoted_leaders: []
V28: frozen growth benchmark
```
