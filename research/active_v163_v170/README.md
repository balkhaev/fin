# Active V163–V170 — cross-venue funding dislocation evidence

Этот цикл исследует уже реализованный `services/funding_router` как **отдельный market-neutral sleeve**. Он не меняет V75/V28/V136 и не использует их капитал.

## Версии

- **V163** — public endpoint/market coverage probe и текущий scan.
- **V164** — исторические funding/mark-price observations.
- **V165** — causal same-asset cross-venue replay.
- **V166** — fees, basis, slippage и latency audit.
- **V167** — immutable pre-final selection.
- **V168** — holdout/final evaluation.
- **V169** — отдельный capital-allocation gate; только если standalone прошёл.
- **V170** — checkpoint и forward protocol.

## Экономическая гипотеза

Для одного base asset:

```text
LONG  perpetual на площадке с более низким ожидаемым funding
SHORT perpetual на площадке с более высоким ожидаемым funding
```

Ноги имеют одинаковый base notional. Strategy return должен происходить из funding spread после:

- обеих entry/exit комиссий;
- basis P&L между площадками;
- slippage/adverse-selection buffers;
- различающихся funding timestamps;
- недоступности или смены знака следующего funding;
- margin/collateral constraints.

## Fixed universe

```text
assets:    BTC ETH SOL XRP DOGE
venues:    Binance USD-M, Bybit linear, OKX USDT swaps
```

Недоступный asset/venue не заменяется другим после просмотра результата. Coverage сохраняется как data-quality evidence.

## Frozen policy grid

```text
predicted cumulative funding edge: 12, 16, 20, 24 bps
hold:                              8h, 16h, 24h
entry absolute basis cap:          10, 20, 35 bps
stability observations:            1, 2, 3
```

Итого 108 процессов. Actual selection cutoff будет записан до открытия final window в `selection_proof_before_final.json`.

## Causal rule

В момент решения разрешены только:

- уже выплаченные funding observations;
- completed mark-price candles;
- basis по цене, доступной в момент next-open;
- никаких будущих realized funding rates.

Следующий funding прогнозируется консервативно как минимум из:

```text
last realized spread
median of recent realized spreads
```

Сделка открывается на следующем доступном 8h open после сигнала.

## Baseline cost model

При gross 1.0x, по 0.5x на каждую ногу:

```text
one maker + one taker entry
conservative taker exit on both legs
explicit slippage buffer
explicit exit-basis buffer
```

Все costs хранятся отдельно от funding и basis P&L.

## Standalone gates

До какой-либо интеграции требуется одновременно:

```text
prefinal CAGR                 >= 5%
prefinal Sharpe               >= 0.75
prefinal Max DD               >= -12%
all prefinal segments         > 0
severe full CAGR              > 0
worst calendar year           >= -8%
annual turnover               <= 20x
zero modeled liquidations
positive margin buffer
holdout return                > 0
final return                  > 0
```

Если standalone не проходит, V169 не создаёт combined equity.

## Evidence caveat

Исторические funding APIs могут иметь разную глубину и revision policy. Raw payloads, request parameters, timestamps и SHA-256 сохраняются. Program-level holdout не является pristine, потому что funding family уже исследовалась ранее.

```text
live_ready = false
real_leverage_authorized = false
profitability_proven = false
```
