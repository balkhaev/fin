# DYN-IV113 — parity локального paper worker

Локальная реализация `src/finruntime/strategies/dyn_paper.py` переносит без
изменения параметров и порядка вычислений forward engine из `balkhaev/fin2`.

## Зафиксированный источник

```text
fin2 commit                         00d0f1d3465ca6a9b6aaa016ca12b5c3375f79e1
strategy-forward-engine.ts SHA-256 4631424615704d13fcd5aa071ea15c2a07163a4816f71465484903af42b594ce
paper-position-pnl.ts SHA-256       9ca0ed71483c7f8324192e5721b0076f14e1269f0988cbbb0cdb7a4b1d20c1c2
paper starting NAV                  10000 USDT
paper reset date                    2026-07-26
exchange submission                unavailable
```

Перенесены 17 активов, 600 дневных свечей, eligibility, три BTC-фильтра,
FLOW, absolute momentum, weekly hold, inverse-vol family allocation,
70%-volatility target, cap `2.5x`, торговые/финансовые расходы и position PnL.

## Прямая проверка на одинаковом market snapshot

29 июля 2026 года оба engine были независимо запущены против Binance public
data с одинаковым paper account. Совпали:

```text
asOf                 2026-07-28
assets               17
eligible             BTC, ETH, ZEC, XRP, BNB, TRX, ADA, XLM
BTC filters          false, false, false
BTC consensus        0
flow family weight   0.53760272148008
abs family weight    0.46239727851992
target gross         0
cash weight          1
positions            0
executions           0
paper NAV            10000 USDT
failed symbols       0
```

Разница floating-point в family weights была меньше `6e-16`. Текущий CASH
поэтому является подтверждённым решением модели, а не fallback при ошибке.
При отсутствии или просрочке локального snapshot UI отдельно показывает
`Нет данных` и не интерпретирует это состояние как CASH.

## Автоматические проверки

`tests/runtime/test_dyn_paper.py` фиксирует parity tie-ranking, полный
synthetic replay, paper positions, капитал `10000`, свечи и запрет exchange
submission. Container healthcheck требует свежий локальный DYN snapshot.
