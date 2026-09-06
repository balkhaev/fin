# Аудит и бэктесты `balkhaev/fin`

Дата аудита: **1 августа 2026**  
Базовый commit `main`: `cb942798acdd0f27867b923476dc9b50eb67984f`  
Изолированная ветка: `agent/backtest-audit-20260801`  
Draft PR: **#109**  
Основные входные snapshots:

- DYN-IV113: `25869b442e44a821319b70db2118a6d0834a4bdc20eb2c9211ae852c74049ea8`
- DS-40/180: `sha256:41a9efc9dbf9e94a95407063370f63a914907606a1a004e5322b31838e7f9715`
- Atlas V517/V75: checksum проверяется самим frozen replay.

## Итог

1. **DYN-IV113 — лучший текущий кандидат**, но доходность сильно зависит от отдельных активов и высокой волатильности. Базовый свежий двухлетний replay дал CAGR **151,0%**, Sharpe **1,70**, max drawdown **−31,1%**. Более разумный профиль риска получен при target-vol 50%: CAGR **106,8%**, Sharpe **1,73**, max drawdown **−21,8%**.
2. **DS-40/180 положителен, но доказательность пока слабее**: доступно только 395 наблюдений после честного burn-in. База: CAGR **46,0%**, Sharpe **1,04**, max drawdown **−33,3%**. Главная оптимизация — order-level deadband, а не изменение сигнала.
3. **Atlas V517/V75 — полезный historical reference, но не Atlas NX R1.** Его нельзя возвращать из API под `strategy_id=atlas-nx` без явной маркировки. Текущий guard на frozen history вообще не срабатывал.
4. **Funding Neutral и Consensus WIF+DOT сейчас не воспроизводятся из GitHub Actions** из-за data-ingestion: Bybit отвечает 403, а месячный архив WIF за июль 2026 ещё отсутствовал. Это не отрицательный результат стратегии; это блокер доказательства.
5. **Ничего из найденного нельзя автоматически считать новой production-конфигурацией.** Все параметры ablation выбраны после просмотра того же периода; им нужен предзарегистрированный shadow/forward этап.

## 1. DYN-IV113

Окно: **2024-07-31 — 2026-07-31**, 731 дневное наблюдение, 16 usable assets. `BTTUSDT` не вернул закрытых дневных свечей.

| Вариант | CAGR | Sharpe | Max DD | Vol | Исполнения |
|---|---:|---:|---:|---:|---:|
| Baseline: target vol 70%, gross 2.5 | 151,0% | 1,70 | −31,1% | 66,9% | 1 653 |
| Target vol 50% | 106,8% | **1,73** | **−21,8%** | 48,6% | 1 832 |
| Target-band 2% | **153,1%** | 1,71 | −30,4% | 67,1% | **414** |
| Target-band 5% | 147,4% | 1,67 | −29,1% | 67,8% | 304 |
| Execution cost 50 bps | 133,8% | 1,60 | −32,3% | 66,9% | 1 653 |
| Execution cost 100 bps | 95,7% | 1,33 | −35,1% | 66,8% | 1 652 |
| Financing 40% annual | 139,0% | 1,63 | −31,2% | 66,9% | 1 653 |
| Conservative combo | 82,5% | 1,57 | −22,7% | 44,4% | 1 297 |
| Extra execution delay 1 day | 143,0% | 1,65 | −33,6% | 67,2% | 1 656 |

### Что работает

- **Target-vol 50%** улучшает risk-adjusted профиль: Sharpe немного выше, а просадка меньше примерно на 9,3 п.п. Это лучший кандидат для отдельного low-risk paper profile.
- **Target-band 2%** уменьшает число изменений целевых весов примерно на 75% и не ухудшает метрики на этом окне. Это лучший кандидат для снижения оборота.
- Стратегия остаётся положительной при 50–100 bps execution cost и 40% annual financing stress. Это хороший, но не полный stress-test: market impact, borrow availability и проскальзывание по размеру не моделируются.
- Дополнительная задержка исполнения на день ухудшила результат умеренно, а не разрушила его. Это поддерживает причинность, но не заменяет отдельный event-time audit.

### Что хрупко

Leave-one-asset-out показал сильную зависимость результата от нескольких монет:

| Удалённый актив | CAGR | Sharpe | Max DD |
|---|---:|---:|---:|
| TRX | **58,2%** | 1,01 | −40,2% |
| XRP | **82,9%** | 1,25 | −44,5% |
| ADA | 106,6% | 1,42 | −29,5% |
| BNB | 114,0% | 1,46 | −43,5% |
| LINK | 122,1% | 1,52 | −31,0% |

Удаление XLM, наоборот, улучшило метрики; удаление XMR ничего не изменило. Leave-one-out не является точной attribution, потому что меняется нормировка всего портфеля, но он явно показывает **universe fragility** и необходимость point-in-time universe/listing/delisting тестов.

### Решение по DYN

- Не менять baseline молча.
- Запустить два предзарегистрированных paper sleeves:
  - `dyn-iv113-risk50`: target vol 50%;
  - `dyn-iv113-band2`: текущий риск, target deadband 2%.
- Комбинацию risk50 + band2 сначала протестировать отдельно; она в этом аудите не измерялась.
- До live-capital: point-in-time universe, walk-forward по нескольким неперекрывающимся окнам, exchange filters/min-notional, liquidity-aware cost model.

## 2. DS-40/180 T50-C3

Доступное честное окно после 365-дневного burn-in: **2025-07-02 — 2026-07-31**, 395 наблюдений. Текущий `HISTORY_LIMIT=760` не позволяет честно заявить двухлетний forward-like replay.

Baseline:

- CAGR **46,0%**
- Sharpe **1,04**
- max drawdown **−33,3%**
- execution costs около **$648** на стартовый NAV $10 000
- funding PnL около **−$329**
- **4 572 исполнения**, или 11,6 исполнения на наблюдение
- 3 518 actual funding intervals и **3 282 fallback intervals**

### Почему target-level band почти не помог

Даже когда целевой вектор удерживается, ledger каждый день решает fixed point и возвращает quantity к точному target-weight. Поэтому target-band 0,25–2% оставил почти все исполнения. Недельный/двухнедельный target hold также не решает основной источник churn.

### Order-level deadband audit

| Минимальное отклонение веса для ордера | CAGR | Sharpe | Max DD | Исполнения | Costs |
|---|---:|---:|---:|---:|---:|
| 0,00% baseline | 46,0% | 1,04 | −33,3% | 4 572 | $648 |
| 0,10% | 46,3% | 1,05 | −33,2% | 3 523 | $642 |
| 0,25% | 46,0% | 1,04 | −33,5% | 2 670 | $626 |
| 0,50% | 44,9% | 1,03 | −33,7% | 1 973 | $601 |
| 1,00% | 46,7% | 1,06 | −33,4% | 1 308 | $557 |
| 2,00% | 51,8% | 1,13 | −32,4% | 724 | $480 |
| 5,00% | **55,3%** | **1,22** | **−30,2%** | **303** | **$346** |

Это сильный сигнал, что exact-weight rebalancing неэффективен. Однако 5% — постфактум лучший результат на единственном коротком окне, поэтому выбирать его сразу нельзя.

### Решение по DS

- Реализовать production-grade order gate с:
  - weight/notional deadband;
  - minimum notional и exchange step size;
  - обязательным закрытием/очисткой dust;
  - отдельной обработкой sign flip и target zero;
  - журналом skipped orders.
- Предзарегистрировать **2% и 5%** как два shadow-кандидата, не подбирать дальше по тому же окну.
- Увеличить immutable history минимум до 3–4 лет и повторить walk-forward.
- Сократить долю funding fallback; сейчас почти половина funding observations не actual.

## 3. Atlas

Текущий on-demand route `atlas-nx` фактически запускает account-level **V517/V524/V75 tri-state guard**, а не текущий Atlas NX R1.

Frozen reference:

- 2021-01-01 — 2026-06-30: CAGR **50,5%**, Sharpe **1,46**, max drawdown **−23,7%**.
- Последние два года 2024-07-01 — 2026-06-30: CAGR **43,3%**, Sharpe **1,20**, max drawdown **−23,4%**.

Ablation последнего двухлетнего окна:

- all-states 1x: CAGR 26,8%, Sharpe 1,18, DD −15,8%; большая часть excess return связана с leverage, а не резким ростом Sharpe;
- rebalance 5d: CAGR 43,9%, Sharpe 1,19 — практически не лучше 10d;
- rebalance 20d: CAGR 34,7%, Sharpe 1,03 — явно хуже;
- no-trade band 0%: CAGR 44,5%, но DD ухудшился до −25,1%; текущий band выглядит разумно;
- guard disabled и guard cap 0,75 дали **точно тот же результат**, что baseline: текущий guard threshold на этой истории не был достигнут;
- более ранний guard немного снизил DD, но также снизил CAGR и Sharpe.

### Решение по Atlas

- Разделить IDs и UI labels: `atlas-v517-reference` и `atlas-nx-r1`.
- V517 оставить frozen historical reference, не «оптимизировать» на той же истории.
- Не считать guard валидированным, пока нет сценария/истории, где он реально включался.

## 4. Factor backtests

### Funding Neutral

Replay не стартовал: Bybit public API вернул HTTP 403 из GitHub Actions. Стратегия использует Binance/Bybit funding и mark-price series; без Bybit нельзя сделать честный вывод о доходности.

### Consensus WIF + DOT

Replay не стартовал, потому что код запросил месячный архив `WIFUSDT-15m-2026-07.zip` 1 августа 2026, когда файл ещё не был опубликован. Для предыдущего месяца нужен grace period и daily/API fallback.

### Почему CI это не поймал

Текущие unit tests подменяют factor runner fake-функцией и проверяют shape результата, а не реальное archive/API поведение. Поэтому green CI не означает воспроизводимость factor replay.

### Исправления

- archive resolver: monthly → daily/API fallback на 404, особенно для последних 1–2 месяцев;
- raw-data cache/content-addressed snapshots;
- Bybit fallback endpoint или заранее закреплённый официальный snapshot;
- integration smoke с небольшим реальным окном, допускающий controlled skip только при доказанном external outage;
- отдельный статус `data_unavailable`, а не смешивание с `strategy_failed`.

## 5. Ошибки и архитектурные улучшения

### P0 — корректность и доказательность

1. **Strategy identity:** не отдавать V517 под ID Atlas NX R1.
2. **Duration bug:** `duration_seconds` считается от переданного audit `now`, а не от фактического wall-clock старта; в отчётах появились значения около 9 часов для минутного job.
3. **Data availability:** исправить last-month archive fallback и Bybit portability.
4. **DS history:** `HISTORY_LIMIT=760` недостаточен для заявленного двухлетнего теста после warm-up.
5. **Integration tests:** добавить реальные/fixture-backed ingestion tests; fake runner недостаточен.

### P1 — performance/cost

1. DYN: shadow target-vol 50% и target-band 2%.
2. DS: order-level deadband 2%/5%, min-notional и step-size aware execution.
3. Не использовать общий `CAGR >= 50%` как универсальный pass/fail: он стимулирует leverage/selection chasing. Нужны preregistered thresholds по Sharpe, DD, turnover, data quality и stability.

### P2 — robustness

1. Point-in-time universe и delisting/listing history.
2. Walk-forward с несколькими неперекрывающимися test windows и embargo.
3. Asset contribution/leave-one-out monitoring.
4. Отдельные метрики actual/fallback funding, data completeness, request failures и stale inputs.
5. Liquidity-aware costs и capacity stress, а не только фиксированные bps.

## Статус изменений

В `main` ничего не изменено. Draft PR #109 содержит только research harness/workflows и этот отчёт. Он не меняет стратегию, paper accounts, deployment entrypoints или live permissions и не должен сливаться как production change.
