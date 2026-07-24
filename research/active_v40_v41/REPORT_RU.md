# Active V40–V41: exact-8h flow и liquidity research

## Решение

Обе гипотезы отклонены. Ни один из 30 independently ranked процессов не прошёл заранее зафиксированные prefinal gates. Final January–June 2026 был открыт только после записи selection proof; параметры после этого не изменялись.

## V40 — flow-confirmed continuation

Проверены четыре семейства:

- channel breakout с abnormal quote volume и aligned taker-buy pressure;
- return momentum с подтверждением потоком;
- persistent taker imbalance плюс medium trend;
- simultaneous quote-volume/trade-count impulses.

Выбранный диагностический процесс `flow_breakout+persistent_flow`:

- prefinal CAGR **−0,77%**;
- Sharpe **−0,18**;
- Max DD **−19,14%**;
- turnover **19,76×/год**;
- worst severe segment **−13,93%**;
- worst extreme segment **−20,05%**.

Stress full, 40 б.п. на сторону:

- CAGR **−1,22%**;
- total return **−6,51%**;
- Max DD **−21,86%**;
- Sharpe **−0,31**;
- turnover **19,81×/год**;
- modelled costs **4 651,02** на стартовый счёт 10 000;
- funding P&L **−44,23**.

January–June 2026: **−2,80%**. Severe full CAGR: **−8,74%**.

Raw `flow_breakout` на prefinal имел около +1,17% CAGR, но нарушил segment, drawdown и turnover gates. Поток не превратил price-breakout в устойчивый edge после расходов.

## V41 — liquidity exhaustion/reversal

Проверены:

- wick exhaustion на экстремальном объёме;
- failed auction после предыдущего шока;
- price/flow divergence;
- high-range liquidity sweep.

Выбранный диагностический процесс `flow_divergence`:

- prefinal CAGR **−1,32%**;
- Sharpe **−0,74**;
- Max DD **−7,85%**;
- turnover **2,10×/год**;
- worst severe segment **−8,92%**;
- worst extreme segment **−12,22%**.

Stress full:

- CAGR **−1,20%**;
- total return **−6,43%**;
- Max DD **−7,85%**;
- Sharpe **−0,70**;
- turnover **1,94×/год**.

Почти нулевой final объясняется почти нулевой экспозицией, а не положительным ожиданием. Severe и extreme результаты отрицательны.

## Методология

- fixed January-2021 universe из 13 активов, включая слабые и делистнутые рынки;
- quote volume, trade count и taker-buy quote из завершённых Binance 8h-баров;
- completed 8h close → next 8h open;
- actual archived funding timestamps;
- target gross cap 0,35;
- forced-exit penalty 100 б.п.;
- costs 40/80/120/160/200 б.п. на сторону;
- V40 и V41 ранжировались независимо;
- final не использовался для выбора;
- deterministic causal/no-look-ahead self-test прошёл.

## Provenance

- public compute commit: `2773eba1ba00bb30ebd63111890a22a3bded90bc`;
- workflow run: `30106779770`, success;
- artifact ID: `8601942308`;
- artifact digest: `sha256:54c055eaf4663db2e5781e93e27ce6308ff60eb28e3608fe6a95d074fcb9f759`;
- selection proof SHA-256: `77253002a19a83f748ec43d5f4bce83ca08e4f956f3bc3544eed8aa7b99613f1`.

V28 остаётся frozen growth benchmark. V40/V41 не интегрируются с ним.
