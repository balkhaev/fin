# V445–V452 — forward market-mechanism validator

Status: `forward_mechanism_validator_ready_no_observations`.

Наблюдений после frozen start пока нет. Валидатор не является стратегией и не разрешает state gating.

## Primary forward mechanisms

1. `persistent_regime_non_degradation`: V136 должен уменьшить turnover минимум на 10% относительно V75 в режимах длительностью более пяти дней, не ухудшая net return и drawdown.
2. `switching_regime_execution_benefit`: то же требование применяется к high-switching context, определённому только по development state history.

## Diagnostic contexts

`early_state`, `novel`, `high_transition_surprise` публикуются независимо от результата и не могут быть удалены после наблюдения.

```text
paper_observation_count                0
market_mechanism_claim_supported       false
capital_change_authorized              false
strategy_parameter_change_authorized   false
live_ready                             false
real_leverage_authorized               false
```
