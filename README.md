# FIN

Воспроизводимая исследовательская и paper/shadow платформа для торговых стратегий.

Репозиторий объединяет:

- канонический research ledger и отрицательные результаты;
- V75 ATLAS-NX benchmark и V136 execution shadow;
- V517 tri-state risk-budget profile;
- deterministic planner, paper broker, accounting, funding и reconciliation;
- append-only hash-chain journal и atomic paper cycles;
- V413/V421 market-state observatory, drift и state memory;
- state-conditioned forward telemetry и mechanism validator;
- FIN Control Room с автоматическим runtime monitoring.

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
GET /api/v1/health
GET /api/v1/events
```

POST/order endpoints отсутствуют.

## Paper operations

```bash
python -m finruntime init-account --help
python -m finruntime paper-cycle --help
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
- `docs/checkpoints/runtime-v1/OPERATIONS_RUNBOOK_RU.md`;
- `frontend/README.md`.
