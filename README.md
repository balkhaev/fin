# fin

Публичный репозиторий воспроизводимых исследований и безопасной реализации торговых алгоритмов.

Цель — сохранять читаемый код, frozen-параметры, происхождение данных, отрицательные результаты и paper/shadow implementation, не выдавая историческую оптимизацию за доказанную live-стратегию.

## Реализация

Поиск и implementation ведутся независимыми потоками.

**Второму implementation-агенту начинать здесь:**

1. [`IMPLEMENTATION.md`](IMPLEMENTATION.md)
2. [`docs/implementation/MASTER_IMPLEMENTATION_PLAN_RU.md`](docs/implementation/MASTER_IMPLEMENTATION_PLAN_RU.md)
3. [`docs/implementation/AGENT_HANDOFF_RU.md`](docs/implementation/AGENT_HANDOFF_RU.md)
4. [`docs/implementation/RUNTIME_CONTRACTS_RU.md`](docs/implementation/RUNTIME_CONTRACTS_RU.md)
5. [`docs/implementation/ACCEPTANCE_CRITERIA_RU.md`](docs/implementation/ACCEPTANCE_CRITERIA_RU.md)
6. [Implementation issue #36](https://github.com/balkhaev/fin/issues/36)

Замороженная очередь:

```text
primary:  v75_atlas_nx
control:  v28_growth_control
shadow:   v136_execution_shadow
separate: services/funding_router paper service
```

PR №34 superseded и не должен сливаться: реализация публикуется небольшими прямыми PR без ZIP/base64 materialization.

## Исследования

Основные поздние checkpoints:

- [`docs/checkpoints/v138/`](docs/checkpoints/v138/) — V75 и execution/regime audit;
- [`docs/checkpoints/v146/`](docs/checkpoints/v146/) — отклонённый continuous-futures proxy;
- [`docs/checkpoints/v154/`](docs/checkpoints/v154/) — официальные датированные VX-контракты;
- [`docs/checkpoints/v68/`](docs/checkpoints/v68/) — полный ранний ledger и reproof;
- [`research/active_v163_v170/`](research/active_v163_v170/) — текущий cross-venue funding/basis evidence cycle, пока не слит.

Отрицательные результаты сохраняются. Новый sleeve не интегрируется с V75, пока самостоятельно не пройдёт frozen gates.

## Правила исследования

- completed information → next available open;
- overnight move старой позиции учитывается до ребалансировки;
- costs, slippage, funding, delisting, margin и collateral учитываются явно;
- selection proof фиксируется до final;
- пороги не ослабляются после просмотра;
- ноль при нулевой экспозиции не является forward-подтверждением;
- слабый standalone sleeve нельзя скрывать внутри сильной общей equity curve;
- historical data coverage и availability являются обязательными gates.

## Граница безопасности

```text
live_execution_available = false
live_ready = false
real_leverage_authorized = false
```

В каноническом runtime до отдельного нового checkpoint не должно быть автоматической live-отправки заявок.
