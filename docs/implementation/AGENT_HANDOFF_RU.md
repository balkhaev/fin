# Handoff implementation-агенту

## Начни здесь

```bash
git clone https://github.com/balkhaev/fin.git
cd fin
git checkout main
```

Прочитай в порядке:

1. `IMPLEMENTATION.md`.
2. `docs/implementation/MASTER_IMPLEMENTATION_PLAN_RU.md`.
3. `docs/implementation/RUNTIME_CONTRACTS_RU.md`.
4. `docs/implementation/ACCEPTANCE_CRITERIA_RU.md`.
5. `docs/checkpoints/v138/` и `research/active_v131_v138/`.
6. research evidence V75/V28 и `services/funding_router/`.

## Контекст решений

- Новый поиск параметров не входит в implementation scope.
- Primary — `v75_atlas_nx`.
- Control — `v28_growth_control`.
- Shadow execution — `v136_execution_shadow`.
- `funding_router` — отдельный сервис и отдельный P&L.
- PR №34 содержит неканонический transport/bootstrap и должен считаться superseded, а не основой для merge.
- PR №33 остановлен и не должен быть реализован.

## Первая задача

Создай PR `agent/runtime-contracts` со следующими файлами:

```text
src/finruntime/models.py
src/finruntime/data/contracts.py
src/finruntime/data/availability.py
schemas/runtime/*.schema.json
docs/checkpoints/runtime-v1/SOURCE_REGISTRY.json
tests/runtime/test_contracts.py
tests/runtime/test_availability.py
```

До переноса торговой логики зафиксируй exact source registry:

```json
{
  "profile": "v75_atlas_nx",
  "research_commit": "...",
  "source_paths": ["..."],
  "source_sha256": {"path": "sha256"},
  "frozen_config_paths": ["..."],
  "expected_regression_outputs": ["..."]
}
```

## Вторая задача

Перенеси V75 в production-shaped module без изменения математики. Правильная последовательность:

1. Создать committed fixture из уже существующих research inputs.
2. Получить canonical daily targets исходным research code.
3. Сохранить representative expected hashes.
4. Написать новый `profiles/v75.py`.
5. Сравнить каждый день и каждый instrument.
6. Только после точного совпадения подключать portfolio/accounting layer.

Не начинай с написания «чистой новой реализации по описанию» — это почти гарантированно создаст расхождение.

## Третья задача

Добавить V28 как отдельный process/state. Не использовать V75 state, cash или high-water.

## Четвёртая задача

Добавить V136 как shadow-only преобразование уже рассчитанных V75 targets. Оно не должно менять primary plan.

## Пятая задача

Добавить paper ledger и planner. Live adapter не создавать.

## Обязательные design decisions

### Determinism

- input snapshot hash;
- strategy target hash;
- execution plan hash;
- sequence number;
- same state + same snapshot => same bytes after canonical JSON serialization.

### Idempotency

Повторная обработка одного snapshot:

- не создаёт новый order plan;
- не увеличивает sequence;
- не дублирует fill/event;
- возвращает ссылку на существующий plan id.

### State safety

- append-only event log;
- atomic current-state pointer;
- recovery test после simulated crash;
- corrupted state => HALT, не empty state.

### Data safety

- no naive datetimes;
- no silent forward-fill;
- `available_at_utc` обязателен;
- stale on-chain => accelerator zero;
- missing critical market price => no risk increase.

## Не делай

- не добавляй новые индикаторы;
- не меняй universe;
- не меняй thresholds;
- не подключай live API keys;
- не объединяй funding-router equity с V75;
- не объявляй V136 primary;
- не используй historical results как acceptance implementation tests без exact fixtures;
- не коммить generated state/database/secrets.

## Рекомендуемые labels и PR titles

```text
runtime
paper-trading
safety
reproducibility
no-live
```

PR titles:

```text
Add runtime contracts and provenance registry
Reproduce frozen V75 targets in finruntime
Add independent V28 control runtime
Add V136 shadow execution filter
Add deterministic paper ledger and planner
Add runtime operations and forward checkpoint
```

## Команды definition-of-done

Целевая единая команда:

```bash
python -m pip install -e '.[runtime-dev]'
python -m pytest tests/runtime -q
python -m finruntime self-test
python scripts/verify_runtime.py --full
```

Она должна работать на чистом clone без внешних архивов и без доступа к старым Actions artifacts.

## Вопросы, которые решаются кодом, а не предположениями

- Какие exact research files являются source of truth? Запиши hashes.
- Какой timestamp делает on-chain observation доступной? Храни его явно.
- Какой next open используется? Вычисляй по calendar/venue contract.
- Как обрабатывается perpetual sign flip? Два intents: reduce-only close, затем open.
- Что происходит при stale или missing data? HALT risk increase.
- Что происходит при повторном запуске? Тот же plan id.

## Финальный handoff обратно владельцу

Implementation-агент должен вернуть:

- merged PR SHAs;
- runtime checkpoint branch;
- exact command запуска paper cycle;
- strategy registry output;
- regression evidence;
- список известных operational blockers;
- подтверждение, что live adapter отсутствует.
