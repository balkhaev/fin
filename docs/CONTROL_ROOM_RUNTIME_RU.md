# FIN Control Room — локальный paper/shadow control plane

## Назначение

Control Room превращает committed research dashboard в локальное read-only приложение, которое автоматически наблюдает за runtime-каталогом.

```text
sealed scheduler inbox
        ↓
deterministic paper-cycle artifacts
        ↓
strict telemetry / journal parser
        ↓
runtime health + incident timeline
        ↓
read-only HTTP API + SSE
        ↓
FIN Control Room frontend
```

Слой не генерирует targets, не изменяет портфель и не предоставляет exchange submission.

## Запуск

```bash
python -m pip install -e .
python scripts/build_frontend_data.py
fin-control-room \
  --runtime-root /var/lib/fin/runtime \
  --host 127.0.0.1 \
  --port 8000
```

Проверка snapshot без запуска HTTP server:

```bash
fin-control-room \
  --runtime-root /var/lib/fin/runtime \
  --snapshot > /tmp/control-room-snapshot.json
```

## Runtime layout

Для каждой стратегии ожидается существующий operations layout:

```text
<runtime-root>/<strategy-id>/
├── account_state.json
├── events.jsonl
├── forward_telemetry.csv
└── cycles/
    └── <cycle-id>/
        ├── COMMITTED.json
        ├── reconciliation.json
        ├── forward_telemetry.json
        └── ...
```

Scheduler context:

```text
<runtime-root>/.scheduler/status.json
<runtime-root>/.scheduler/events.jsonl
```

Optional strategy/market context:

```text
<runtime-root>/v517_state.json
<runtime-root>/v517_decision.json
<runtime-root>/market_state.json
```

## API

| Endpoint | Содержимое |
|---|---|
| `GET /api/v1/dashboard` | historical dashboard + live runtime overlay |
| `GET /api/v1/runtime` | account/cycle/telemetry summary |
| `GET /api/v1/incidents` | latest integrity/execution incidents |
| `GET /api/v1/scheduler` | queue, heartbeat и last result scheduler |
| `GET /api/v1/health` | health и uptime server |
| `GET /api/v1/events` | Server-Sent Events при изменении snapshot |
| `WS /api/v1/ws` | realtime paper/strategy snapshots и heartbeat без polling |

Любой `POST` возвращает `405`. Orders/mutation routes отсутствуют.

## Health semantics

```text
healthy   latest row clean, journal valid
warn      incomplete execution, stale feed, slippage >1.5x model
halt      reconciliation/hash/stale-row failure, corrupt journal/artifact
idle      runtime root exists, но observations отсутствуют
```

Historical incidents сохраняются в timeline. Текущий status определяется latest evidence и целостностью источников.

## Fail-closed rules

- неожиданная telemetry schema → `halt`;
- duplicate `(timestamp, strategy_id)` → `halt`;
- journal hash-chain break → `halt`;
- invalid account/cycle JSON → `halt`;
- missing runtime root → `idle`, не `healthy`;
- no runtime market-state → archived V413 остаётся единственным контекстом;
- corrupt/stale scheduler heartbeat → `warn` или `halt`;
- rejected scheduler request остаётся immutable evidence;
- UI/API никогда не меняют `live_ready`, `real_leverage_authorized` или exchange submission на `true`.

## Сетевой доступ

По умолчанию server слушает `127.0.0.1`. Для `0.0.0.0` требуется `--allow-remote`; это лишь защита от случайного exposure, а не authentication layer. Для удалённого доступа используйте отдельный reverse proxy с TLS и authentication.

## Непрерывное paper-исполнение

```bash
fin-paper-scheduler daemon \
  --runtime-root /var/lib/fin/runtime \
  --poll-seconds 5
```

Подробный spool/envelope contract: `docs/PAPER_SCHEDULER_RU.md`. Control Room не управляет scheduler и не предоставляет mutation endpoint — он только читает его status и journal.
