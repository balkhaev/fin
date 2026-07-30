# Двухлетний бектест стратегий

## Контекст

Нужно проверить доказательства доходности всех четырёх paper-стратегий и добавить в live-интерфейс одну кнопку без настроек, которая показывает сделки за последние два года.

Репозиторный context resolver был запущен из Hub для путей FIN, но эти пути находятся вне графа `D:/code/hub`, поэтому возвращённый Hub-пакет к FIN не относится. Codebase Memory MCP в текущем окружении недоступен. Для bounded fallback прочитаны только канонические реализации и документы `fin`, `trader` и `fin2`.

## Проверенные границы доказательств

- `dyn-iv113`: в `fin2` есть checksum-верифицируемый frozen OOS ledger на 69 trade episodes, 2443 order legs и период 2024-01-01—2026-07-26. Полный OOS CAGR равен 112.638%. Это исторический model account, а не текущий paper-счёт и не прогноз.
- `funding-neutral`: runtime работает в paper, но его контракт прямо содержит `profitability_proven=false`. Точный двухлетний replay требует исторических predicted funding, стаканов, OI и basis.
- `consensus-wif-dot`: paper-движок реализован, но в `trader` нет зафиксированных входных CSV и backtest output, достаточных для воспроизводимого двухлетнего результата.
- `atlas-nx`: активный Atlas NX R1 — новая reconstructed identity. По migration contract он не может наследовать метрики V517/V524; exact V75 target producer отсутствует.

## Контракт

- Read-only `GET /api/v1/backtests/{strategy_id}`.
- Единая схема для всех четырёх стратегий.
- `verified` разрешён только при совпадении strategy identity и проверенном provenance.
- Сделки DYN показываются за фиксированное двухлетнее окно до даты frozen snapshot.
- Для остальных стратегий ответ `insufficient_evidence` объясняет конкретные blockers и не рисует фиктивные CAGR/сделки.
- Исторические метрики всегда отделены от текущего paper-ledger и сопровождаются предупреждением «не прогноз».

## Реализация и проверка

1. RED-тесты каталога и HTTP API, включая запрет наследования Atlas.
2. Импорт checksum-проверяемого DYN ledger как package data и нормализация trade episodes.
3. Кнопка «Бектест · 2 года» у выбранной стратегии, loading/error/verified/insufficient states, метрики и таблица сделок.
4. Unit/integration/static tests, compile/lint и ручной browser smoke.
5. Commit/push `main`, проверка CI/Coolify и production API/UI; `fin2` и `trader` остаются остановлены.

## Риски

- Двухлетнее окно DYN показывает пересекающиеся с окном trade episodes; headline CAGR/Sharpe/MDD относятся к полному immutable OOS окну 2.565 года и поэтому явно подписываются отдельно.
- Нельзя превращать отсутствие доказательств в нулевую доходность: `insufficient_evidence` означает «не доказано», а не «не работает».

## Review gate

CRITICAL: none.

HIGH: none.

MEDIUM: none. CAGR и Total DYN вычисляются из checksum-проверенных start/end NAV и дат manifest; Atlas predecessor metric не может пройти active-identity gate.

LOW: В таблице выводятся все 53 trade episode без пагинации. Объём мал и ограничен immutable двухлетним окном.

Accepted risks: Sharpe и Max DD берутся из frozen strategy monitor snapshot `fin2`, поскольку trade-episode archive не содержит дневную equity curve. UI явно отделяет полный OOS metrics scope от двухлетней таблицы сделок.

Required fixes: none.

Verification gaps: Полный POSIX runtime-suite нельзя валидно выполнить на Windows из-за fail-closed `fcntl` locking; его должен повторить Linux CI/production container. Целевые strategy/API/frontend tests и browser smoke прошли локально.
