# V413–V420 — Market State Observatory

Цель этого цикла — перестать трактовать рынок как набор отдельных сигналов и построить причинный слой **общего состояния рынка**.

Observatory не является стратегией и не имеет права менять V75, V136, V28 или распределение капитала. Он отвечает на другие вопросы:

- рынок находится в спокойном тренде, ротации, спекулятивном расширении или deleveraging;
- насколько текущее состояние похоже на исторические состояния development-периода;
- насколько неожиданен переход между состояниями;
- как долго длится текущий режим;
- в каких состояниях V75 и V136 в будущем получают turnover, slippage, drawdown или reconciliation stress.

## Механизм

Используются 14 завершённых daily-признаков из фиксированного January-2021 Binance USD-M universe:

- trend и breadth;
- market volatility и downside-volatility term structure;
- cross-sectional correlation и dispersion;
- quote-liquidity и taker-buy pressure;
- paid funding level и dispersion;
- drawdown breadth и downside-jump breadth.

Признаки агрегируются в шесть объяснимых осей:

```text
trend
breadth
stress
rotation
liquidity
leverage
```

Robust scalers и шестисостоянийный codebook обучаются только на 2021–2023. Данные 2024, 2025 и 2026 H1 используются только для диагностики устойчивости, occupancy, novelty и transitions.

Каждая дневная запись содержит:

- state id и интерпретируемый label;
- шесть осей состояния;
- assignment confidence;
- novelty ratio;
- transition surprise;
- duration текущего состояния.

## Хронология

```text
warmup                  2020
state fit               2021–2023
validation diagnostics  2024
holdout diagnostics     2025
final diagnostics       2026 H1
```

Строка состояния с датой `d` использует информацию, завершённую не позднее предыдущего daily close, и описывает информацию, доступную перед UTC open даты `d`.

## Разрешённое применение

Observatory может использоваться для forward attribution V75/V136/V28:

- return и drawdown по состояниям;
- turnover и slippage по состояниям;
- stale-data и reconciliation failures;
- переходы, предшествующие execution stress;
- автоматизированное объяснение рыночного контекста.

## Запрещённое применение

```text
state label -> trade                         prohibited
historical state-conditioned retuning       prohibited
capital changes from OOS state diagnostics  prohibited
refitting centroids on 2024–2026            prohibited
```

## Запуск

```bash
python -m pip install -r research/market_state_v413_v420/requirements.txt
python research/market_state_v413_v420/run_observatory.py --self-test
python research/market_state_v413_v420/run_observatory.py \
  --root research/market_state_v413_v420 \
  --cache .cache/v9
```

## Safety

```text
historical_parameter_search_closed = true
strategy_parameter_changes_permitted = false
allocation_changes_permitted = false
integration_permitted = false
live_ready = false
real_leverage_authorized = false
profitability_proven = false
```
