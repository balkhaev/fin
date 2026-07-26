# Active V171–V178 — Binance/Hyperliquid funding sleeve

Этот цикл продолжает V163–V170, но **не переиспользует отвергнутую V165 price-basis гипотезу**. Цель — проверить исходную экономическую идею cross-venue funding carry на паре площадок, для которой доступны обе исторические ноги: Binance USD-M и Hyperliquid perpetuals.

V75/V28/V136 не меняются. До standalone-прохода новый sleeve не получает их капитал.

```text
live_ready = false
real_leverage_authorized = false
profitability_proven = false
```

## Почему этот цикл нужен

V163–V164 не смогли проверить Binance/Bybit/OKX funding carry из-за региональной недоступности endpoints и отсутствия полной парной истории. V165 после этого проверил другую гипотезу — convergence публичных Binance/OKX candles с фиксированным funding buffer — и был отвергнут.

V171 возвращается к **реальным funding cashflows обеих ног**:

```text
если lagged Binance funding > lagged Hyperliquid funding:
    LONG Hyperliquid perpetual
    SHORT Binance USD-M perpetual

если lagged Hyperliquid funding > lagged Binance funding:
    LONG Binance USD-M perpetual
    SHORT Hyperliquid perpetual
```

На обеих площадках используется одинаковое base quantity. Общий gross не превышает equity: примерно 0.5x на каждую ногу, без разрешения реального leverage.

## Версии

- **V171** — public access, provenance и coverage gate.
- **V172** — 8h normalization и causal alignment funding timestamps.
- **V173** — frozen 54-policy grid.
- **V174** — fees, slippage, basis, missing-data и 8h-delay audits.
- **V175** — immutable development-only selection.
- **V176** — holdout 2025.
- **V177** — final 2026 H1 и guarded V75 integration.
- **V178** — checkpoint, frozen decision и forward protocol.

## Fixed universe

```text
assets: BTC ETH SOL XRP DOGE
venues: Binance USD-M, Hyperliquid
period: 2023-05-01 .. 2026-06-30
bar:    8h
```

Недоступный asset не заменяется другим после просмотра результатов. Для запуска grid требуются минимум три fixed assets с достаточным synchronized price/funding coverage.

## Evidence

- Binance prices: checksum-verified 1h public archive, уже материализованный V165; берутся только 8h boundary opens.
- Binance funding: checksum-verified monthly `fundingRate` archive.
- Hyperliquid prices: public `candleSnapshot` 8h.
- Hyperliquid funding: public hourly `fundingHistory`, агрегированный в 8h cashflows.
- Нормализованные observations, request metadata и SHA-256 сохраняются в ветке.

Funding payment с timestamp ровно на границе относится к интервалу, который **только что завершился**. Сделка на этой границе считается открытой после payment — это исключает получение уже прошедшего funding.

## Frozen forecast

В момент решения разрешены только завершённые предыдущие funding blocks. Для lookback 3/6/12 blocks прогноз равен sign-consistent conservative minimum между:

```text
abs(last realized funding spread)
abs(rolling median realized funding spread)
```

Будущий realized funding не используется.

## Frozen 54-policy grid

```text
lookback:                    3, 6, 12 blocks
predicted portfolio edge:   16, 24, 32 bps
hold:                        1, 2, 3 blocks
entry absolute basis cap:   20, 40 bps
```

Порог edge считается до фактически списанных fees/slippage; обе entry и exit операции обеих ног учитываются в equity.

## Selection protocol

```text
development: 2023-05-01 .. 2024-12-31
holdout:     2025-01-01 .. 2025-12-31
final:       2026-01-01 .. 2026-06-30
```

2025 и 2026 исключены из ranking. До открытия holdout фиксируются ranking CSV, выбранные компоненты, design hash и selection-proof hash.

Development gates:

```text
CAGR                  >= 5%
Sharpe                >= 0.75
Max DD                >= -10%
all calendar years    > 0
trade count           >= 20
annual turnover       <= 40x
```

После selection одновременно требуются positive holdout/final, positive severe и 8h-delay CAGR, worst year не хуже -8%, минимум 30 сделок и отсутствие forced exits из-за missing observations.

## Audits

| Audit | Fee/side | Slippage/side | Delay |
|---|---:|---:|---:|
| base | 5.0 bps | 2.5 bps | 0 |
| severe | 5.5 bps | 6.0 bps | 0 |
| extreme | 7.5 bps | 10.0 bps | 0 |
| delay_8h | 5.0 bps | 2.5 bps | 1 block |

Missing funding вызывает penalty и forced exit; конец sample закрывается с дополнительным conservative exit penalty.

## Ограничения

Hyperliquid hourly funding внутри 8h блока оценивается по boundary price, а не по каждой hourly mark. Public candles не являются synchronized executable quotes. Outage, liquidation-engine, collateral segregation и transfer risk моделируются неполно. Кроме того, на уровне всей программы 2025–2026 уже не pristine.

Даже исторический проход означает только `frozen_historical_candidate_needs_forward`; затем нужен новый paper-forward период. Live и real leverage остаются запрещены.
