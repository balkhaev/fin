# Аудит материализации V75

Дата аудита: 2026-07-30 по часовому поясу проекта. Машиночитаемая фиксация: `V75_MATERIALIZATION_AUDIT.json`.

## Результат

Точные исходные файлы V75, закреплённые в `SOURCE_REGISTRY.json`, **не восстанавливаются из доступной истории репозитория**:

```text
v75_operational_feedback_engine.py
sha256 3303cd91511bca0be81ade21272e1e8ba6f76adf826d238e9c4bd7cbe78f69fc

v75_original_stress_equity.csv
sha256 0f578a56132ec9858031cc6ad5cc919f732e66990625c2fdd6ff91143e44956b

v75_original_annual_returns.csv
sha256 e3de37108b5d459ad9f8324388a3a34571f29c5c594a77d82477cb812c8e0d25
```

Поэтому:

```text
provenance_complete = false
M2 exact regression = blocked
live_ready           = false
```

Это не означает, что стратегию невозможно реализовать заново. Это означает, что заново написанный или приблизительно восстановленный код нельзя честно называть byte-identical V75 без нового strategy id и migration record.

## Проверенные источники

### Все reachable Git objects

Полный проход `git rev-list --objects --all` по fetched refs не обнаружил ни одного blob с ожидаемым размером и SHA-256. Все три target-файла остались в списке missing.

### V138 compact transport

Исторический commit:

```text
9e5b4c2d8324ece94a2b28cfe60137e6c0a79eb5
```

Проверены manifest, каждый encoded fragment, общий encoded payload и архив:

```text
format          base64(tar.xz)
archive bytes   25284
archive sha256  5a0fbbfec2433be17e3dba0fc6ff6f22cc9d3f59c3943c71cc736e822d6232ef
files extracted 152
```

Архив валиден, но не содержит ни одного target-файла — даже файла с совпадающим basename.

### V87/V87b transport

Исторический commit:

```text
3faf7794c8fa740ce601b4c040da24d627d2501d
```

Фактическая структура:

```text
legacy prefix decoded bytes  5600
V87b part 000 decoded bytes   6375
V87b part 001 decoded bytes   7984
combined decoded bytes       14359
common prefix bytes           5600
XZ footer present             false
XZ EOF reached                false
corruption offset             9216 compressed bytes
complete tar members          0
```

Legacy V87 prefix полностью совпадает с началом V87b part 000. Это подтверждает принадлежность к одному XZ stream, но не восстанавливает отсутствующие bytes. Составной поток не имеет XZ footer и падает с `Corrupt input data`; извлечь полный tar member с ожидаемым hash невозможно.

## Разрешённые следующие действия

1. Найти внешний retained artifact или backup, для которого exact bytes совпадут со всеми закреплёнными SHA-256.
2. После получения файла независимо проверить hash, размер и provenance chain, затем только добавить его в canonical tree.
3. Если exact artifacts потеряны окончательно, реализовать восстановленную логику под **новым strategy id**, добавить migration record и отдельные regression fixtures.

## Запрещённые подмены

- восстанавливать missing engine по текстовому описанию и отмечать как exact V75;
- заменять equity-файл похожим или более поздним файлом;
- считать корректный prefix доказательством полного архива;
- ослаблять pinned SHA-256;
- объявлять M2 завершённым без daily target fixture и exact regression.

## Safety

Аудит не меняет параметры стратегии, не добавляет broker/exchange submit adapter и не разрешает реальное плечо.
