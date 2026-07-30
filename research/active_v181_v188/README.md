# Active V181–V188 — actual liquidation-flow research

Этот цикл ищет новый intraday источник P&L на **фактических forced-liquidation observations**, а не на свечном объёме, open-interest regime или crowding proxy.

Он не меняет V75/V28/V136 и не получает долю их капитала до самостоятельного прохождения всех gates.

## Fixed universe

```text
BTCUSD_PERP
ETHUSD_PERP
venue: Binance COIN-M
```

COIN-M выбран до просмотра результата, потому что публичный архив содержит отдельные `liquidationSnapshot`, `metrics` и mark-price observations. Недоступный symbol/date не заменяется другим после просмотра.

## Версии

- **V181** — checksum-aware data/schema/coverage probe;
- **V182** — causal minute event panel и provenance;
- **V183** — liquidation continuation family;
- **V184** — liquidation exhaustion/reversal family;
- **V185** — immutable development selection;
- **V186** — costs, latency, missing-data and concentration audits;
- **V187** — holdout/final decision и guarded V75 integration gate;
- **V188** — checkpoint и frozen paper-forward protocol.

## Frozen chronology

```text
development: 2021-01-01 through 2023-12-31
validation:  2024-01-01 through 2024-12-31
holdout:     2025-01-01 through 2025-12-31
final:       2026-01-01 through 2026-06-30
```

Holdout/final не участвуют в выборе. На уровне всей программы эти окна не являются pristine; это явно сохраняется в selection proof.

## Causal execution

- liquidation events агрегируются только после завершения минутного bucket;
- metrics/OI должны иметь `available_at <= decision_time`;
- вход не раньше следующего доступного minute open;
- никаких future fills, future liquidation totals или post-event extrema в сигнале;
- старая позиция получает весь move до выхода;
- missing critical price/event data запрещает новую позицию.

## Frozen event families

Liquidation intensity нормируется причинно rolling history, без использования будущих данных.

```text
rolling intensity percentiles: 95.0, 97.5, 99.0
minimum cluster duration:       1m, 3m
holding horizon:                1m, 5m, 15m, 60m
reaction:                       continuation, reversal
side:                           long-liquidation, short-liquidation
```

Соседние процессы оцениваются как заранее объявленная grid. Порог нельзя ослаблять после просмотра 2024–2026.

## Baseline costs and risk

```text
target gross:              0.25x
maximum concurrent trades: 1
base round-trip:           15 bps
severe round-trip:         30 bps
extreme round-trip:        50 bps
latency audit:             +1 minute
forced/missing exit:       additional 20 bps
```

## Standalone gates

До какой-либо интеграции одновременно требуются:

```text
development CAGR                  >= 5%
development Sharpe                >= 1.00
development Max DD                >= -12%
development closed trades         >= 150
BTC and ETH standalone return     > 0
validation return                 > 0
holdout return                    > 0
final return                      > 0
severe full CAGR                  > 0
1-minute-delay full CAGR          > 0
worst calendar year               >= -8%
top-month positive P&L share      <= 35%
zero unexplained data/fill events
```

Слабый standalone sleeve не смешивается с V75.

```text
live_ready = false
real_leverage_authorized = false
profitability_proven = false
```
