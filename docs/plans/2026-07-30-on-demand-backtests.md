# Бектест по нажатию

## Результат для пользователя

Кнопка «Бектест · 2 года» не читает заранее сохранённый отчёт. Каждый клик
создаёт новый идентификатор запуска, загружает актуальные закрытые исторические
данные, прогоняет текущую реализацию выбранной стратегии с начальным капиталом
10 000 USDT и показывает рассчитанные метрики и сделки.

Execution lane: repository engineering. Изменение локально для `balkhaev/fin` и
не создаёт Hub Delivery/AgentRun.

## Контекст и границы

- Пути: `src/finruntime/observability/backtest_runner.py`,
  `src/finruntime/observability/server.py`, `frontend/live.{html,js,css}` и
  соответствующие runtime/frontend tests.
- Hub context resolver был запущен для точных FIN-путей, но классифицировал их
  как внешний `repo-governance` scope. Codebase Memory MCP недоступен; применён
  bounded file fallback только по FIN backtest API, четырём strategy engines и
  тестам.
- Основной вектор: честное измерение paper-стратегий. Прошлая доходность не
  переносится между strategy identity и не считается прогнозом.

## Инварианты

- Бектест никогда не меняет paper-ledger и не отправляет биржевые заявки.
- Клиент не передаёт настройки: период, капитал, комиссии и текущая strategy
  identity задаются серверным кодом.
- Каждый POST выполняет новый расчёт; UI не кэширует результат между кликами.
- DYN-IV113 и Atlas NX R1 используют их текущие production engine-функции и
  закрытые Binance Spot 1d candles с warm-up до начала двухлетнего окна.
- Funding Neutral и Consensus WIF + DOT не получают приближённый OHLC-only
  результат. Их preflight блокируется, пока нет обязательной двухлетней истории
  predicted funding/orderbook/OI/basis. Binance REST документирует только 30 дней
  для basis/OI statistics, а Bybit orderbook endpoint отдаёт текущий snapshot.
- Один процесс допускает не более одного тяжёлого backtest одновременно.

## Проверяемые гарантии

1. `POST /api/v1/backtests/{strategy_id}` без body запускает новый расчёт и
   возвращает `execution.status=completed`, уникальный `run_id`, точное окно,
   input SHA-256, метрики и сделки для DYN/Atlas.
2. Повторный POST имеет другой `run_id` и заново вызывает history loader.
3. POST с настройками/body отклоняется; неизвестная стратегия даёт 404; второй
   конкурентный запуск даёт 409.
4. Funding/Consensus возвращают `blocked_missing_inputs`, null metrics и точные
   blockers, не синтетический результат.
5. UI использует POST/no-store, показывает стадию реального расчёта, run id,
   фактическое data-as-of и не содержит backtest cache.
6. GET сохраняет прежний immutable evidence report только как совместимый
   справочный endpoint; кнопка его больше не использует.

## Последовательность

1. Добавить RED-тесты on-demand runner, POST-контракта и UI fetch semantics.
2. Реализовать bounded historical loader, текущие DYN/Atlas replay, trade episode
   reconstruction и метрики.
3. Подключить POST с single-flight guard и безопасными ошибками.
4. Обновить модалку и документацию, затем провести review/verification.
5. Commit/push main, дождаться Linux CI/Coolify и выполнить production smoke.

## Риски и восстановление

- Публичный расчёт делает десятки market-data запросов: single-flight ограничивает
  CPU/network fan-out; timeout и неполные данные завершаются fail-closed.
- Delisted symbols допустимы только если стратегия сохраняет свой minimum-assets
  contract; недостаточное покрытие блокирует результат.
- Rollback: вернуть предыдущий commit, где UI читает immutable GET report.
- Readback: exact deployment SHA, `/api/v1/health`, два последовательных DYN POST,
  Atlas POST, blocked Funding POST, WebSocket и browser modal.

## Evidence

- RED: `PYTHONPATH=src python -m unittest tests.runtime.test_backtests` упал
  ровно на `ModuleNotFoundError: finruntime.observability.backtest_runner` до
  добавления production-кода.
- GREEN: 19 изменённых runtime/frontend tests проходят; `ruff check` и
  `ruff format --check` проходят; `node --check frontend/live.js` проходит;
  `git diff --check` проходит. Accounting assertion подтверждает, что сумма
  `net_pnl_usd` всех закрытых и открытых trade episode совпадает с изменением NAV.
- Real-data readback 2026-07-30:
  - DYN-IV113: 2024-07-29—2026-07-29, 731 observations, 56 episodes,
    `$10,000 → $58,760.73`, CAGR `142.549%`, Max DD `-31.092%`, 30 Binance
    requests, один ожидаемый unavailable `BTTUSDT`;
  - Atlas NX R1: 731 observations, 52 episodes, `$10,000 → $7,465.75`, CAGR
    `-13.604%`, Max DD `-49.388%`, 17 Binance requests. Порог 50% честно не
    пройден и predecessor-метрика не унаследована.
- Full local runtime discovery: 160 tests запущены; 130 проходят, 29 падают на
  заранее известной Windows-несовместимости POSIX locks/fsync, один raw-byte
  provenance hash расходится из-за CRLF checkout. Эти файлы не менялись;
  Linux CI остаётся обязательной проверкой.
- Frontend discovery: 11/11 проходят. Локальный Docker smoke недоступен, потому
  что Docker Desktop daemon не запущен; container build/readback выполняется в
  CI/Coolify.
- Review:
  - CRITICAL: none.
  - HIGH: none.
  - MEDIUM: none.
  - LOW: none.
  - Accepted risks: публичный Binance endpoint может временно быть недоступен;
    single-flight действует на один процесс; factor-стратегии намеренно
    fail-closed до появления полного исторического архива.
  - Required fixes: none.
  - Verification gaps: none for the released scope.
- Release/readback:
  - commit `d1c74b47c66a84c9cec16b6f2226ff56c6564bf6` pushed to `main`;
  - GitHub Actions runs `30538810276` (frontend/control-plane) and `30538810317`
    (runtime contracts) completed successfully;
  - Coolify deployment `c1481xbb83z9rg4uxd6xu9hh` finished with rolling update
    and container healthcheck on the exact commit;
  - production `/api/v1/health`: `healthy`, transport `websocket`,
    `exchange_submission_available=false`, `live_ready=false`;
  - browser click produced DYN run `61240dee…`; a second click produced the new
    run `574dfc0b…`, both with 56 episodes and the same reproducible input/result;
  - production Atlas POST produced run `cfa99fe9…`, 52 episodes, CAGR `-13.604%`;
    Funding POST returned `blocked_missing_inputs`; settings body returned 400;
    order POST remained 405;
  - UI modal visually checked: loading state, metrics, NAV, scrollable trades and
    provenance render correctly; WebSocket populated four `$10,000` paper cards;
  - legacy Coolify apps `fin2` and `trader` remain `exited:unhealthy` (stopped).
- Memory: этот план является durable handoff; отдельный Hub changelog не нужен,
  потому что Hub runtime/authority не меняются.
