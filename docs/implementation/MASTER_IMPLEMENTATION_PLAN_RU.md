# Master implementation plan — frozen strategy runtime

Дата фиксации: 2026-07-26.

Этот документ является постановкой задачи для отдельного implementation-агента. Он **не разрешает менять исследовательские параметры**, подбирать новые thresholds или объявлять историческую модель готовой к live-торговле.

## 1. Цель

Построить воспроизводимый paper/shadow runtime для уже выбранных профилей:

| Профиль | Роль | Разрешённый режим |
|---|---|---|
| `v75_atlas_nx` | Основная комплексная стратегия | paper + shadow |
| `v28_growth_control` | Обязательная контрольная стратегия | paper + shadow |
| `v136_execution_shadow` | Экспериментальный execution-filter поверх V75 | только shadow |
| `funding_router` | Отдельный delta-neutral сервис | scan + paper |

`funding_router` не смешивается с V75, не получает долю капитала автоматически и ведёт отдельный журнал P&L.

## 2. Неподлежащие обсуждению ограничения

```text
live_execution_available = false
live_ready = false
real_leverage_authorized = false
```

Implementation-агент не должен:

- добавлять broker/exchange submit adapter в primary runtime;
- включать live по environment flag без отдельного нового checkpoint;
- менять frozen-параметры V75, V28 или V136;
- оптимизировать стратегию на новых исторических данных;
- скрывать пропущенные или устаревшие данные через forward-fill без explicit availability policy;
- считать нулевую доходность при нулевой экспозиции подтверждением edge;
- объединять `funding_router` с V75 в одну equity curve до самостоятельного paper-proof.

## 3. Источники истины в репозитории

Перед кодированием агент обязан найти и записать SHA-256/commit SHA следующих evidence layers:

1. Последний checkpoint V75/V136: `docs/checkpoints/v138/` и `research/active_v131_v138/`.
2. Канонический ATLAS/V75 source и inputs в research-пути V69–V76/V87.
3. V28 source/config/result evidence в research-пути V26–V28.
4. Последний общий handoff/checkpoint, доступный в `docs/checkpoints/`.
5. `services/funding_router/STATUS.json`, `config.example.toml`, tests и README.

Если одинаковый параметр встречается в нескольких старых отчётах, приоритет:

```text
последний merged checkpoint
→ exact machine-readable frozen config
→ exact source, который воспроизводит checkpoint
→ текстовый отчёт
→ старый PR body
```

Все использованные source paths и hashes записать в:

```text
docs/checkpoints/runtime-v1/SOURCE_REGISTRY.json
```

## 4. Целевая структура

```text
src/finruntime/
├── __init__.py
├── __main__.py
├── cli.py
├── models.py
├── registry.py
├── profiles/
│   ├── v75.py
│   ├── v28.py
│   └── v136.py
├── data/
│   ├── contracts.py
│   ├── validation.py
│   ├── availability.py
│   └── adapters/
│       ├── binance_spot.py
│       ├── binance_perp.py
│       ├── onchain.py
│       └── cash_rate.py
├── portfolio/
│   ├── state.py
│   ├── accounting.py
│   ├── risk.py
│   └── reconciliation.py
├── execution/
│   ├── planner.py
│   ├── v136_filter.py
│   ├── paper_broker.py
│   └── fills.py
├── journal/
│   ├── atomic.py
│   └── sqlite.py
└── monitoring/
    ├── checks.py
    └── report.py

config/strategies/
├── v75_atlas_nx.json
├── v28_growth_control.json
└── v136_execution_shadow.json

schemas/runtime/
├── market_snapshot.schema.json
├── strategy_snapshot.schema.json
├── portfolio_state.schema.json
├── execution_plan.schema.json
├── fill_event.schema.json
└── reconciliation_report.schema.json

scripts/
├── run_paper_cycle.py
├── backfill_market_snapshot.py
├── reconcile_paper.py
└── verify_runtime.py

tests/runtime/
├── test_contracts.py
├── test_availability.py
├── test_v75_regression.py
├── test_v28_regression.py
├── test_v136_execution.py
├── test_risk_limits.py
├── test_planner_idempotency.py
├── test_journal_recovery.py
├── test_paper_fills.py
└── test_no_live.py
```

## 5. Milestones

### M0 — repository and provenance lock

Deliverables:

- закрыть или пометить superseded PR №34;
- создать новую implementation-ветку от актуального `main`;
- создать `SOURCE_REGISTRY.json`;
- записать exact hashes frozen configs, research source и контрольных outputs;
- запретить использование opaque ZIP/base64 transport в canonical runtime paths.

Acceptance:

```text
python scripts/verify_runtime.py --provenance-only
```

должен падать при изменении любого frozen source hash без отдельного migration record.

### M1 — data contracts и availability

Создать единый `MarketSnapshot` со следующими блоками:

```text
as_of_utc
available_at_utc
source_timestamp_utc
spot_daily
perp_8h
funding_events
onchain_features
cash_rate
source_hashes
quality_flags
```

Обязательные правила:

- UTC везде;
- сигнал использует только данные с `available_at_utc <= decision_time`;
- completed daily candle не доступна раньше своего close;
- next-open execution рассчитывается отдельно от signal timestamp;
- on-chain snapshot имеет собственный publication timestamp, не только observation date;
- stale on-chain старше 48 часов принудительно обнуляет V67 accelerator;
- отсутствующий funding event не заменяется нулём без explicit quality flag;
- каждый raw payload имеет SHA-256.

Начальные adapters:

- spot daily OHLCV для frozen crypto universe;
- BTC/ETH perpetual 8h OHLC и funding schedule;
- frozen on-chain feature snapshot;
- cash-rate observation.

Acceptance:

- malformed timezone rejected;
- duplicate timestamp rejected;
- future-available observation rejected;
- stale on-chain sets accelerator permission to zero;
- same raw inputs produce same snapshot hash.

### M2 — exact V75 signal/target engine

Не переписывать логику «по памяти». Вынести из canonical research source и сохранить математическую идентичность:

- V27 execution-aware core;
- V4 defensive allocation;
- V67 on-chain spot/perpetual accelerator;
- unified net target book;
- prior-day high-water feedback;
- irreversible risk ratchet;
- volatility throttle;
- cash, gross и margin constraints.

Frozen schedules:

```text
high-water thresholds:
1.75x → 2.50x
2.00x → 3.00x
2.00x → 4.00x

defensive V4 weights by stage:
0% → 10% → 20%

maximum on-chain accelerator:
35% → 30% → 25%

gross cap:
1.10x → 1.05x → 1.00x

volatility multiplier:
vol <= 25%        1.00
25% < vol <= 35%  0.75
vol > 35%         0.50
```

Risk reserves:

```text
initial margin reserve       25%
maintenance margin           10%
operational reserve          20%
additional accelerator cash  2% per unit scale
```

Acceptance:

- deterministic replay на committed research inputs;
- daily target weights совпадают с canonical V75 на всём доступном regression window;
- допускается только численная погрешность, заранее указанная в тесте;
- high-water stage никогда не уменьшается;
- изменение будущей строки не меняет прошлые targets.

### M3 — V28 control

V28 — отдельный контроль, не fallback-подмена V75.

Требования:

- собственный strategy id;
- собственный state и journal;
- прямое воспроизведение frozen target book;
- никакого заимствования high-water состояния V75;
- одинаковый data snapshot, но отдельный plan hash.

Acceptance:

- regression against committed V28 weights/equity;
- независимость state files;
- одновременный запуск V75/V28 не создаёт общую позицию или общий cash ledger.

### M4 — V136 execution shadow

Frozen parameters:

```text
L1 no-trade band         0.08
maximum target age       28 days
step fraction            1.00
risk reduction buffer    0.02
```

Правила:

- уменьшение gross исполняется немедленно;
- target zero закрывает позицию немедленно;
- perpetual sign flip разбивается на `reduceOnly close` и отдельный open intent;
- увеличивающие риск изменения могут быть отложены band/age;
- V136 не влияет на V75 primary plan;
- tracking error измеряется ежедневно.

Acceptance:

- sign-flip tests;
- immediate risk-reduction tests;
- max-age forced update;
- no-trade preservation;
- deterministic order sequence;
- separate journal.

### M5 — portfolio state и accounting

`PortfolioState` минимум:

```text
strategy_id
as_of_utc
cash
spot_positions
perp_positions
entry_marks
high_water
ratchet_stages
held_targets
target_age
pending_orders
last_snapshot_hash
last_plan_hash
sequence_number
```

Accounting rules:

- old position receives overnight move before rebalance;
- funding credited/debited at actual event timestamp;
- fees and slippage are separate fields;
- forced exits have separate penalty field;
- cash yield is separate, auditable flow;
- no double-counting between target drift and fills;
- journal is append-only; current state is reconstructible from events.

Acceptance:

- accounting identity every cycle;
- state recovery after interrupted write;
- no negative unexplained cash;
- duplicate event id is idempotent;
- corrupted journal fails closed.

### M6 — order planner, без submit

`ExecutionPlan` содержит intents, но не выполняет их.

Для каждого intent:

```text
strategy_id
plan_id
sequence_number
instrument
venue
market_type
side
reduce_only
quantity
quantity_unit
reference_price
max_slippage_bps
reason
parent_intent_id
expires_at_utc
input_hash
```

Planner rules:

- targets netted by instrument before order generation;
- close/reduce precedes risk-increasing order;
- sign flip split;
- missing price prevents order generation;
- stale snapshot prevents risk increase;
- plan id deterministic;
- same state + same snapshot = same plan;
- repeated planning does not duplicate pending intents.

В primary runtime отсутствует метод `submit_order`.

### M7 — paper broker

Paper broker обязан использовать записанные market observations, а не идеальную цену:

- bid/ask when available;
- otherwise explicit spread proxy;
- partial fills;
- commission schedule;
- slippage;
- funding;
- rejected/expired intents;
- delayed fill;
- market outage scenario.

Paper fill никогда не переписывает исходный plan. Он создаёт отдельный `FillEvent`.

### M8 — reconciliation и monitoring

Каждый цикл формирует:

```text
model target
planned target
paper-filled target
tracking error
realized fees
modelled fees
slippage
funding
stale-data flags
risk-limit utilization
margin buffer
```

Alert levels:

- `INFO`: normal drift;
- `WARN`: tracking error или stale non-critical source;
- `HALT`: invalid timestamp, corrupted state, missing critical price, gross breach, margin breach, unexplained position.

HALT означает только отсутствие нового risk-increasing plan.

### M9 — scheduler и operations

Минимальные команды:

```bash
python -m finruntime registry
python -m finruntime validate-snapshot snapshot.json
python -m finruntime plan --strategy v75_atlas_nx --snapshot snapshot.json
python -m finruntime paper-cycle --strategy v75_atlas_nx
python -m finruntime reconcile --strategy v75_atlas_nx
python -m finruntime status
python -m finruntime self-test
```

Operational schedule:

- market ingestion after completed UTC day;
- three 8h funding/perp checkpoints;
- on-chain availability poll;
- plan only after all mandatory sources are sealed;
- atomic write before external notification;
- no automatic live submission.

### M10 — frozen forward protocol

Обязательные параллельные profiles:

```text
primary:  v75_atlas_nx
control:  v28_growth_control
shadow:   v136_execution_shadow
separate: funding_router paper
```

Не менее:

- 180 календарных дней;
- 30 существенных target changes V75;
- 30 закрытых funding-router paper trades для его собственного решения;
- хотя бы один ненулевой V67 accelerator regime;
- ноль необъяснимых delta mismatches;
- ноль state corruption/recovery failures;
- model-to-paper tracking error не выше 2% капитала;
- фактические fees/slippage не хуже predeclared tolerance;
- никаких ручных изменений frozen parameters.

Любое изменение signal/risk параметров создаёт новый strategy id и обнуляет forward clock.

## 6. Тестовая матрица

Минимум:

### Unit

- JSON/schema validation;
- timezones;
- target arithmetic;
- gross/margin caps;
- V136 state machine;
- plan idempotency;
- journal recovery.

### Regression

- V75 exact committed replay;
- V28 exact committed replay;
- V136 historical shadow replay;
- expected hashes of representative days.

### Causal

- mutate last future observation;
- past targets unchanged;
- publication timestamp lag respected;
- next-open only.

### Property/fuzz

- arbitrary weights cannot exceed gross cap after risk layer;
- high-water monotonic;
- sequence number monotonic;
- cash/accounting identity;
- reduce-only never increases absolute position.

### Failure injection

- missing funding;
- stale on-chain;
- missing open;
- duplicate fill;
- partial fill;
- corrupted state;
- process crash between plan and state commit;
- exchange outage in paper broker.

### Safety

- no live adapter importable from `finruntime`;
- `mode=live` always raises;
- no secret names or API keys in snapshots/plans;
- leverage authorization flag always false.

## 7. CI gates

Required workflows:

```text
runtime-unit.yml
runtime-regression.yml
runtime-causal.yml
runtime-replay.yml
runtime-safety.yml
```

Merge blocked unless:

- Python 3.11–3.13 pass;
- deterministic hashes stable;
- provenance registry valid;
- no opaque archives in canonical paths;
- no live adapter;
- all schemas/examples valid;
- V75/V28 regression exact;
- V136 shadow tests pass.

## 8. Branch/PR sequence for implementation-agent

Recommended PRs:

1. `agent/runtime-contracts` — models, schemas, provenance registry.
2. `agent/runtime-v75-engine` — exact V75 target regression.
3. `agent/runtime-v28-control` — independent V28 profile.
4. `agent/runtime-v136-shadow` — execution state machine.
5. `agent/runtime-paper-ledger` — state, planner, paper fills.
6. `agent/runtime-ops` — CLI, scheduler, monitoring, runbook.
7. `agent/runtime-forward-checkpoint` — immutable checkpoint and deployment docs.

Каждый PR должен быть небольшим, содержать читаемый код напрямую и не использовать bootstrap/base64 transport.

## 9. Definition of done для runtime-v1

Runtime-v1 считается реализованным, когда:

- новый агент клонирует `main` и запускает все проверки одной командой;
- V75/V28 targets воспроизводятся из committed fixture inputs;
- V136 создаёт отдельный shadow plan;
- paper state восстанавливается из журнала;
- plan deterministic и idempotent;
- live невозможен технически;
- implementation checkpoint хранит exact hashes;
- runbook позволяет ежедневно выполнять paper cycle без чтения старых PR или этого чата.

Runtime-v1 **не** считается доказательством прибыльности и не меняет исследовательский статус стратегий.
