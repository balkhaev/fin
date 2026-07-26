# Implementation handoff

Поиск кандидатов и реализация разделены на два независимых потока.

- **Другой агент реализует** замороженные стратегии и paper/shadow runtime по плану ниже.
- **Исследовательский агент** продолжает искать новые независимые источники P&L, не меняя параметры реализуемых профилей.

Канонический план:

- [`docs/implementation/MASTER_IMPLEMENTATION_PLAN_RU.md`](docs/implementation/MASTER_IMPLEMENTATION_PLAN_RU.md)
- [`docs/implementation/AGENT_HANDOFF_RU.md`](docs/implementation/AGENT_HANDOFF_RU.md)
- [`docs/implementation/ACCEPTANCE_CRITERIA_RU.md`](docs/implementation/ACCEPTANCE_CRITERIA_RU.md)
- [`docs/implementation/RUNTIME_CONTRACTS_RU.md`](docs/implementation/RUNTIME_CONTRACTS_RU.md)
- [`docs/implementation/IMPLEMENTATION_PLAN.json`](docs/implementation/IMPLEMENTATION_PLAN.json)

## Замороженная очередь реализации

1. `v75_atlas_nx` — основной paper/shadow профиль.
2. `v28_growth_control` — обязательный контрольный профиль.
3. `v136_execution_shadow` — shadow-only execution filter поверх V75.
4. `services/funding_router` — отдельный market-neutral сервис, не часть V75 и не источник доказанной доходности.

## Неизменяемая граница безопасности

```text
live_execution_available = false
live_ready = false
real_leverage_authorized = false
```

До прохождения paper-forward критериев код не должен содержать автоматическое разрешение live-торговли или реального плеча.
