# Аудит dependency closure V28

Дата аудита: 2026-07-30 по часовому поясу проекта. Машиночитаемая фиксация: `V28_PROVENANCE_CLOSURE_AUDIT.json`.

## Итог

V28 находится в лучшем состоянии, чем V75: прямые engine, runner, frozen candidate, summary и supporting evidence сохранены в репозитории и проходят существующий `verify_archive.py`.

Однако exact executable closure и regression closure не завершены:

```text
direct source integrity     passed
imported dependency closure incomplete
raw input closure           incomplete
daily target fixture        absent
provenance_complete         false
M3                           blocked
```

## Что сохранено напрямую

Закреплены SHA-256 следующих файлов:

```text
v28_exact8h_engine.py           52adefc600e8f15fb6152acec8a4e5a780a12c5120cb30fbae8a93aef97bf49e
v28_run_research.py             64c968222fb10687040b56077a1c82ec0484e359377b20db85cb5d2d10cdcf6a
v28_frozen_candidate.json       a78f7af830e718e77dbd28905cfbdcc7b33ed64a166e18f9910ae3e991822e61
v28_summary.json                2feb4a90c11e5c70ce0de1b24c7ae5f4f9313acc33d5ebe4be9db7602e0db2c3
v26_v27_compact_evidence.json   237da3b32b739fb829468ebc0c17e82f5dc31813b89dbba7df5d637893a1a845
delivery_basis_rejection.json   64c3b5da72eb82aa8a8204c7edeb1a526ba4b278e982d3bd48176a24e4210e1a
verify_archive.py               87d9846821de1be6c05ac7096778d14c64708c2275adaac0c9d54eac3497a0f8
```

`verify_archive.py` подтверждает Git blob identities engine/runner, компилирует оба Python-файла и проверяет frozen candidate, summary, selection proof и acceptance checks.

## Неполный module closure

`v28_exact8h_engine.py` динамически добавляет локальные каталоги в `sys.path` и импортирует:

```text
inputs
signals
engine
execution_policy
v35_funding_carry
v36_cash_carry
```

`v28_run_research.py` дополнительно импортирует:

```text
v50_exact8h_audit
v43_exact8h_fast
run_research
engine
```

По всем reachable refs:

- найдены одноимённые `inputs.py`, `signals.py` и `engine.py`, включая plausible V8 candidates;
- но V28 не хранит manifest, который связывает эти конкретные blobs с историческим local environment;
- `execution_policy.py`, `v35_funding_carry.py`, `v36_cash_carry.py`, `v50_exact8h_audit.py` и `v43_exact8h_fast.py` отсутствуют;
- `run_research.py` и `engine.py` имеют множество несвязанных кандидатов, поэтому выбор по basename был бы недоказуемой подменой.

Plausible V8 files сохранены в audit только как candidates, а не как authorized exact dependencies.

## Неполный data closure

Source использует абсолютные локальные пути:

```text
/mnt/data/v26_work/active_v26
/mnt/data/v26_work/active_v26/v8_frozen
/mnt/data/v6_new
/mnt/data/v5_new
/mnt/data/v5_new/processed
```

Raw market data, processed 8h perpetual/funding files и source hash manifest для этих inputs в canonical archive отсутствуют.

## Нет regression fixture M3

В `research/active_v26_v28/` нет:

- daily target book;
- daily/8h position fixture;
- equity curve fixture;
- representative-day expected hashes.

Summary metrics и acceptance booleans полезны как историческое evidence, но не позволяют доказать математическую идентичность нового runtime target engine на всём regression window.

Файлы V285/V292 и других более поздних стратегий не относятся к V28; совпадение подстроки `V28` в имени версии не является provenance.

## Разрешённый следующий путь

### Exact M3

Получить retained V28 package, затем:

1. закрепить SHA-256 каждого imported module;
2. закрепить raw/processed input manifests;
3. воспроизвести frozen run в изолированном окружении;
4. сохранить immutable daily target/equity fixtures;
5. сравнить новый runtime engine с fixture на всём окне;
6. только после этого установить `provenance_complete=true`.

### Reconstruction

Если retained package утрачен, self-contained implementation должен получить новый strategy id и migration record. Он может использовать публично сохранённые параметры V28, но не должен называться exact V28 до независимой regression-проверки.

## Safety

Аудит не меняет frozen parameters, не добавляет exchange submission и не разрешает реальное плечо.
