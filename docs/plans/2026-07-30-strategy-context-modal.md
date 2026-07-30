# Модальное описание paper-стратегий

## Результат

Нажатие на любую карточку в блоке «Почему стратегии ждут» открывает понятное
полное описание стратегии на русском языке. Текущая причина ожидания и метрики
продолжают обновляться из WebSocket без перезагрузки страницы.

## Контекст и границы

- Execution lane: repository engineering.
- Backend: `src/finruntime/observability/strategy_hub.py`.
- Frontend: `frontend/live.html`, `frontend/live.js`, `frontend/live.css`.
- Tests: `tests/runtime/test_strategy_hub.py`, `tests/frontend/test_static_assets.py`.
- Реальные ордера, paper-ledgers и торговая логика не меняются.
- Структурный codebase-memory MCP в текущей сессии недоступен; выполнен точечный
  поиск по владельцу `context` и месту его отображения.

## Проверяемые гарантии

1. Каждая стратегия содержит `context.full_description` с резюме, шагами,
   условиями входа/выхода, риск-контролями, источниками данных и текущим статусом.
2. Карточка контекста открывается мышью, Enter и Space.
3. Используется нативный `<dialog>` с понятным заголовком, кнопкой закрытия,
   закрытием по Escape/фону и возвратом фокуса.
4. Открытая модалка получает свежий `current_state` при следующих WebSocket-снимках.
5. На мобильном экране модалка остаётся читаемой и прокручивается внутри viewport.
6. Exchange submission остаётся недоступным.

## Риски и rollout

- Риск: WebSocket-render заменяет карточку, с которой открыта модалка. Возврат
  фокуса выполняется по стабильному `data-strategy-id`, а не по старому DOM-узлу.
- Риск: длинный текст выходит за viewport. Высота ограничена, контент прокручивается.
- Rollback: redeploy предыдущего production-коммита `9cd7741`.
- Readback: API contract, WebSocket, browser interaction, keyboard close и mobile
  layout smoke после Coolify deployment.

## Evidence

- Context: bounded file search recorded above.
- RED: contract tests correctly failed без `context.full_description` и без
  `<dialog id="strategy-dialog">`.
- GREEN: те же contract tests прошли после реализации; полный релевантный набор —
  `25 passed`.
- Review:
  - CRITICAL: нет.
  - HIGH: нет.
  - MEDIUM: исправлено ошибочное описание stale-data выхода funding-стратегии;
    фактическое поведение сохраняет paper-состояние и не создаёт новое решение.
    Production smoke также привёл к явному обработчику Escape независимо от
    нативной реализации `<dialog>`.
  - LOW: дополнительное полное описание увеличивает WebSocket snapshot, но остаётся
    небольшим относительно свечных данных и устраняет второй источник истины в UI.
  - Accepted risks: нативный `<dialog>` требует современный браузер; production
    surface уже рассчитан на современные Chromium/WebKit/Firefox.
  - Required fixes: выполнены.
  - Verification gaps: production interaction и WebSocket readback до выпуска.
- Verify: pytest, Ruff check/format, Node syntax и `git diff --check` прошли;
  production readback выполняется после выпуска.
- Release: direct-main и Coolify; SHA, CI и production smoke фиксируются в handoff.
- Memory: этот план и contract tests; отдельное изменение agent policy не требуется.
