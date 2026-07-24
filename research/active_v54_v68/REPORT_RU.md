# Active V54–V68: on-chain × spot/perpetual integration

## Решение

On-chain режим можно использовать как ограниченный риск-бюджет для BTC/ETH perpetual overlay, а не только как long/flat фильтр. Доказанного live edge пока нет: в 2026 YTD V62/V65/V67 не открывали позицию.

- **V28** остаётся основным frozen growth benchmark.
- **V65** заморожен как консервативный small-leverage кандидат.
- **V67** заморожен как более доходный blended кандидат.
- Ни один вариант не получает статус live-ready.

## Общая конструкция

- BTC/ETH spot + USD-M BTC/ETH perpetual;
- on-chain base: exchange pressure, valuation cycle, V46 confirmation;
- perpetual overlay только при согласии on-chain и trend/vol/funding режима;
- target gross caps 1,05–1,15×;
- completed information, exact 8h bars и archived funding timestamps;
- initial/maintenance margin и operational reserve;
- расходы 40/80/120/160/200 bps на сторону;
- 2026 YTD исключён из выбора.

## V54/V55: overlay поверх V26

V54: prefinal CAGR **30.51%**, Max DD **-23.49%**, max gross **1.046×**. Общего превосходства над V28 не доказано, а on-chain overlay в final был нулевым.

V55 добавил cash sleeve: full CAGR **29.44%**, Max DD **-22.44%**. Но predeclared selection не пройден и overlay-forward остался нулевым.

## V62/V65

### V62

- prefinal CAGR **29.36%**;
- Max DD **-24.99%**;
- Sharpe **1.103**;
- turnover **5.28×/год**;
- observed max gross **1.077×**;
- average perpetual gross **3.63%**;
- post-2020 CAGR **13.35%**;
- 200 bps full CAGR **17.59%**;
- liquidations **0**.

### V65 — conservative cap 1,10×

- prefinal CAGR **28.34%**;
- Max DD **-24.05%**;
- Sharpe **1.103**;
- observed max gross **1.043×**;
- average perpetual gross **2.71%**;
- 200 bps full CAGR **17.20%**;
- liquidations **0**.

V66 подтвердил положительный margin buffer при initial margin 50%, maintenance 20%, funding ×3, delay 2 days и widened intrabar paths.

## V67/V68 — лучший blended вариант

V67: **80% V52 diversified + 20% exchange pressure**, V46 confirmation и небольшой futures overlay.

- target gross cap **1,15×**;
- observed max gross **1.078×**;
- average perpetual gross **5.01%**;
- prefinal CAGR **31.39%**;
- total prefinal return **414.33%**;
- Max DD **-25.12%**;
- Sharpe **1.097**;
- turnover **5.72×/год**;
- post-2020 CAGR **15.31%**;
- robust CAGR floor **30.77%**;
- best-year positive-log share **54.59%**;
- worst leave-one-year-out CAGR **15.35%**;
- liquidations **0**.

| Costs per side | Full CAGR | Max DD |
|---:|---:|---:|
| 40 bps | 29.20% | -25.12% |
| 80 bps | 26.45% | -25.93% |
| 120 bps | 23.76% | -27.81% |
| 160 bps | 21.12% | -30.77% |
| 200 bps | 18.54% | -33.60% |

V68:

- observed minimum margin buffer **6.44%**;
- harsh full CAGR **27.27%**;
- 20% widened + 120 bps full CAGR **21.61%**;
- 20% widened + 120 bps Max DD **-26.73%**;
- liquidations **0**.

## Ограничения

1. В 2026 YTD on-chain режим не был активен.
2. V67 full CAGR **29.20%** не доказал превосходство над V28 CAGR 30,17% на полностью сопоставимом пути.
3. Perpetuals добавляют exchange, funding, mark-price и liquidation risks.
4. Положительный исторический margin buffer не гарантирует защиту от будущего gap или venue failure.

## Статус

- V65: frozen paper-forward small-leverage tier, cap 1,10×.
- V67: frozen paper-forward return tier, cap 1,15×.
- Live trading и дальнейшее увеличение плеча запрещены до первой ненулевой forward-позиции с фактическими fill/funding/margin logs.
