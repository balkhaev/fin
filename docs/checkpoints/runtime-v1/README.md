# Runtime-v1 M0–M1 checkpoint

This checkpoint implements the first direct, reviewable runtime layer from the
frozen implementation plan:

- exact source/provenance registry;
- canonical JSON and SHA-256 runtime identities;
- MarketSnapshot, StrategySnapshot, PortfolioState, ExecutionPlan, FillEvent
  and ReconciliationReport contracts;
- source availability and stale on-chain fail-closed rules;
- append-only hash-chain journal foundation;
- frozen strategy registry with no live mode.

The exact V75 and V28 target engines remain unmaterialized. Atlas NX R1 is a
separately registered reconstruction with a reset forward clock; see
`ATLAS_NX_R1_RECONSTRUCTION_RU.md` and `ATLAS_NX_R1_MIGRATION.json`. It does not
create a broker submission path. `live_execution_available`, `live_ready` and
`real_leverage_authorized` remain false.

## V517 shadow risk-budget adapter

`finruntime.profiles.v517_guard` adds a deterministic shadow-only adapter for the
non-pristine V517/V524 tri-state risk budget. It consumes a sealed V75 snapshot
and completed V75 equity history, applies an explicit outer leverage cap, and
remains subject to the existing fail-closed planner. See
`V517_RISK_BUDGET_RUNTIME_RU.md`. No exchange submission or live authorization
is added.
