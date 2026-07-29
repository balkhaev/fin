# FIN

Воспроизводимая исследовательская и paper/shadow платформа для торговых стратегий.

Репозиторий объединяет:

- канонический research ledger и отрицательные результаты;
- V75 ATLAS-NX benchmark и V136 execution shadow;
- V517 tri-state risk-budget profile;
- deterministic planner, paper broker, accounting, funding и reconciliation;
- append-only hash-chain journal, single-writer locks и write-once paper cycles;
- V413/V421 market-state observatory, drift и state memory;
- state-conditioned forward telemetry и mechanism validator;
- sealed continuous paper scheduler;
- FIN Strategy Hub с автоматическим runtime monitoring.

Strategy Hub сводит в один интерфейс рабочие контуры трёх репозиториев:

- `fin`: Funding Neutral и V75 Atlas NX;
- `trader`: Consensus WIF + DOT, портированный в отдельный public-data paper-ledger;
- `fin2`: DYN-IV113 из его текущего forward-paper API.

У каждой стратегии отдельный капитал, позиции и PnL. Верхняя сумма — только
операторская сводка; деньги между ledger не смешиваются. Все market data
реальные, но exchange submission отсутствует.

## Текущий исторический engineering target

V517/V524 показывает на известной истории около `50.55% CAGR`, Sharpe `1.460` и Max DD `-23.68%`. Это не pristine OOS и не обещание будущей доходности. Position-level margin replay, frozen forward acceptance и exchange adapter отсутствуют, поэтому:

```text
live_ready                 false
real_leverage_authorized   false
exchange submission        unavailable
```

## Быстрый запуск Control Room

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python scripts/build_frontend_data.py
fin-control-room --runtime-root runtime --open-browser
```

Операторский экран откроется по `http://127.0.0.1:8000/live.html`; исторический research dashboard остаётся на `/index.html`.

Запуск scheduler:

```bash
fin-paper-scheduler daemon --runtime-root runtime
```

## Docker / Coolify paper mode

The root container initializes a persistent `v75_atlas_nx` paper account, runs
the sealed paper scheduler, Funding Neutral, the WIF/DOT paper worker and
serves Strategy Hub on port `8000`:

```bash
docker build -t fin-paper .
docker run --rm -p 8000:8000 -v fin-runtime:/data/runtime fin-paper
```

The scheduler intentionally does not invent market or target snapshots. Exact
paper cycles still require sealed producer inputs. The repository's standalone
public-market paper engine is `services/funding_router`; its Docker image starts
in `paper` mode, uses no exchange credentials and persists its SQLite state in
`/app/data`.

Control Room автоматически читает:

```text
runtime/<strategy-id>/forward_telemetry.csv
runtime/<strategy-id>/events.jsonl
runtime/<strategy-id>/account_state.json
runtime/<strategy-id>/cycles/*/COMMITTED.json
```

API полностью read-only:

```text
GET /api/v1/dashboard
GET /api/v1/runtime
GET /api/v1/incidents
GET /api/v1/scheduler
GET /api/v1/paper
GET /api/v1/strategies
GET /api/v1/health
GET /api/v1/events
```

POST/order endpoints отсутствуют.

## Paper operations

```bash
python -m finruntime init-account --help
python -m finruntime paper-cycle --help
fin-paper-scheduler enqueue --help
fin-paper-scheduler run-once --help
fin-paper-scheduler daemon --help
python -m finruntime status --help
python -m finruntime verify-journal --help
```

## Проверка readiness

```bash
python scripts/live_preflight.py --mode shadow
python scripts/live_preflight.py --mode live
```

Shadow preflight должен пройти. Live preflight обязан fail-closed до появления всех внешних доказательств.

## Исследовательские правила

- completed information → next available open;
- overnight move старой позиции учитывается до ребалансировки;
- costs, slippage, funding, delisting и collateral учитываются явно;
- selection proof фиксируется до final;
- пороги не ослабляются после просмотра;
- отрицательные результаты не удаляются;
- missing data не заменяется нулём;
- historical metric не является capital authorization.

Подробности:

- `docs/LIVE_HANDOFF_RU.md`;
- `docs/CONTROL_ROOM_RUNTIME_RU.md`;
- `docs/PAPER_SCHEDULER_RU.md`;
- `docs/checkpoints/runtime-v1/OPERATIONS_RUNBOOK_RU.md`;
- `frontend/README.md`.
