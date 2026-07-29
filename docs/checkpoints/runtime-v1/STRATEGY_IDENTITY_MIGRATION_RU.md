# Протокол migration record и новой идентичности стратегии

Дата фиксации: 2026-07-30.

Этот документ определяет, что делать, если exact historical source, dependency closure или regression fixtures стратегии утрачены. Протокол не разрешает восстанавливать старую идентичность по памяти, текстовому описанию, summary metrics или похожим файлам.

Машинный контракт:

```text
src/finruntime/provenance/migration.py
schemas/runtime/strategy_migration_record.schema.json
```

Проверка:

```bash
finruntime validate-migration migration.json
```

## 1. Два допустимых вида записи

### `byte_identical_materialization`

Используется только когда получены exact bytes, совпадающие со всеми ранее закреплёнными SHA-256.

Обязательные свойства:

```text
predecessor_strategy_id == successor_strategy_id
status                   == validated
forward_clock_reset      == false
changed_components       == []
changed_parameters       == {}
predecessor manifest     == successor manifest
regression fixtures      present
successor provenance     complete
```

Такая запись не меняет экономическую или программную идентичность. Она лишь материализует доказуемо те же bytes.

### `reconstruction`

Используется для любого заново написанного, частично восстановленного, портированного или заменённого implementation, если byte identity не доказана.

Обязательные свойства:

```text
predecessor_strategy_id != successor_strategy_id
forward_clock_reset      == true
changed_components       non-empty
```

Даже если публично известные параметры перенесены без изменений, новый source/dependency closure означает новую стратегию. Исторические метрики predecessor не становятся forward evidence successor.

## 2. Lifecycle

### `planned`

Разрешена фиксация новой identity и design intent до появления кода.

```text
successor_source_hashes       empty
regression_fixture_hashes     empty
successor_provenance_complete false
```

### `implemented`

Source successor уже committed и закреплён hashes, но exact regression ещё не завершён.

```text
successor_source_hashes       non-empty
successor_provenance_complete false
```

Наличие реализации не является доказательством исторической эквивалентности или готовности к forward acceptance.

### `validated`

Для successor существуют committed source hashes и immutable regression fixture hashes.

```text
successor_source_hashes       non-empty
regression_fixture_hashes     non-empty
successor_provenance_complete true
```

`validated` означает полноту provenance новой identity, а не разрешение реального капитала.

## 3. Authorization всегда fail-closed

Во всех migration records следующие поля обязаны оставаться `false`:

```text
capital_authorization_carried_forward
live_ready
real_leverage_authorized
exchange_submission_available
```

Разрешены только режимы:

```text
paper
shadow
```

`live` не является допустимым migration mode.

## 4. Forward clock

Для `reconstruction` forward clock всегда начинается заново.

Нельзя переносить от predecessor:

- накопленные календарные дни;
- число target changes;
- закрытые paper trades;
- acceptance incidents;
- отсутствие corruption failures;
- historical или paper capital authorization.

Forward clock successor начинается только после отдельного immutable checkpoint, который фиксирует committed implementation, source/data hashes, initial account state и acceptance protocol.

Для `byte_identical_materialization` clock не сбрасывается только потому, что доказаны те же bytes и та же стратегия. Это исключение не переносит live authorization: capital decision остаётся отдельным checkpoint.

## 5. Evidence manifests

Migration record содержит repository-relative path → SHA-256 manifests:

```text
source_audits
predecessor_artifact_hashes
successor_source_hashes
regression_fixture_hashes
```

Требования:

- только canonical POSIX paths внутри репозитория;
- абсолютные пути, `..`, пустые segments и backslash запрещены;
- hashes записываются с `sha256:` prefix;
- `source_audits` не может быть пустым;
- migration ID вычисляется детерминированно по canonical JSON без собственного ID.

JSON member order не меняет migration identity.

## 6. Parameters и components

`inherited_parameters` явно перечисляет параметры, перенесённые без изменения.

`changed_parameters` явно перечисляет изменённые значения. Один ключ не может одновременно быть inherited и changed.

`changed_components` описывает изменения implementation-level, например:

```text
dependency_closure
source_reconstruction
data_adapter
signal_engine
risk_layer
execution_model
regression_fixture
```

Для reconstruction список не может быть пустым, даже если `changed_parameters` пуст.

## 7. Применение к текущим blockers

### V75

`V75_MATERIALIZATION_AUDIT.json` доказывает, что exact engine и regression fixtures отсутствуют в доступной истории. Поэтому допустимы только:

1. byte-identical materialization после получения внешнего retained artifact с pinned hashes;
2. reconstruction под новым strategy id.

### V28

`V28_PROVENANCE_CLOSURE_AUDIT.json` подтверждает direct source integrity, но imported module/data closure и daily target fixtures неполны. Выбор same-basename dependencies по предположению не сохраняет identity. До получения retained closure любая self-contained реализация является reconstruction с новым ID.

## 8. Что этот протокол не делает

Migration record сам по себе:

- не регистрирует successor в `STRATEGIES`;
- не создаёт trading implementation;
- не переносит account state;
- не запускает forward clock;
- не разрешает live execution;
- не доказывает прибыльность.

Регистрация successor, implementation checkpoint и forward protocol выполняются отдельными reviewable PR после прохождения соответствующих gates.
