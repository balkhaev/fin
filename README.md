# fin

Репозиторий воспроизводимых исследований и fail-closed paper/shadow runtime для торговых алгоритмов.

Цель — сохранять код, происхождение данных, отрицательные результаты, положительные кандидаты и операционные ограничения, не выдавая переобучение за готовую live-стратегию.

## FIN Control Room

Новый статический frontend показывает V75/V136/V509/V517, equity и drawdown, stress surface, архивное состояние рынка, runtime telemetry и все блокеры перед real-money live.

```bash
python scripts/build_frontend_data.py
python -m http.server 8000 --directory frontend
```

Откройте `http://localhost:8000`.

- [Frontend](frontend/)
- [Инструкция frontend](frontend/README.md)
- [Handoff research → shadow → live](docs/LIVE_HANDOFF_RU.md)

## Текущий стек

```text
V75 ATLAS-NX                  primary historical benchmark
V136 Execution Plateau       execution shadow
V517 Tri-state Guard         research/shadow risk-budget adapter
V413/V421                    market-state observatory and memory
V429/V445                    forward telemetry and mechanism validation
finruntime                   deterministic paper broker, journal and reconciliation
```

V517 достиг исторической engineering-цели около `50.55% CAGR`, Sharpe `1.460` и Max DD `-23.68%`, но параметры informed известной историей, pristine holdout отсутствует, position-level margin replay не завершён и forward evidence ещё нет.

## Что можно запускать сейчас

```text
paper/shadow runtime          готов
V136 execution shadow         готов
V517 risk-budget shadow       готов
state-conditioned telemetry   готов
real exchange submission      отсутствует
real leverage                 не разрешено
```

Проверка:

```bash
python -m pip install -e .
python scripts/live_preflight.py --mode shadow
```

Real-money live остаётся fail-closed до прохождения exact target producer, position-level margin/liquidation replay, минимум 180 дней frozen forward evidence и testnet-проверенного exchange adapter.

## Исследовательские правила

- completed information → next available open;
- overnight move старой позиции учитывается до ребалансировки;
- costs, slippage, funding, delisting и collateral учитываются явно;
- selection proof фиксируется до final;
- пороги не ослабляются после просмотра;
- отрицательные результаты не удаляются;
- ноль при нулевой экспозиции не является forward-подтверждением;
- API keys не коммитятся;
- missing/stale/reconciliation failure → fail closed.
