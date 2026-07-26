# Acceptance criteria — runtime-v1

## A. Merge criteria для implementation PR

### Provenance

- [ ] exact source paths и SHA-256 frozen research code записаны;
- [ ] exact expected regression outputs записаны;
- [ ] source registry валидируется CI;
- [ ] никакой зависимости от старых Actions artifacts;
- [ ] canonical paths не содержат ZIP/TAR/base64 fragments.

### Data contracts

- [ ] UTC-aware timestamps;
- [ ] `available_at_utc` обязателен;
- [ ] future availability rejected;
- [ ] duplicate/conflicting observation rejected;
- [ ] stale on-chain forced-flat;
- [ ] source payload hashes deterministic.

### V75

- [ ] target weights совпадают с canonical research replay;
- [ ] high-water monotonic;
- [ ] risk stages irreversible;
- [ ] gross/cash/margin caps выполняются;
- [ ] future-row mutation не меняет прошлые targets;
- [ ] daily target hash deterministic.

### V28

- [ ] отдельный state;
- [ ] отдельный journal;
- [ ] отдельный plan id;
- [ ] regression matches frozen control;
- [ ] одновременный запуск с V75 не объединяет cash/positions.

### V136

- [ ] band `0.08`;
- [ ] max age `28`;
- [ ] immediate risk reduction;
- [ ] immediate zero exit;
- [ ] sign flip split;
- [ ] shadow plan не изменяет V75 plan;
- [ ] tracking error записывается.

### Planner

- [ ] deterministic;
- [ ] idempotent;
- [ ] risk reductions first;
- [ ] missing critical price blocks risk increase;
- [ ] no `submit_order` path;
- [ ] `mode=live` rejected.

### Accounting/journal

- [ ] append-only event log;
- [ ] atomic state commit;
- [ ] crash recovery test;
- [ ] duplicate event idempotent;
- [ ] accounting identity;
- [ ] corrupted state fails closed.

### Paper broker

- [ ] spread/commission/slippage separate;
- [ ] partial/rejected/expired fills;
- [ ] funding events;
- [ ] outage scenario;
- [ ] fills do not mutate original plan.

### CI

- [ ] Python 3.11;
- [ ] Python 3.12;
- [ ] Python 3.13;
- [ ] unit tests;
- [ ] regression tests;
- [ ] causal tests;
- [ ] replay tests;
- [ ] safety tests;
- [ ] schemas/examples validation.

## B. Runtime-v1 checkpoint criteria

- [ ] all implementation PRs merged;
- [ ] immutable checkpoint branch created;
- [ ] `SOURCE_REGISTRY.json` complete;
- [ ] `STRATEGY_REGISTRY.json` complete;
- [ ] one-command clean-clone verification;
- [ ] runbook tested by a second process/person;
- [ ] no secret material committed;
- [ ] live adapter absent;
- [ ] `live_ready=false`;
- [ ] `real_leverage_authorized=false`.

## C. Paper-forward evidence criteria

### V75/V28/V136

Minimum observation:

```text
180 calendar days
30 significant V75 target changes
>= 1 non-zero V67 accelerator regime
>= 1 defensive stage change or explicit absence report
```

Operational gates:

- [ ] no unexplained position mismatch;
- [ ] no state corruption;
- [ ] no duplicate orders/plans;
- [ ] tracking error V75 model→paper <= 2% equity;
- [ ] V136 actual turnover reduction reported;
- [ ] actual costs compared with research stress assumptions;
- [ ] every stale-data halt recorded;
- [ ] every manual intervention recorded and disqualifies affected interval from clean forward.

### Funding router

Separate evidence:

```text
>= 30 days continuous paper operation
>= 30 fully closed positions
positive realized paper P&L after all modelled costs
zero unexplained delta mismatch
zero unrecovered SQLite/state failures
```

Funding-router paper P&L не засчитывается в V75 forward.

## D. Future live review criteria

Эти критерии не разрешают live автоматически; они только позволяют открыть отдельный review:

- [ ] runtime-v1 paper criteria passed;
- [ ] authenticated read-only exchange smoke tests;
- [ ] smallest-size sandbox/testnet execution tests;
- [ ] independent code review;
- [ ] jurisdiction/counterparty review;
- [ ] explicit capital-at-risk limit;
- [ ] kill switch tested;
- [ ] withdrawal permissions absent;
- [ ] IP allowlist;
- [ ] separate subaccounts;
- [ ] manual owner approval in a new checkpoint.

До отдельного checkpoint:

```text
live_execution_available = false
real_leverage_authorized = false
```

## E. Automatic rejection conditions

Implementation/runtime считается неприемлемым при любом из условий:

- historical target regression mismatch;
- silent forward-fill;
- naive datetime;
- non-deterministic plan hash;
- high-water decrease;
- risk increase on stale critical data;
- sign flip without reduce-only close phase;
- missing event journal;
- corrupted state resets to empty;
- live mode available;
- secret/API key committed;
- combined V75 + funding-router P&L without separate accounting;
- changed strategy parameter without new strategy id and reset forward clock.
