# FIN Paper Scheduler

`fin-paper-scheduler` — однописательский fail-closed сервис, который превращает заранее запечатанные входные данные в последовательные paper-cycle artifacts.

Он **не получает рыночные данные сам**, не вычисляет приближённый V75 target и не содержит exchange API. Единственный допустимый вход — полностью сформированный и хешированный request envelope.

## Поток данных

```text
external sealed producer
        ↓
fin-paper-scheduler enqueue
        ↓
runtime/.scheduler/inbox/<request-id>.json
        ↓
single-writer validation and account-hash check
        ↓
existing deterministic paper-cycle engine
        ↓
account_state / events.jsonl / forward_telemetry.csv
        ↓
FIN Runtime Control Plane
```

## Envelope

Каждый request содержит:

- `MarketSnapshot`;
- `StrategySnapshot`;
- paper quotes;
- reference prices;
- critical/on-chain source lists;
- modelled cost и slippage assumptions;
- exact `expected_account_hash` и `expected_account_sequence`;
- `not_before_utc` и `expires_at_utc`;
- canonical `request_id` SHA-256.

Изменение любого поля меняет `request_id`. Request с другим account hash не может быть применён к текущему runtime.

## Инициализация account

```bash
finruntime init-account \
  --root runtime \
  --strategy v75_atlas_nx \
  --as-of-utc 2026-07-29T00:00:00Z \
  --starting-cash 10000
```

## Постановка request в очередь

```bash
fin-paper-scheduler enqueue \
  --runtime-root runtime \
  --market-snapshot sealed/market_snapshot.json \
  --strategy-snapshot sealed/strategy_snapshot.json \
  --quotes sealed/quotes.json \
  --reference-prices sealed/reference_prices.json \
  --critical-source spot_daily \
  --critical-source perp_daily \
  --modelled-cost 12.5 \
  --modelled-slippage-bps 8 \
  --ttl-seconds 86400
```

Producer обязан завершить запись всех входных файлов до вызова `enqueue`. Scheduler сам создаёт canonical envelope атомарно.

## Один проход

```bash
fin-paper-scheduler run-once \
  --runtime-root runtime \
  --max-items 10
```

## Непрерывный daemon

```bash
fin-paper-scheduler daemon \
  --runtime-root runtime \
  --poll-seconds 5 \
  --max-items-per-pass 10
```

Одновременно допускается только один daemon. Дополнительный процесс не получит `.daemon.lock` и завершится без мутации runtime.

## Статус и проверка

```bash
fin-paper-scheduler status --runtime-root runtime
fin-paper-scheduler verify --runtime-root runtime
```

Control Plane автоматически читает:

```text
runtime/.scheduler/status.json
runtime/.scheduler/events.jsonl
```

и показывает queue depth, heartbeat, completed/rejected requests и последние ошибки.

## Каталоги

```text
runtime/.scheduler/
├── inbox/
├── processing/
├── completed/
├── rejected/
├── status.json
├── events.jsonl
├── .scheduler.lock
└── .daemon.lock
```

- `completed/*.request.json` и `completed/*.result.json` — immutable success/halt evidence;
- `rejected/*.request.json` и `rejected/*.error.json` — immutable rejected evidence;
- истёкший, повреждённый или divergent request никогда не удаляется молча.

## Fail-closed правила

Request не создаёт новую позицию, если:

- source hash не совпадает;
- critical source stale/missing/invalid;
- expected account hash не совпадает;
- envelope hash повреждён;
- request истёк;
- journal или committed artifact не проходит проверку;
- другой writer удерживает account lock.

Source-hash mismatch теперь передаётся непосредственно в pre-trade risk layer и разрешает только reductions/closures. Stale quality автоматически отражается в forward telemetry даже при ошибочном caller-флаге.

## Идемпотентность и recovery

- cycle identity включает starting account hash и sequence;
- per-cycle JSON artifacts write-once;
- journal batch добавляется под process lock одним fsync;
- singleton events нельзя переписать другим payload;
- повтор request восстанавливает только exact committed result;
- divergent global account state не перезаписывается;
- более старый cycle не может откатить более новый account.

## Safety boundary

```text
exchange_submission_available = false
live_ready                    = false
real_leverage_authorized      = false
capital_changes_permitted     = false
```

Scheduler автоматизирует только paper/shadow evidence. Он не устраняет блокеры exact V75 producer, position-level margin replay, frozen forward acceptance и testnet exchange adapter.
