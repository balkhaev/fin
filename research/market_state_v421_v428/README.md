# V421–V428 — Market State Explainability and Memory

Этот слой использует неизменяемые результаты V413–V420 и отвечает на вопросы, которые обычный backtest не решает:

- какие оси и raw-features формируют текущий режим;
- насколько распределение режимов ушло от 2021–2023;
- изменились ли сами переходы между режимами;
- какие периоды были действительно новыми относительно development;
- как вероятность выхода из режима зависит от его длительности;
- какие следующие состояния наиболее вероятны по frozen transition graph.

## Что не меняется

```text
V413 feature weights      frozen
V413 robust scalers       frozen
V413 centroids            frozen
V413 transition source    frozen
V75/V136 parameters       frozen
capital allocation        unchanged
```

## Outputs

- `daily_state_driver_attribution.csv` — dominant axes, raw features и centroid mismatch;
- `occupancy_drift.csv` — Jensen–Shannon divergence и PSI;
- `transition_drift.csv` — drift переходов и self-persistence;
- `axis_drift.csv` — mean/median shifts;
- `novelty_episodes.csv` — последовательные novelty intervals;
- `state_duration_hazard.csv` — эмпирическая вероятность выхода по duration buckets;
- `SCENARIO_GRAPH.json` — frozen transition scenarios;
- `CURRENT_MARKET_CONTEXT.json` и `.md` — последний архивный контекст;
- `DRIFT_MONITOR_QUALITY.json` — integrity checks.

## Интерпретация

Drift не является автоматически bearish или bullish сигналом. Он показывает, что рынок посещает состояния с другой частотой либо переходит между ними иначе, чем в development. Это полезно для:

- attribution V75/V136;
- объяснения slippage и turnover;
- выявления structural breaks;
- post-mortem execution incidents;
- контроля того, что paper-forward evidence относится к знакомым или новым режимам.

## Safety

```text
state/drift -> trade                prohibited
historical retuning                prohibited
capital changes from diagnostics   prohibited
integration_permitted              false
live_ready                         false
real_leverage_authorized           false
```
