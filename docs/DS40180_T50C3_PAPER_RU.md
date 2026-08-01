# DS-40/180 T50-C3 — OKX paper trading

## Что запускается

Корневой `scripts/run_paper_stack.py` автоматически поднимает отдельный worker
`finruntime.strategies.ds40180_t50c3_paper`. Он использует только публичные
данные OKX по USDT perpetual swaps:

- закрытые дневные свечи `1Dutc`;
- текущий mark price;
- историю фактически начисленного funding (`realizedRate`, с fallback на
  `fundingRate`).

API-ключи не нужны. В модуле нет клиента размещения ордеров; поля
`exchange_submission_available`, `live_ready` и `real_leverage_authorized`
жёстко равны `false`.

## Зафиксированная стратегия

- три базовых рукава: Long-only, Light short hedge и Slow-bear specialist;
- режим DS-40/180: медленный 180-дневный режим с гистерезисом и ранний
  40-дневный триггер;
- T50-C3: целевая волатильность 50%, weekly risk scale от `1.0x` до `3.0x`;
- дополнительный paper-only safety cap: gross не выше `1.25x`, вес одного
  инструмента не выше `30%` капитала;
- новый независимый счёт $10 000 и новый forward clock; исторические метрики
  исследовательского бэктеста не наследуются.

## Файл состояния

По умолчанию атомарный snapshot записывается сюда:

```text
/data/runtime/ds40180_t50c3_paper_snapshot.json
```

В нём находятся текущие веса, long/short позиции, NAV, execution ledger,
фактический/fallback funding, режим DS-40/180 и текущий risk scale.

## Настройки контейнера

```text
FIN_DS40180_STARTING_CASH=10000
FIN_DS40180_RESET_DATE=2026-07-31
FIN_DS40180_POLL_SECONDS=300
```

Запуск одного диагностического цикла без всего стека:

```bash
python -m finruntime.strategies.ds40180_t50c3_paper \
  --snapshot /tmp/ds40180.json \
  --reset-date 2026-07-31 \
  --starting-cash 10000 \
  --once
```

## Fail-closed поведение

Для расчёта требуется BTC и минимум восемь контрактов с достаточной общей
историей. При ошибке OKX, недостатке свечей или некорректных данных новый
snapshot не заменяет последний корректный. Отсутствующий funding штрафуется
консервативным годовым fallback 5% на соответствующий notional.
