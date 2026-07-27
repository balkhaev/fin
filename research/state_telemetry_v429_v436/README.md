# V429–V436 — State-Conditioned Paper Telemetry

Этот слой соединяет frozen market-state observatory V413 с будущим paper runtime V75/V136/V28.

Он не отвечает на вопрос «что купить». Он отвечает на вопросы:

- в каких состояниях стратегия получает и теряет P&L;
- где возникает turnover и slippage;
- какие переходы предшествуют просадке или reconciliation incident;
- полезен ли V136 execution plateau именно там, где V75 исполняется дорого;
- накоплено ли forward evidence в знакомых или novel состояниях.

## Required strategies

```text
V75_ATLAS_NX
V136_EXECUTION_PLATEAU
V28_GROWTH_CONTROL
```

## Outputs

- joined telemetry and frozen state rows;
- metrics by strategy/state;
- metrics by state transition;
- metrics by novelty flag;
- V136 versus V75 forward comparison;
- fail-closed data-quality result;
- frozen acceptance decision.

## Forward gates

```text
start not before                  2026-07-28
minimum duration                  180 calendar days
minimum V136 target changes       25
reconciliation breaks             0
source hash match                 100%
V136 turnover reduction           >=10%
V136 net paper return delta       >=0
V136 DD worsening                 <=2 percentage points
paper/model slippage ratio        <=1.5x
missing or stale data             fail closed
```

## Safety

State-conditioned diagnostics cannot be used to rewrite historical parameters. Until every forward gate passes, V75 remains primary paper/shadow, V136 remains execution shadow, and real capital/leverage remain unauthorized.
