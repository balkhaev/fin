# V501–V508 — V75 account-level risk budget

Цель цикла — проверить, можно ли приблизить historical CAGR к 50% без существенного выхода за исходный V75 drawdown envelope.

## Источники

- daily V75 stress equity, материализованный из pinned ветки V139;
- persistent V285/V365 stabilizer из V477;
- frozen causal V413 market-state stream.

Перед исследованием V75 stream обязан совпасть с V138 по 2 007 датам, annual returns, total return, Sharpe и Max DD. Сам V75 target engine этим циклом не переписывается.

## Chronology

```text
development        2021–2023
validation         2024
holdout            2025
final               2026 H1
```

2024–2026 H1 открываются только после immutable selection proof.

## Проверяемая логика

- постоянное account leverage — только controls;
- causal 63-day volatility targeting;
- continuous V413 state score без дискретных state→trade правил;
- completed-equity drawdown governor;
- 0%, 10% или 15% persistent stabilizer allocation;
- scheduled rebalance, no-trade band и urgent risk reduction;
- financing, transfer costs и дополнительный underlier-cost stress.

## Целевые ограничения

```text
full CAGR target                 50%
development CAGR floor           45%
development Max DD              -25%
full Max DD                     -27%
Sharpe floor                      1.35
maximum account leverage          1.95x
average account leverage          1.65x
```

Это account-level research, а не position-level margin simulation. Даже положительный результат не разрешает реальное плечо или изменение капитала.

```text
integration_permitted      false
capital_change_authorized  false
live_ready                 false
real_leverage_authorized   false
profitability_proven       false
```
