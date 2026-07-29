# Runtime causality and resumable paper execution checkpoint

Дата фиксации: 2026-07-30.

Этот checkpoint усиливает причинность и идемпотентность paper/shadow runtime. Он не добавляет exchange submission, live mode или разрешение реального плеча.

## Реализовано

### Source causality

`SourceObservation` теперь валидирует базовый порядок времени:

```text
source_timestamp_utc <= available_at_utc <= decision_time_utc
```

Невозможное наблюдение отвергается при построении `MarketSnapshot`, а не только на последующем risk layer. JSON Schema фиксирует структуру source observation; cross-field ordering остаётся исполняемым Python-инвариантом.

### Resumable paper fills

`PaperAccountState` версии `1.1` хранит хешируемый прогресс активного плана:

```text
active_plan_filled_quantities
active_plan_fill_event_ids
```

Paper broker рассчитывает remaining quantity из накопленного исполнения. Повторный вызов того же плана:

- продолжает partial fill только на оставшийся объём;
- не может превысить исходный intent quantity;
- открывает sign-flip child intent только после полного cumulative fill родительского close;
- после полного исполнения становится идемпотентным no-op.

Accounting независимо проверяет cumulative quantity и согласованность статусов `partial` / `filled`.

### Backward compatibility

Состояния `PaperAccountState` версии `1.0` продолжают валидироваться с прежней hash identity и сериализуются без новых полей. Новые состояния создаются как schema `1.1`.

### Account chronology

Fill, funding и mark transitions не могут уменьшить `PaperAccountState.as_of_utc`. Повторное обращение к уже активному плану сохраняет текущее account time. При переходе к новому плану lifecycle не откатывает state timestamp.

### Semantic journal verification

Помимо hash chain журнал проверяет:

- non-decreasing sequence для каждой стратегии;
- допустимый порядок runtime phases;
- обязательные predecessors;
- межфазную временную причинность;
- `HALT_CLEARED` только после `HALT_RAISED`.

Несколько `FILL_RECORDED` одной execution phase могут приходить не по timestamp-порядку. Журнал сохраняет максимальное время фазы и не допускает более ранний `STATE_COMMITTED`.

Semantic batch проверяется до записи, поэтому ошибочная последовательность не оставляет частично дописанный journal.

## Проверки

Локальный PR validation на Python 3.12:

```text
119 runtime tests passed
runtime compile passed
contracts/schema/safety verification passed
```

Отдельно проверены:

- future-dated source rejection;
- schema 1.0 account compatibility;
- partial 5 + resumed 15 = exact intent 20;
- completed-plan replay no-op;
- resumed perpetual sign flip;
- same-phase out-of-order fill timestamps;
- phase-boundary timestamp rejection;
- sequence rollback and missing predecessor rejection.

## Safety status

```text
live_execution_available   false
live_ready                 false
real_leverage_authorized   false
exchange_submission        unavailable
```

Этот checkpoint является engineering evidence для paper/shadow runtime и не является доказательством прибыльности или разрешением использования реального капитала.
