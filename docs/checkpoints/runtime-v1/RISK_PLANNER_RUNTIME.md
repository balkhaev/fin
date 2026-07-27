# Runtime-v1 — pre-trade risk and deterministic planner

Этот checkpoint добавляет M5–M6 слой поверх frozen strategy snapshots. Он не реализует V75 alpha и не меняет V136 policy.

## Risk layer

Входы:

```text
StrategySnapshot targets     equity fractions
PortfolioState positions     base quantities
MarketSnapshot               immutable source bundle
Reference price book         positive decimal prices
```

Fail-closed правила:

- critical source missing/stale/future/invalid запрещает любое instrument-level увеличение риска;
- same-sign уменьшение и закрытие остаются разрешёнными;
- sign flip при blocked risk increase превращается в close-only target;
- target gross масштабируется к меньшему из strategy gross cap и hard cap;
- spot cash use + perpetual initial margin + operational reserve не могут превышать equity;
- отсутствующая цена существующей позиции блокирует risk calculation.

Default operational constraints:

```text
hard gross cap          1.10x
initial margin ratio    25%
operational reserve     20%
```

StrategySnapshot может задавать более низкий `gross_cap`, но не может повысить hard cap.

## Planner

Planner переводит constrained equity weights в signed base quantities по deterministic reference prices.

Порядок intents:

```text
1. spot/perpetual reductions and closes
2. risk-increasing opens/additions
```

Perpetual sign flip всегда делится:

```text
reduce-only close current position
separate non-reduce-only open with parent_intent_id
```

Одна и та же комбинация:

```text
market snapshot
strategy snapshot
portfolio state
risk decision
planner policy
```

даёт одинаковые intent ids и plan hash. Если уже существует другой pending plan, planning завершается `PlanningHalt`.

## Missing prices

Planner не оценивает цену по памяти и не создаёт intent без reference price. Отсутствующая цена вызывает fail-closed planning halt; она не заменяется последней известной ценой.

## Scope boundary

Не реализованы:

- exchange submission;
- paper fills;
- partial-fill state transitions;
- funding/accounting journal;
- V75/V28 target engines;
- historical V136 target regression.

```text
live_execution_available = false
live_ready = false
real_leverage_authorized = false
profitability_proven = false
```
