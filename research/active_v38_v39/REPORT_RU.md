# Active V38–V39: exact-8h session effects и breakout

## Решение

Обе гипотезы отклонены. Ни один из 33 проверенных процессов не прошёл заранее заданные prefinal gates. Параметры после открытия января–июня 2026 года не менялись.

## V38 — session effects

Проверены четыре семейства:

- same-session trailing seasonality;
- seasonality с подтверждением среднего тренда;
- короткий cross-sectional continuation;
- редкая shock continuation/reversal по режиму.

Лучший `shock_regime` не был рабочим edge:

- prefinal CAGR около **0,22%**;
- Max DD около **−37,7%**;
- turnover около **41,5×/год**;
- не все stress-сегменты положительны.

Seasonality и continuation уничтожались оборотом и cross-sectional price risk.

## V39 — exact-8h breakout

Лучший процесс — `slow`:

- prefinal CAGR **3,08%**;
- Max DD **−14,33%**;
- turnover 6,85×/год;
- worst severe segment **−4,09%**;
- worst extreme segment **−6,27%**.

Однако stress 2024 дал **−1,86%**, поэтому условие положительности всех prefinal-сегментов нарушено.

На полном периоде при 40 б.п. на сторону:

- CAGR **2,40%**;
- return **+13,92%**;
- Max DD **−14,33%**;
- Sharpe **0,42**.

При 80 б.п. CAGR стал отрицательным (**−0,40%**). Январь–июнь 2026 года при stress costs дал **−2,11%**.

## Методология

- fixed January-2021 universe, включая слабые и делистнутые активы;
- exact 8-hour bars и archived funding timestamps;
- completed close → next 8h open;
- stress/severe/extreme/super/catastrophic costs: 40/80/120/160/200 б.п. на сторону;
- target gross cap 0,35;
- selection только по 2021–2025;
- final открыт после записи selection proof;
- deterministic causal/no-look-ahead self-test прошёл.

## Provenance

- public compute commit: `8c3969d1b8fbaa42a4cd7e7e97d27ec50edac3c8`;
- artifact ID: `8601353002`;
- artifact digest: `sha256:f70cfbdd681a88d46b3a4de7a0464c374244befeef74b9182420abc3a9631f53`;
- selection proof SHA-256: `ad661b5b830aef4693908ae707cd8ce83976f8ec0cd5bafedfc7e6ad04b13237`.

V28 остаётся frozen growth benchmark. V38/V39 не интегрируются с ним.
