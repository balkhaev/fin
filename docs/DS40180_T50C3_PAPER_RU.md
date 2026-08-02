# DS-40/180 T50-C3 v2 — постоянная paper-торговля OKX

Стратегия работает только с публичными данными OKX и не содержит API-ключей,
authenticated-клиента или функций отправки ордеров.

## Что изменилось в v2

- paper-счёт больше не пересчитывается от reset date на каждом цикле;
- состояние хранится атомарно, события записываются в append-only hash-chain journal;
- исправлено противоречие между early-bear и медленным short-рукавом;
- введены отдельные режимы `bull`, `early_bear`, `confirmed_bear`;
- текущий adverse funding уменьшает только дорогую сторону позиции;
- общий gross выбирается по covariance/stressed-correlation контроллеру: 0.75 / 1.25 / 1.50;
- вес одного контракта ограничен 25% NAV;
- добавлен ограниченный 4h crisis overlay с максимальным gross 15%;
- малые увеличения риска удерживаются no-trade band, но выходы, sign flip и сокращения риска выполняются всегда;
- paper fill использует публичный bid/ask OKX и дополнительный impact 2 bps.

## Файлы runtime

```text
/data/runtime/ds40180_t50c3_paper_snapshot.json
/data/runtime/ds40180_t50c3_paper_state.json
/data/runtime/ds40180_t50c3_paper_events.jsonl
```

Snapshot можно пересоздать, но `state.json` и `events.jsonl` должны храниться на
постоянном volume. Уже обработанные доходности не переписываются при поздней
коррекции свечи OKX; вместо этого создаётся `data_revision_detected` event.

Проверка журнала:

```bash
python -m finruntime.strategies.ds40180_t50c3_paper \
  --snapshot /data/runtime/ds40180_t50c3_paper_snapshot.json \
  --verify-journal /data/runtime/ds40180_t50c3_paper_events.jsonl
```

## Запуск

Корневой Docker stack запускает worker автоматически. Настройки:

```text
FIN_DS40180_STARTING_CASH=10000
FIN_DS40180_RESET_DATE=2026-07-31
FIN_DS40180_POLL_SECONDS=300
```

Standalone:

```bash
python -m finruntime.strategies.ds40180_t50c3_paper \
  --snapshot runtime/ds40180_t50c3_paper_snapshot.json \
  --reset-date 2026-07-31 \
  --starting-cash 10000 \
  --poll-seconds 300
```

## Риск

`riskScale=3` не означает постоянный gross 3x. Итоговый портфель проходит
asset-cap, funding guard, covariance stress и динамический gross-cap. Абсолютный
paper-only потолок — 1.50x, стрессовый — 0.75x.

Статус безопасности остаётся неизменным:

```text
exchange_submission_available = false
live_ready                    = false
real_leverage_authorized      = false
```


## Forward A/B: v1 reference против v2

Каждый цикл v2 теперь параллельно пересчитывает **read-only** эталон старой
`okx-paper-v1` логики из закреплённого commit
`cb942798acdd0f27867b923476dc9b50eb67984f`. Исходный engine сохранён
байт-в-байт с blob `dd573280ddec0e2ae50e33941d4f0154525d4809`.
Эталон не регистрируется как активная стратегия и не влияет на позиции v2.

Файлы наблюдения:

```text
/data/runtime/ds40180_t50c3_ab_snapshot.json
/data/runtime/ds40180_t50c3_ab_events.jsonl
```

A/B journal добавляет не более одной пары на закрытый рыночный день. Повторные
внутридневные циклы обновляют snapshot, но не увеличивают число forward-дней.
Сравнение фиксирует NAV, return, maximum drawdown, realized volatility,
downside volatility, turnover, trading costs, funding и число исполнений.

Окна проверки:

- 30 forward-дней — первичный review;
- 60 дней — промежуточный review;
- 90 дней — предпочтительное окно решения.

Даже после 90 дней победитель автоматически не назначается: snapshot только
помечает исследование как `eligible_for_decision`, после чего требуется ручной
разбор качества данных, риска и исполнения.
