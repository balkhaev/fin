# Active V147–V154 — CME dated-contract execution research

Цель цикла — заменить continuous/front-month proxy из V139–V146 на реальные датированные контракты CME и разделить:

1. back-adjusted исследовательский сигнал;
2. нескорректированный исполнимый P&L конкретного контракта;
3. фактический roll между двумя датированными контрактами;
4. multiplier, tick value, комиссии, spread и margin.

## Этапы

- **V147** — проверка доступности официальных страниц и settlement JSON для исторических trade dates;
- **V148** — contract-month parser, lifecycle/expiry calendar и raw-data contract;
- **V149** — signal-only back-adjusted series;
- **V150** — executable unadjusted contract ledger;
- **V151** — contract sizing, multipliers и micro-contract feasibility;
- **V152** — bid/ask, fees и roll slippage;
- **V153** — margin/liquidation audit;
- **V154** — standalone frozen selection и sealed evaluation.

Исследование не переиспользует слабые параметры V139–V146. Если официальный источник недоступен из публичного CI или не даёт воспроизводимую историческую цепочку, это фиксируется как data-access blocker; результат не подменяется новым Yahoo proxy.

`live_ready = false` и `real_leverage_authorized = false` до прохождения V154 на execution-grade данных.
