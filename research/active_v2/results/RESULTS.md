# Active research v2 — результаты

## Вердикт

- `rotation_ensemble`: **rejected_or_needs_iteration**.
- `shock_reversal`: **отклонён**.
- Разрешение на live trading: **нет**.

## Методология

- Binance Spot, BTCUSDT и ETHUSDT, 15 минут.
- История: 2020-01-01 — 2026-07-01.
- Development: 2020–2022.
- Validation: 2023–2024.
- Research holdout: 2025-01-01 — 2026-07-01.
- Параметры выбирались только по development + validation.
- Расходы: low 5 б.п., base 10 б.п., stress 20 б.п. на сторону.
- Holdout не считается полностью нетронутым, поскольку режим 2025–2026 уже наблюдался в AIMR v1.

## Rotation ensemble

| Сценарий | Период | Доходность | Годовая | Max DD | Sharpe | Оборот |
|---|---|---:|---:|---:|---:|---:|
| base | validation | +156,30% | +60,01% | −18,63% | 1,82 | 241,43x |
| stress | validation | +101,28% | +41,82% | −24,45% | 1,39 | 241,43x |
| low | research holdout | +16,20% | +10,56% | −22,57% | 0,58 | 149,36x |
| base | research holdout | **+7,83%** | +5,17% | −24,98% | 0,34 | 149,36x |
| stress | research holdout | **−6,73%** | −4,55% | −30,14% | −0,13 | 140,69x |

Сигнал как класс выглядит сильнее AIMR v1, но запас преимущества слишком мал относительно оборота. При удвоении расходов holdout становится отрицательным. Поэтому V3 должен уменьшить частоту ребалансировки и число переключений на порядок, а не поднимать риск.

## Shock reversal

Ни одна из 768 конфигураций не прошла минимальные критерии одновременно на development и validation. Для выбранного диагностического варианта:

| Сценарий | Период | Доходность | Max DD | Сделки | Profit factor |
|---|---|---:|---:|---:|---:|
| base | validation | −4,91% | −5,39% | 41 | 0,31 |
| base | research holdout | −0,93% | −2,14% | 12 | 0,57 |
| stress | research holdout | −2,33% | −2,84% | 12 | 0,21 |

Shock-модуль закрыт. Дальнейшая оптимизация этой семьи по уже просмотренным данным запрещена.

## Происхождение

- Compute repository: `balkhaev/trader`, draft PR #5.
- Workflow run: `30022908924`.
- Head SHA: `3a2bc04cf90960c60eb195aa606db50b40773d4f`.
- Artifact ID: `8570557080`.
- Artifact SHA-256: `1aacca9416f545499bce3b6d77cea3de19318c32abbb4c2bfe5c7fbf8badd262`.
- Канонический код исследования находится в `research/active_v2` этого репозитория.

## Следующее решение

V3 проверяет только cost-aware low-turnover trend/rotation:

- дневные завершённые сигналы;
- ребалансировка раз в 3–14 дней;
- minimum hold и cooldown;
- отдельный порог входа и более строгий порог переключения BTC↔ETH;
- 10–20% целевая волатильность без плеча;
- отбор по худшему результату base/stress на development и validation;
- rolling walk-forward и фиксированный paper-forward candidate.
