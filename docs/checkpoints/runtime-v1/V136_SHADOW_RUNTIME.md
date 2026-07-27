# V136 execution shadow — runtime-v1

This layer ports only the frozen V136 target-holding state machine. It accepts a
deterministic V75 `StrategySnapshot` and returns a separate
`v136_execution_shadow` snapshot.

Frozen parameters:

```text
L1 no-trade band          0.08
maximum target age        28 days
step fraction             1.00
risk-reduction buffer     0.02
```

The implementation reproduces the research `apply_policy` semantics, including
the strict risk-reduction-buffer boundary and the original perpetual sign test.
A zero-to-nonzero perpetual target therefore counts as a sign change in the
frozen research implementation and is updated immediately.

The V75 primary snapshot is immutable. The shadow has a separate strategy id,
target hash, reasons and age state. Availability, margin feasibility, sign-flip
order splitting, paper fills and reconciliation remain later runtime layers.

Historical V136 regression is intentionally blocked until the exact canonical
V75 target stream and daily fixture are materialized directly in the repository.

```text
live_execution_available = false
live_ready = false
real_leverage_authorized = false
integration_permitted = false
```
