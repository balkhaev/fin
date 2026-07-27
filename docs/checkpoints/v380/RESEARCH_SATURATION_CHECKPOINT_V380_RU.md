# Checkpoint V380 — насыщение исторического поиска кандидатов

Дата фиксации: 27 июля 2026 года.

## Решение

После циклов V179–V380 **новый standalone-кандидат для капитала не найден**. На fixed January-2021 Binance USD-M universe дальнейший перебор соседних cross-sectional параметров закрывается: число проверенных семейств и политик уже достаточно велико, а единственный новый процесс, прошедший development целиком, не выдержал однократно открытый OOS.

```text
new_candidate_found       = false
new_sleeve_allocation     = 0%
integration_permitted     = false
live_ready                = false
real_leverage_authorized  = false
profitability_proven      = false
```

## Новые циклы V341–V380

| Цикл | Семейство | Development evidence | OOS | Решение |
|---|---|---|---|---|
| V341–V348 | volatility term structure | 0/108; top CAGR 28.14%, Sharpe 1.557 | не открыт | near-miss, rejected |
| V349–V356 | slow attention / taker flow | 0/108; top CAGR 18.53%, short leg negative | не открыт | rejected |
| V357–V364 | correlation / beta stability | 0/108; top CAGR 42.14%, directional long effect | не открыт | rejected |
| V365–V372 | exact 50/50 downside-vol ensemble | development passed: CAGR 23.75%, Sharpe 2.079, DD -4.54% | 2024 -10.36%, 2025 +0.36%, 2026 H1 -1.32% | rejected after OOS |
| V373–V380 | residual jump incidence / clustering | 0/108; no policy positive in every development year | не открыт | rejected |

## Главный новый результат

V341 обнаружил сильный raw effect у cross-sectional downside-volatility compression. В отличие от большинства предыдущих factors, два процесса имели положительные long и short legs. Единственный заранее зафиксированный 50/50 ensemble V365 прошёл **все** development gates:

```text
Development CAGR       +23.75%
Development Sharpe       2.079
Development Max DD      -4.54%
Turnover                14.07x/year
Max gross                0.529x
Long P&L             +$8,082.25
Short P&L              +$847.72
```

Но OOS опроверг устойчивость:

```text
Validation 2024  -10.36%
Holdout 2025      +0.36%
Final 2026 H1     -1.32%
Extreme-cost CAGR -0.60%
```

Следовательно, высокий development Sharpe не является достаточным доказательством будущего edge.

## Повторяющаяся структура отрицательных результатов

1. Low-risk, liquidity, path-quality, correlation, attention и jump factors часто выбирают сильную long basket.
2. Short basket обычно теряет деньги и превращает nominally market-neutral construction в скрытую directional/alt-beta ставку.
3. Beta hedge может механически убрать market beta, но не устраняет idiosyncratic drawdown и concentration.
4. Более сильные cross-sectional rows часто нарушают gross, concentration или year-by-year gates.
5. Когда exact process проходит development и открывает OOS, edge ослабевает или исчезает.

## Закрытые области исторического tuning

Без нового независимого источника данных запрещено продолжать соседний mining в следующих семействах:

- downside-volatility compression и любые новые смеси V341/V365;
- low-skew / lottery quality V269–V292;
- systematic coskewness V301–V340;
- low-risk, liquidity-quality, attention and correlation ranking;
- residual reversal, path quality and jump-incidence thresholds;
- funding, basis, calendar spread and cross-venue carry;
- depth, taker-flow, lead-lag and liquidation proxies.

Запрет включает изменение lookback, k, rebalance, gross, hedge cap, costs и периодов после просмотра результатов.

## Что остаётся допустимым

### 1. Независимые executable datasets

Исторический поиск можно возобновить только при появлении нового источника, который закрывает заранее заданные development/validation/holdout/final окна и содержит реальную исполнимость:

- option bid/ask, settlement and contract multipliers;
- complete paid-funding history across both legs;
- complete liquidation/order-event history through 2026;
- queue-aware L2 or actual fill observations.

### 2. Forward-only experiments

Новая гипотеза может быть зафиксирована после 27 июля 2026 года и оцениваться только на будущих observations. Исторические V341/V349/V357 long-side diagnostics не могут быть преобразованы в доказанный long-only candidate задним числом.

### 3. Paper/shadow implementation existing benchmark

V75 остаётся обязательным benchmark, V28 — control, V136 — execution shadow. Это не разрешает live execution или реальное плечо.

## Капитальное решение

```text
V341–V380 allocation       = 0%
V365 watch status          = rejected OOS control only
V285 watch status          = rejected OOS control only
V75 benchmark status       = unchanged, paper/shadow only
```

Отсутствие нового sleeve не является аргументом для увеличения gross или leverage текущего benchmark.
