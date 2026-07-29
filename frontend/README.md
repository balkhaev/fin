# FIN Control Room

Control-room приложение для research/paper/shadow контура `balkhaev/fin`.

В репозитории два экрана:

- `index.html` — committed research evidence, stress surface и live-readiness gates;
- `live.html` — локальный операторский экран с автоматическим чтением runtime artifacts.

## Живой локальный режим

```bash
python -m pip install -e .
python scripts/build_frontend_data.py
fin-paper-scheduler daemon --runtime-root runtime &
fin-control-room --runtime-root runtime --open-browser
```

Эквивалентная команда без installed entrypoint:

```bash
python scripts/run_control_room.py --runtime-root runtime --open-browser
```

Операторский экран доступен по `http://127.0.0.1:8000/live.html`. Сервер:

- отдаёт static frontend;
- предоставляет read-only API `/api/v1/dashboard`, `/api/v1/runtime`, `/api/v1/incidents`, `/api/v1/scheduler`, `/api/v1/health`;
- отправляет готовые paper/strategy snapshots по WebSocket `/api/v1/ws` без frontend polling;
- автоматически читает `<runtime-root>/<strategy-id>/forward_telemetry.csv`, `events.jsonl`, `cycles/*/COMMITTED.json` и `account_state.json`;
- показывает optional `v517_state.json`, `v517_decision.json` и `market_state.json`;
- показывает scheduler queue, heartbeat, completed/rejected requests и last error;
- не имеет POST/order endpoint и не читает API-ключи.

Связывание с non-loopback интерфейсом требует явного `--allow-remote`; аутентификация в server не реализована, поэтому по умолчанию он слушает только localhost.

## Статический режим

```bash
python scripts/build_frontend_data.py
python -m http.server 8000 --directory frontend
```

В static режиме `index.html` показывает historical evidence. Для `live.html` нужен `fin-control-room`, поскольку обычный static server не предоставляет runtime API.

Frontend не требует npm, bundler или внешних JavaScript-библиотек.

## GitHub Pages

Workflow `deploy-control-room-pages.yml` запускается вручную. Pages публикует только static evidence bundle; локальные runtime-файлы и paper accounts туда не загружаются.

## Safety boundary

Панель не создаёт ордера и не хранит API-ключи. Значение `live_ready` остаётся `false`, пока не пройдены exact target producer, position-level margin replay, frozen forward acceptance и exchange-adapter gates.
