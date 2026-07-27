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

It does **not** port the V75 or V28 target engine and does not create a broker
submission path. `live_execution_available`, `live_ready` and
`real_leverage_authorized` remain false.
